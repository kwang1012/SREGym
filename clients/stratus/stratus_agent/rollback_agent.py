import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END
from langgraph.graph import START

from clients.stratus.configs import BaseAgentCfg
from clients.stratus.stratus_agent.base_agent import BaseAgent
from clients.stratus.tools.stratus_tool_node import StratusToolNode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class RollbackAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tool_node = None

    def build_agent(self):
        self.tool_node = StratusToolNode(
            async_tools=self.async_tools,
            sync_tools=self.sync_tools,
        )

        self.graph_builder.add_node(self.thinking_prompt_inject_node, self.llm_thinking_prompt_inject_step)
        self.graph_builder.add_node(self.tool_calling_prompt_inject_node, self.llm_tool_call_prompt_inject_step)
        self.graph_builder.add_node(self.thinking_node, self.llm_thinking_step)
        self.graph_builder.add_node(self.tool_calling_node, self.llm_tool_call_step)
        self.graph_builder.add_node(self.process_tool_call_node, self.tool_node)
        self.graph_builder.add_node(self.post_round_process_node, self.post_round_process)
        self.graph_builder.add_node(self.force_submit_tool_call_node, self.llm_force_submit_tool_call_node)

        self.graph_builder.add_edge(START, self.thinking_prompt_inject_node)
        self.graph_builder.add_edge(self.thinking_prompt_inject_node, self.thinking_node)
        self.graph_builder.add_edge(self.thinking_node, self.tool_calling_prompt_inject_node)
        self.graph_builder.add_edge(self.tool_calling_prompt_inject_node, self.tool_calling_node)
        self.graph_builder.add_edge(self.tool_calling_node, self.process_tool_call_node)
        self.graph_builder.add_edge(self.process_tool_call_node, self.post_round_process_node)
        self.graph_builder.add_conditional_edges(
            self.process_tool_call_node,
            self.should_submit_router,
            {
                self.force_submit_tool_call_node: self.force_submit_tool_call_node,
                self.post_round_process_node: self.post_round_process_node,
            },
        )
        self.graph_builder.add_edge(self.force_submit_tool_call_node, END)
        self.graph_builder.add_edge(self.post_round_process_node, END)

        memory = MemorySaver()
        self.graph = self.graph_builder.compile(checkpointer=memory)
