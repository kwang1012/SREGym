import os

import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command
import numpy as np
import re
from sentence_transformers import SentenceTransformer

from clients.stratus.stratus_utils.str_to_tool import str_to_tool

from langchain_cerebras import ChatCerebras


def build_tools(file_path: str):
    diagnosis_agent_tools = yaml.safe_load(open(file_path))
    sync_tools = []
    async_tools = []
    tool_descriptions = ""
    if diagnosis_agent_tools["sync_tools"] is not None:
        for sync_tool_struct in diagnosis_agent_tools["sync_tools"]:
            sync_tools.append(str_to_tool(sync_tool_struct))
            tool_descriptions += (
                f"tool name: {sync_tool_struct['name']}"
                + "\n\n"
                + f"tool descriptions {sync_tool_struct['description']}"
                + "\n\n"
            )
    if diagnosis_agent_tools["async_tools"] is not None:
        for async_tool_struct in diagnosis_agent_tools["async_tools"]:
            async_tools.append(str_to_tool(async_tool_struct))
            tool_descriptions += (
                f"tool name: {async_tool_struct['name']}"
                + "\n\n"
                + f"tool description: {async_tool_struct['description']}"
                + "\n\n"
            )
    return sync_tools, async_tools, tool_descriptions


def llm_inference(model, messages, tools: list | None = None, **kwargs):

    llm = ChatCerebras(
        model=model,
    )
    # llm = ChatOpenAI(
    #     base_url="http://localhost:8000/v1",
    #     # api_key="not-needed",
    #     model="meta-llama/Llama-3.3-70B-Instruct",
    #     **kwargs,
    # )
    if tools:
        llm = llm.bind_tools(tools)

    response = llm.invoke(input=messages)

    return response


def semantic_similarity(a: str, b: str) -> float:
    """
    Compute semantic similarity between text strings a and b using embeddings.
    'embed_func' should return a normalized embedding vector.
    """
    emb_a = embed_text(a)
    emb_b = embed_text(b)
    return float(np.dot(emb_a, emb_b))


embed_model = None


def embed_text(text: str):
    global embed_model
    if embed_model is None:
        embed_model = SentenceTransformer('all-MiniLM-L6-v2')
    return embed_model.encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True
    )


class SREAgent:
    def __init__(self, logs_dir, model_name):
        self.log_dir = logs_dir
        self.model_name = model_name

        self.sync_tools, self.async_tools, self.tool_descs = build_tools(
            "./clients/sre/agent_tools.yaml")

        self.sync_tools_by_name = {t.name: t for t in self.sync_tools}
        self.async_tools_by_name = {t.name: t for t in self.async_tools}

    async def _handle_tool_calls(self, message: AIMessage):
        if not isinstance(message, AIMessage):
            raise ValueError(
                "Last message is not an AIMessage; skipping tool invocation.")

        new_messages = []
        for tool_call in message.tool_calls:
            if tool_call["name"] == "submit_tool":
                print("[AGENT] Submitting tool, ending process.")
                return None

            arg_list = [f"{key} = {value}" for key,
                        value in tool_call["args"].items()]
            tools_str = f"\n- {tool_call['name']}({', '.join(arg_list)})"
            print(f"[AGENT] AI Tool Calls: {tools_str}")
            try:
                if tool_call["name"] in self.sync_tools_by_name:
                    tool_result = self.sync_tools_by_name[tool_call["name"]].invoke(
                        {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "args": {"state": {}, **tool_call["args"]},
                            "id": tool_call["id"],
                        }
                    )
                elif tool_call["name"] in self.async_tools_by_name:
                    tool_result = await self.async_tools_by_name[tool_call["name"]].ainvoke(
                        {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "args": {"state": {}, **tool_call["args"]},
                            "id": tool_call["id"],
                        }
                    )
                else:
                    tool_result = Command(
                        update={
                            "messages": [
                                ToolMessage(
                                    content=f"Tool {tool_call['name']} does not exist!",
                                    tool_call_id=tool_call["id"],
                                )
                            ]
                        }
                    )

                assert isinstance(
                    tool_result, Command
                ), f"Tool {tool_call['name']} should return a Command object, but return {type(tool_result)}"
                if not tool_result.update:
                    continue
                new_messages += tool_result.update["messages"]
            except Exception as e:
                print(f"[AGENT] Error calling tool {tool_call['name']}: {e}")
                new_messages += [
                    ToolMessage(
                        content=f"Error: {e}; This happens usually because you are "
                        f"passing inappropriate arguments to the tool.",
                        tool_call_id=tool_call["id"],
                    )
                ]

        return new_messages

    def _planning_step(self, messages):
        content = (
            "You are now in the planning stage."
            "You should reflect and come up with the sub tasks that you need to complete this task."
            "Respond ONLY with numbered points."
        )
        human_prompt = HumanMessage(content=content)

        resp = llm_inference(model=self.model_name,
                             messages=messages + [human_prompt])

        sub_tasks = [
            line.strip().lstrip("0123456789. ")
            for line in resp.content.splitlines()
            if line.strip()
        ]

        return sub_tasks

    def _thinking_step(self, messages):
        content = "You are now in the thinking stage. Choose a tool from the available tools and justify your choice."
        # content = (
        #     "You are now in the thinking stage."
        #     "You should reflect and come up with the sufficient knowledge points as sub tasks that you need to complete this task."
        #     "For each sub task, you can take multiple actions to complete it, and each action corresponds to a tool call."
        #     "Justify the confidence of your tool choice based on how much the tool can help you complete the sub task."
        #     "Usually, the later tools should have a lower confidence because you won't have enough information before executing the first few tools. But if you are very sure about the tool choice, you can also give a high confidence for later tools."
        # )
        return llm_inference(model=self.model_name, messages=messages + [HumanMessage(content=content)])

    def _action_step(self, messages):
        human_prompt = HumanMessage(
            content="Now generate a tool call according to your last chosen tool.")
        return llm_inference(model=self.model_name, messages=messages + [human_prompt], tools=self.sync_tools + self.async_tools)

    def _analysis_uncertainty(self, original_messages, message):
        similarities = []
        for i in range(5):
            # Sample another answer (temperature sampling or prompt variants)
            candidate_answer = llm_inference(
                self.model_name, original_messages, temperature=1).content

            # Compute semantic similarity to the original answer
            sim = semantic_similarity(
                message, candidate_answer)
            similarities.append(sim)

        # Return average similarity as the observed consistency score
        obs_consistency = float(np.mean(similarities))

