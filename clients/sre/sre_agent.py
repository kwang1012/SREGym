from clients.sre.base_agent import BaseAgent, llm_inference
import json

import numpy as np
from sentence_transformers import SentenceTransformer

from langchain_core.messages import HumanMessage

from transformers import logging
logging.set_verbosity_error()


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
        convert_to_numpy=True,
        show_progress_bar=False
    )


class SREAgent(BaseAgent):

    def _planning_step(self, messages):
        content = """You are in the planning stage.

Break down the task into a trajectory of diagnostic steps.

Each step must:
1. State the hypothesis being tested
2. Specify what evidence is required
3. Indicate what decision will be made based on that evidence

Respond ONLY in valid JSON with this format:

{
  "trajectory": [
    {
      "step_id": 1,
      "description": "...",
      "hypothesis": "...",
      "evidence_needed": "...",
      "decision_rule": "..."
    }
  ]
}
        """
        # content = (
        #     "You are now in the planning stage."
        #     "You should reflect and come up with the sub tasks that you need to complete this task."
        #     "Respond ONLY with numbered points."
        # )
        human_prompt = HumanMessage(content=content)

        resp = llm_inference(model=self.model_name,
                             messages=messages + [human_prompt])

        plan = json.loads(resp.content)
        steps = plan["trajectory"]

        return steps

    def _thinking_step(self, messages):
        content = "You are now in the thinking stage. Choose a tool from the available tools and justify your choice."
        messages.append(HumanMessage(content=content))
        # content = (
        #     "You are now in the thinking stage."
        #     "You should reflect and come up with the sufficient knowledge points as sub tasks that you need to complete this task."
        #     "For each sub task, you can take multiple actions to complete it, and each action corresponds to a tool call."
        #     "Justify the confidence of your tool choice based on how much the tool can help you complete the sub task."
        #     "Usually, the later tools should have a lower confidence because you won't have enough information before executing the first few tools. But if you are very sure about the tool choice, you can also give a high confidence for later tools."
        # )
        return llm_inference(model=self.model_name, messages=messages)

    def _action_step(self, messages):
        human_prompt = HumanMessage(
            content="Now generate a tool call according to your last chosen tool.")
        return llm_inference(model=self.model_name, messages=messages + [human_prompt], tools=self.sync_tools + self.async_tools)

    def _analysis_uncertainty(self, original_messages, message):
        similarities = []
        alternative_messages = []
        for i in range(5):
            # Sample another answer (temperature sampling or prompt variants)
            candidate_answer = llm_inference(
                self.model_name, original_messages, temperature=1).content

            # Compute semantic similarity to the original answer
            sim = semantic_similarity(
                message, candidate_answer)
            similarities.append(sim)
            alternative_messages.append(candidate_answer)

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
        return (1 - obs_consistency), alternative_messages

    def _analysis_divergence(self, original_messages, message):
        # Placeholder for divergence analysis logic
        return 0.5  # Example return value

    def _analysis_commitment(self, original_messages, message):
        # Placeholder for commitment analysis logic
        return 0.5  # Example return value

    def _analysis_criticality(self, original_messages, message):
        # Placeholder for criticality analysis logic
        uncertainty, alternative_messages = self._analysis_uncertainty(
            original_messages, message)
        return uncertainty, alternative_messages
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
        print("Original Sub Tasks:")
        for i, task in enumerate(sub_tasks):
            print(f"{i+1}. {task}")
        self.trajectory = sub_tasks

        for i, task in enumerate(sub_tasks):
            print(f"{'='*10} {task} {'='*10}")
            messages.append(HumanMessage(
                content=f"""For the current sub task: {task}
1. List the candidate tools.
2. Explain why the selected tool provides the highest expected information gain.
3. Explain what uncertainty will remain after using this tool.
4. Explain what you will do if the tool output contradicts expectations."""
            )
            )
            ai_message = llm_inference(
                model=self.model_name, messages=messages)
            messages.append(ai_message)
            ai_message = self._action_step(messages)
            tool_results = await self._handle_tool_calls(ai_message)
            if tool_results is None:
                print("Agent has decided to submit the tool, ending process.")
                return 0

            messages.extend(tool_results)

            replan_prompt = HumanMessage(
                content="""After reviewing tool outputs:

1. Evaluate whether the current hypothesis was supported, weakened, or falsified.
2. Assign a confidence score (0-1) to the remaining trajectory.
3. Decide:
   - Continue
   - Modify specific steps
   - Fully replan

Respond in this format:

Hypothesis evaluation:
Confidence in remaining trajectory:
Decision:
New trajectory:
Justification:"""
            )
            ai_message = llm_inference(
                model=self.model_name, messages=messages + [replan_prompt])
            print(f"[Replan Reflection]: {ai_message.content}")
            break

        return 0
        step = 0
        while step < 10 and not self.submitted:
            ai_message = self._thinking_step(messages)
            print(f"[Thought]: {ai_message.content}")
            criticality, alternative_messages = self._analysis_criticality(
                messages, ai_message.content)

            print(f"{'=' * 10} Criticality score: {criticality} {'=' * 10}")
            if criticality > 0.5:
                print("Alternative messages:")
                for i, msg in enumerate(alternative_messages):
                    print(f"{i+1}. {msg}")
                # Recommend alternative
                # alternatives = self._generate_alternatives(ai_message)
                # print("Recommended alternatives:")
                # for alt in alternatives:
                #     print(f"- {alt}")

                # # Wait for human input before proceeding
                # input("Press Enter to continue...")

            messages.append(ai_message)
            ai_message = self._action_step(messages)
            tool_results = await self._handle_tool_calls(ai_message)

            messages.extend(tool_results)
            step += 1

        return 0

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
