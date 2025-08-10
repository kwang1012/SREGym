import logging
import os

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.constants import END
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from clients.stratus.configs import BaseAgentCfg
from clients.stratus.llm_backend.init_backend import get_llm_backend_for_tools
from clients.stratus.stratus_agent.state import State
from clients.stratus.tools.stratus_tool_node import StratusToolNode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, llm, max_step, prompt_path, sync_tools, async_tools):
        self.graph_builder = StateGraph(State)
        self.graph: CompiledStateGraph | None = None
        self.max_round = max_step
        self.prompts_file_path = prompt_path
        self.async_tools = async_tools
        self.sync_tools = sync_tools
        self.llm = llm

    def llm_inference_step(self, messages, tools):
        return self.llm.inference(messages=messages, tools=tools)

    def llm_explanation_step(self, state: State):
        human_prompt = HumanMessage(
            content="You are now in explanation stage; please briefly explain why you "
            "want to call the tools with the arguments in next tool-call stage; "
            "the tools you mentioned must be available to you at first. "
            "You should not call any tools in this stage; "
        )
        ai_message = self.llm_inference_step(state["messages"] + [human_prompt])
        ai_message.additional_kwargs["is_thought"] = True
        new_messages = [human_prompt, ai_message]
        for tool_call in ai_message.tool_calls:
            tool_call_message = ToolMessage(
                content="Error: You should not call any tools in the explanation stage!", tool_call_id=tool_call["id"]
            )
            new_messages.append(tool_call_message)
        return {
            "messages": new_messages,
        }

    def save_agent_graph_to_png(self):
        with open("./agent_graph.png", "wb") as png:
            png.write(self.graph.get_graph().draw_mermaid_png())

    def run(self, data_for_prompts: dict):
        """Running an agent

        Args:
            data_for_prompts (dict): The data inside the dict will be filled into the prompts.

        Returns:
            final state of the agent running, including messages and other state values.
        """
        if not self.graph:
            raise ValueError("Agent graph is None. Have you built the agent?")

        prompts = get_init_prompts(data_for_prompts)
        if len(prompts) == 0:
            raise ValueError("No prompts used to start the conversation!")

        state = {
            "messages": prompts,
            "num_steps": 0,
            "submitted": False,
        }

        return list(
            self.graph.stream(
                state,
                # recursion_limit could be as large as possible as we have our own limit.
                config={"recursion_limit": 10000},
                stream_mode="values",
            )
        )[-1]

    async def arun(self, data_for_prompts: dict):
        """
        Async running an agent

        Args:
            data_for_prompts (dict): The data inside the dict will be filled into the prompts.

        Returns:
            final state of the agent running, including messages and other state values.
        """
        if not self.graph:
            raise ValueError("Agent graph is None. Have you built the agent?")

        prompts = get_init_prompts(data_for_prompts)
        if len(prompts) == 0:
            raise ValueError("No prompts used to start the conversation!")

        state = {
            "messages": prompts,
            "workdir": "",
            "curr_file": "",
            "curr_line": 0,
            "num_rounds": 0,
            "rec_submission_rounds": 0,
            "submit_tried": False,
            "submitted": False,
            "ans": dict(),
        }

        res = []
        async for event in self.graph.astream(
            state,
            # recursion_limit could be as large as possible as we have our own limit.
            config={"recursion_limit": 10000},
            stream_mode="values",
        ):
            res.append(event)
            event["messages"][-1].pretty_print()

        return res[-1]