#         score_map = {"A": 1.0, "B": 0.0, "C": 0.5}
#         scores = []
        
# #         TEMPLATE_SELF_REFLECTION = """Question: {question}, Proposed Answer: {answer_proposed}. Is the proposed answer: (A) Correct (B) Incorrect
# # (C) I am not sure. The output should strictly use the
# # following template: explanation: [insert analysis], answer:
# # [choose one letter from among choices A through C]
# # """

#         for _ in range(2):
#             # The followup_prompt instructs the model to judge whether its
#             # original answer is correct
#             text = llm_inference(
#                 self.model_name, [HumanMessage(content=TEMPLATE_SELF_REFLECTION.format(question=q, answer_proposed=original_answer))]).content.strip()

#             match = re.search(r"answer\s*:\s*([abc])", text)
#             if match:
#                 ans = match.group(1).upper()
#             else:
#                 ans = "C"  # Default to "I am not sure" if parsing fails
#             print(f"{ans=}, {text=}")
#             # Convert the model judgment into a numeric score
#             scores.append(score_map.get(ans, 0.5))

#         self_reflect_score = float(np.mean(scores))

#         beta = 0.7
#         confidence_score = beta * obs_consistency + \
#             (1.0 - beta) * self_reflect_score
        return obs_consistency

    def _analysis_divergence(self, original_messages, message):
        # Placeholder for divergence analysis logic
        return 0.5  # Example return value

    def _analysis_commitment(self, original_messages, message):
        # Placeholder for commitment analysis logic
        return 0.5  # Example return value

    def _analysis_criticality(self, original_messages, message):
        # Placeholder for criticality analysis logic
        uncertainty = self._analysis_uncertainty(original_messages, message)
        return uncertainty
        # divergence = self._analysis_divergence(original_messages, message)
        # commitment = self._analysis_commitment(original_messages, message)

        # return 0.5 * uncertainty + 0.3 * divergence + 0.2 * commitment

    def _generate_alternatives(self, message):
        # Placeholder for alternative generation logic
        return ["Alternative 1", "Alternative 2"]  # Example return value

    async def arun(self, messages):
        print(f"Running SRE agent with model {self.model_name}")

        messages = messages + \
            [HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)]
            
        sub_tasks = self._planning_step(messages)
        print("Sub Tasks:")
        for i, task in enumerate(sub_tasks):
            print(f"{i+1}. {task}")
        
        for task in sub_tasks:
            print(f"Working on sub task: {task}")
            messages.append(HumanMessage(content=f"Current sub task: {task}, Please choose a tool from the available tools and justify your choice."))
            ai_message = llm_inference(model=self.model_name, messages=messages)
            messages.append(ai_message)
            ai_message = self._action_step(messages)
            tool_results = await self._handle_tool_calls(ai_message)

            messages.extend(tool_results)
            
        return 0
        step = 0
        while step < 5:
            ai_message = self._thinking_step(messages)
            print(f"[Thought]: {ai_message.content}")
            criticality = self._analysis_criticality(messages, ai_message.content)

            print(f"Criticality score: {criticality}")
            # if criticality > 0.5:
            #     print("Critical issue detected. Catching human attention...")
            #     # Recommend alternative
            #     alternatives = self._generate_alternatives(ai_message)
            #     print("Recommended alternatives:")
            #     for alt in alternatives:
            #         print(f"- {alt}")

            #     # Wait for human input before proceeding
            #     input("Press Enter to continue...")

            messages.append(ai_message)
            ai_message = self._action_step(messages)
            tool_results = await self._handle_tool_calls(ai_message)

            messages.extend(tool_results)
            step += 1

        return 0

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
