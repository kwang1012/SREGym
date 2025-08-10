import asyncio
import logging
from pathlib import Path

import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END, START

from clients.stratus.llm_backend.init_backend import get_llm_backend_for_tools
from clients.stratus.stratus_agent.base_agent import BaseAgent
from clients.stratus.stratus_utils.get_logger import get_logger
from clients.stratus.stratus_utils.get_starting_prompt import get_starting_prompts
from clients.stratus.stratus_utils.str_to_tool import str_to_tool
from clients.stratus.tools.stratus_tool_node import StratusToolNode

logger = get_logger()


class DiagnosisAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def build_agent(self):
        tool_node = StratusToolNode(async_tools=self.async_tools, sync_tools=self.sync_tools)
        thinking_node = "thinking_step"
        tool_calling_node = "tool_calling_step"
        process_tool_call_node = "process_tool_call"
        # we add the node to the graph
        self.graph_builder.add_node(thinking_node, self.llm_thinking_step)
        self.graph_builder.add_node(tool_calling_node, self.llm_tool_call_step)
        self.graph_builder.add_node(process_tool_call_node, tool_node)

        # commenting these out first, focusing on basic diagnosis capability
        # self.graph_builder.add_node("post_tool_hook", self.post_tool_hook)
        # self.graph_builder.add_node("summarize_messages", self.summarize_messages)

        self.graph_builder.add_edge(START, thinking_node)
        self.graph_builder.add_edge(thinking_node, tool_calling_node)
        self.graph_builder.add_edge(tool_calling_node, process_tool_call_node)
        self.graph_builder.add_edge(process_tool_call_node, END)

        #     self.graph_builder.add_conditional_edges(
        #     "explanation_agent",
        #     self.check_if_summaries_needed,  # This must return True or False
        #     {
        #         True: "summarize_messages",
        #         False: "tool_node",
        #     }
        # )
        # self.graph_builder.add_edge("summarize_messages", "explanation_agent")
        # self.graph_builder.add_conditional_edges(
        #     "tool_node",
        #     self.check_if_summaries_needed,  # should return True or False
        #     {True: "summarize_messages", False: "post_tool_hook"},
        # )
        # self.graph_builder.add_conditional_edges(
        #     "post_tool_hook",
        #     self.post_tool_route,
        #     {"explanation_agent": "explanation_agent", END: END},
        # )
        # self.graph_builder.add_edge("summarize_messages", "post_tool_hook")
        memory = MemorySaver()
        self.graph = self.graph_builder.compile(checkpointer=memory)

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

        graph_config = {"configurable": {"thread_id": "1"}}
        last_state = self.graph.get_state(config=graph_config)
        logger.info("last state: %s", last_state)
        if len(last_state.values) != 0:
            logger.info("last state values: %s", last_state.values["messages"])
            # this is all the previous msgs the agent had, we just inherit them in the next graph traversal
            state = last_state.values
        else:
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
            }

        graph_events = []
        async for event in self.graph.astream(
            state,
            # recursion_limit could be as large as possible as we have our own limit.
            config={"recursion_limit": 10000, "configurable": {"thread_id": "1"}},
            stream_mode="values",
        ):
            graph_events.append(event)
            event["messages"][-1].pretty_print()

        return graph_events[-1]


def main():
    file_parent_dir = Path(__file__).resolve().parent
    diagnosis_agent_config_path = file_parent_dir.parent / "configs" / "diagnosis_agent_config.yaml"
    diagnosis_agent_config = yaml.safe_load(open(diagnosis_agent_config_path, "r"))
    max_step = diagnosis_agent_config["max_step"]
    prompt_path = file_parent_dir.parent / "configs" / diagnosis_agent_config["prompts_path"]
    sync_tools = []
    async_tools = []
    tool_descriptions = ""
    for sync_tool_struct, async_tool_struct in zip(
        diagnosis_agent_config["sync_tools"], diagnosis_agent_config["async_tools"]
    ):
        sync_tools.append(str_to_tool(sync_tool_struct))
        async_tools.append(str_to_tool(async_tool_struct))
        tool_descriptions += sync_tool_struct["description"] + "\n\n" + async_tool_struct["description"]

    agent = DiagnosisAgent(
        llm=get_llm_backend_for_tools(),
        max_step=max_step,
        sync_tools=sync_tools,
        async_tools=async_tools,
        tool_descs=tool_descriptions,
    )
    agent.build_agent()

    res = asyncio.run(agent.arun(get_starting_prompts(prompt_path, max_step=max_step)))
    print(res)


if __name__ == "__main__":
    main()
