import logging

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from clients.stratus.stratus_agent.state import State
from clients.stratus.tools.stratus_tool_node import StratusToolNode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, llm, max_step, sync_tools, async_tools, submit_tool, tool_descs):
        self.graph_builder = StateGraph(State)
        self.graph: CompiledStateGraph | None = None
        self.max_step = max_step
        self.async_tools = async_tools
        self.sync_tools = sync_tools
        self.llm = llm
        self.tool_descs = tool_descs
        self.submit_tool = submit_tool
        self.force_submit_prompt_inject_node = "force_submit_thinking_step"
        self.force_submit_tool_call_node = "force_submit_tool_call"
        self.llm_force_submit_tool_call_node = StratusToolNode(sync_tools=[], async_tools=[submit_tool])
        self.thinking_prompt_inject_node = "pre_thinking_step"
        self.thinking_node = "thinking_step"
        self.tool_calling_prompt_inject_node = "pre_tool_calling_step"
        self.tool_calling_node = "tool_calling_step"
        self.process_tool_call_node = "process_tool_call"
        self.post_round_process_node = "post_round_process"
        self.callback = UsageMetadataCallbackHandler()

    def llm_inference_step(self, messages, tools):
        return self.llm.inference(messages=messages, tools=tools)

    def llm_thinking_prompt_inject_step(self, state: State):
        human_prompt = HumanMessage(
            content="You are now in the thinking stage. Here are all the tools you can use:\n"
            + self.tool_descs
            + "Choose a tool from the list and output the tool name. Justify your tool choice. In the next step, you will generate a tool call for this tool"
        )
        return {
            "messages": [human_prompt],
        }

    def llm_thinking_step(self, state: State):
        # planning step, not providing tool
        ai_message = self.llm_inference_step(state["messages"], tools=None)
        return {
            "messages": [ai_message],
        }

    def llm_tool_call_prompt_inject_step(self, state: State):
        human_prompt = HumanMessage(content="Now generate a tool call according to your last chosen tool.")
        return {
            "messages": [human_prompt],
        }

    def llm_tool_call_step(self, state: State):
        if self.sync_tools is None:
            if self.async_tools is not None:
                ai_message = self.llm_inference_step(state["messages"], tools=self.async_tools)
            else:
                raise ValueError("the agent must have at least 1 tool!")
        else:
            if self.async_tools is None:
                ai_message = (self.llm_inference_step(state["messages"], tools=self.sync_tools),)
            else:
                ai_message = self.llm_inference_step(state["messages"], tools=[*self.sync_tools, *self.async_tools])
        return {
            "messages": [ai_message],
        }

    def should_submit_router(self, state: State):
        should_submit = state["num_steps"] == self.max_step and state["submitted"] == False
        logger.info(f"Should we force the agent submit? {"Yes!" if should_submit else "No!"}")
        return self.force_submit_prompt_inject_node if should_submit else self.post_round_process_node

    def post_round_process(self, state: State):
        logger.info("agent finished a round")
        logger.info("currently only incrementing step")
        return {
            "num_steps": state["num_steps"] + 1,
        }

    def llm_force_submit_thinking_step(self, state: State):
        human_prompt = HumanMessage(
            content="You have reached your step limit, please submit your results by generating a `submit` tool's tool call."
        )
        return {"messages": [human_prompt]}

    def llm_force_submit_tool_call_step(self, state: State):
        return {"messages": self.llm_inference_step(state["messages"], tools=[self.submit_tool])}

    def save_agent_graph_to_png(self):
        with open("./agent_graph.png", "wb") as png:
            png.write(self.graph.get_graph().draw_mermaid_png())

    def clear_memory(self):
        if not hasattr(self, "memory_saver"):
            raise RuntimeError("Should not be called on uninitialized agent. Did you call build_agent()?")
        # source: https://github.com/langchain-ai/langchain/discussions/19744#discussioncomment-13734390
        thread_id = "1"
        try:
            if hasattr(self.memory_saver, "storage") and hasattr(self.memory_saver, "writes"):
                self.memory_saver.storage.pop(thread_id, None)

                keys_to_remove = [key for key in self.memory_saver.writes.keys() if key[0] == thread_id]
                for key in keys_to_remove:
                    self.memory_saver.writes.pop(key, None)

                print(f"Memory cleared for thread_id: {thread_id}")
                return
        except Exception as e:
            logger.error(f"Error clearing InMemorySaver storage for thread_id {thread_id}: {e}")

    def run(self, starting_prompts):
        """Running an agent

        Args:
            starting_prompts (list[SystemMessage | HumanMessage]): The data inside the dict will be filled into the prompts.

        Returns:
            final state of the agent running, including messages and other state values.
        """
        if not self.graph:
            raise ValueError("Agent graph is None. Have you built the agent?")

        if len(starting_prompts) == 0:
            raise ValueError("No prompts used to start the conversation!")

        state = {
            "messages": starting_prompts,
            "num_steps": 0,
            "submitted": False,
            "rollback_stack": "",
        }

        return list(
            self.graph.stream(
                state,
                # recursion_limit could be as large as possible as we have our own limit.
                config={"recursion_limit": 10000, "configurable": {"thread_id": "1"}, "callbacks": [self.callback]},
                stream_mode="values",
            )
        )[-1]

    async def arun(self, starting_prompts):
        """
        Async running an agent

        Args:
            starting_prompts (dict): The data inside the dict will be filled into the prompts.

        Returns:
            final state of the agent running, including messages and other state values.
        """
        if not self.graph:
            raise ValueError("Agent graph is None. Have you built the agent?")

        if len(starting_prompts) == 0:
            raise ValueError("No prompts used to start the conversation!")

        graph_events = []
        while True:
            graph_config = {"configurable": {"thread_id": "1"}}
            last_state = self.graph.get_state(config=graph_config)
            if len(last_state.values) != 0:
                logger.info("There were last states.")
                # this is all the previous msgs the agent had, we just inherit them in the next graph traversal
                state = last_state.values
            else:
                logger.info("There were no states.")
                # fresh agent start, init state here
                state = {
                    "messages": starting_prompts,
                    # "workdir": "",
                    # "curr_file": "",
                    # "curr_line": 0,
                    "num_steps": 0,
                    # "rec_submission_rounds": 0,
                    # "submit_tried": False,
                    "submitted": False,
                    # "ans": dict(),
                    "rollback_stack": "",
                }

            async for event in self.graph.astream(
                state,
                # recursion_limit could be as large as possible as we have our own limit.
                config={"recursion_limit": 10000, "configurable": {"thread_id": "1"}, "callbacks": [self.callback]},
                stream_mode="values",
            ):
                graph_events.append(event)
                event["messages"][-1].pretty_print()
            last_state = self.graph.get_state(config=graph_config)
            if last_state.values["submitted"]:
                logger.info("agent submitted, breaking loop from base_agent")
                break

        return last_state
