import logging
import os

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.constants import END
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from clients.stratus.llm_backend.init_backend import get_llm_backend_for_tools
from clients.stratus.stratus_agent.state import State
from clients.stratus.tools.stratus_tool_node import StratusToolNode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, llm, max_step, sync_tools, async_tools, submit_tool, tool_descs):
        self.graph_builder = StateGraph(State)
        self.graph: CompiledStateGraph | None = None
        self.max_round = max_step
        self.async_tools = async_tools
        self.sync_tools = sync_tools
        self.llm = llm
        self.tool_descs = tool_descs
        self.submit_tool = submit_tool
        self.force_submit_node = "force_submit"
        self.llm_force_submit_tool_call_node = StratusToolNode(sync_tools=[], async_tools=[submit_tool])

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
        # Fixme: bad programming here! make sure self.max_step and self.post_round_process_node is defined in
        #   this class too!!
        should_submit = state["num_steps"] == self.max_step and state["submitted"] == False
        logger.info(f"Should the agent submit? {"Yes!" if should_submit else "No!"}")
        return self.force_submit_node if should_submit else self.post_round_process_node

    def post_round_process(self, state: State):
        logger.info("agent finished a round")
        logger.info("currently only incrementing step")
        return {
            "num_steps": state["num_steps"] + 1,
        }

    def llm_force_submit_thinking_step(self, state: State):
        # actual tool node defined in __init__
        human_prompt = HumanMessage(
            content="You have reached your step limit, please submit your results by generating a `submit` tool's tool call."
        )
        return {"messages": self.llm_inference_step(state["messages"] + [human_prompt], tools=[self.submit_tool])}

    def save_agent_graph_to_png(self):
        with open("./agent_graph.png", "wb") as png:
            png.write(self.graph.get_graph().draw_mermaid_png())

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
        }

        return list(
            self.graph.stream(
                state,
                # recursion_limit could be as large as possible as we have our own limit.
                config={"recursion_limit": 10000, "configurable": {"thread_id": "1"}},
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
        }

        res = []
        async for event in self.graph.astream(
            state,
            # recursion_limit could be as large as possible as we have our own limit.
            config={"recursion_limit": 10000, "configurable": {"thread_id": "1"}},
            stream_mode="values",
        ):
            res.append(event)
            event["messages"][-1].pretty_print()

        return res[-1]
