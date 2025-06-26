import asyncio
import json
import logging
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END
from langgraph.graph import START, StateGraph

from .tools.kubectl_tools import \
    ExecKubectlCmdSafely, \
    RollbackCommand, \
    GetPreviousRollbackabelCmd
from fastmcp import Client
from fastmcp.client.transports import SSETransport
from .tools.stateful_async_tool_node import StatefulAsyncToolNode
from .utils.ai_msg_mock_utils import ai_msg_tpl
from .state import State


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def route_tools(state: State):
    """
    Use in the conditional edge to route to the ToolNode if the last message
    has tool calls. Otherwise, route to the end.
    """
    logger.info(f"route_tools: {state}")
    if isinstance(state, list):
        ai_message = state[-1]
    elif messages := state.get("messages", []):
        logger.info(f"route_tools: messages length: {len(messages)}")
        ai_message = messages[-1]
    else:
        raise ValueError(f"No messages found in input state to tool_edge: {state}")
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        tool_name = ai_message.tool_calls[0]["name"]
        kubectl_tool_names = [
            "exec_kubectl_cmd_safely",
            "rollback_command",
            "get_previous_rollbackabel_cmd",
        ]
        if tool_name in kubectl_tool_names:
            logger.info("routing tool name: %s", tool_name)
            return "kubectl_tools_node"
        else:
            logger.info(f"Fail routing to kubectl tools node with tool {tool_name}. Tool not found.")
    else:
        logger.info("no tool call, returning END")
    logger.info("invoking node: end")
    return END


class NL2KubectlAgent:
    def __init__(self, llm, client: Client):
        self.client = client
        exec_kubectl_cmd_safely = ExecKubectlCmdSafely(self.client)
        rollback_command = RollbackCommand(self.client)
        get_previous_rollbackabel_cmd = GetPreviousRollbackabelCmd(self.client)
        self.kubectl_tools = [
            exec_kubectl_cmd_safely,
            rollback_command,
            get_previous_rollbackabel_cmd,
        ]

        self.llm = llm

        self.graph_builder = StateGraph(State)
        self.graph = None

        self.test_tool_call_idx = 0
        self.test_campaign_file = ""
        self.test_tool_or_ai_response = "tool"

    def test_campaign_setter(self, test_campaign_file):
        self.test_campaign_file = test_campaign_file

    def mock_llm_inference_step(self, state: State):
        logger.info(f"[mock llm] called by graph, currently on {self.test_tool_call_idx}th tool call")
        ai_message_template = AIMessage(**ai_msg_tpl)
        logger.info("invoking mock llm inference, custom state: %s", state)
        logger.info(f"[mock llm] msg branch: {self.test_tool_or_ai_response}")
        if self.test_tool_or_ai_response == "tool":
            test_campaign = yaml.safe_load(open(self.test_campaign_file, "r"))
            cur_test = test_campaign["test"][self.test_tool_call_idx]
            logger.info(f"\n[mock llm] test campaign tool call: {cur_test['tool_call']}"
                        f"\nargs: {cur_test.get('args', dict())}")

            function_name = cur_test['tool_call']
            function_args_str = json.dumps(cur_test.get('args', dict()))
            ai_message_template.additional_kwargs["tool_calls"][0]["function"]["arguments"] = function_args_str
            ai_message_template.additional_kwargs["tool_calls"][0]["function"]["name"] = function_name
            ai_message_template.tool_calls[0]["name"] = function_name
            ai_message_template.tool_calls[0]["args"] = cur_test.get('args', dict())

            logger.info("[mock llm] type: %s, ai message returned: %s", type(ai_message_template), ai_message_template)
            logger.info("[mock llm] tool calling, returning to ai")
            self.test_tool_call_idx += 1
        elif self.test_tool_or_ai_response == "ai":
            ai_message_template.tool_calls = []
            ai_message_template.content = "test"
            ai_message_template.additional_kwargs = {"refusal": None}
            ai_message_template.response_metadata["finish_reason"] = "stop"
            logger.info("[mock llm] ai messaging, returning to tool")

        if self.test_tool_or_ai_response == "tool":
            self.test_tool_or_ai_response = "ai"
        elif self.test_tool_or_ai_response == "ai":
            self.test_tool_or_ai_response = "tool"

        logger.info(
            "[mock llm] next msg branch: %s, messages returns: %s",
            self.test_tool_or_ai_response,
            state["messages"] + [ai_message_template],
        )

        output = [*state["messages"], ai_message_template]
        return {
            "messages": output,
            "curr_file": state["curr_file"],
            "curr_line": state["curr_line"],
        }

    def llm_inference_step(self, state: State):
        logger.info("invoking llm inference, custom state: %s", state)
        return {
            "messages": [self.llm.inference(messages=state["messages"], tools=self.kubectl_tools)],
            "curr_file": state["curr_file"],
            "curr_line": state["curr_line"],
        }

    def build_agent(self, mock: bool = False):
        # we add the node to the graph
        if mock:
            self.graph_builder.add_node("agent", self.mock_llm_inference_step)
        else:
            self.graph_builder.add_node("agent", self.llm_inference_step)

        # we also have a tool node. this tool node connects to a jaeger MCP server
        # and allows you to query any jaeger information
        kubectl_tools_node = StatefulAsyncToolNode(self.kubectl_tools, self.client)

        # we add the node to the graph
        self.graph_builder.add_node("kubectl_tools_node", kubectl_tools_node)

        # after creating the nodes, we now add the edges
        # the start of the graph is denoted by the keyword START, end is END.
        # here, we point START to the "agent" node
        self.graph_builder.add_edge(START, "agent")

        # once we arrive at the "agent" node, the execution graph can
        # have 2 paths: either choosing to use a tool or not.
        # e.g.,
        # agent -> ob tool -> agent -> ob tool (tool loop)
        # agent -> agent -> agent -> end (normal chatbot loop)
        # this is accomplished by "conditional edges" in the graph
        # we implement "route_tools," which routes the execution based on the agent's
        # output. if the output is a tool usage, we direct the execution to the tool and loop back to the agent node
        # if not, we finish *one* graph traversal (i.e., to END)
        self.graph_builder.add_conditional_edges(
            "agent",
            route_tools,
            # The following dictionary lets you tell the graph to interpret the condition's outputs as a specific node
            # It defaults to the identity function, but if you
            # want to use a node named something else apart from "tools",
            # You can update the value of the dictionary to something else
            # e.g., "tools": "my_tools"
            {"kubectl_tools_node": "kubectl_tools_node", END: END},
        )
        self.graph_builder.add_edge("kubectl_tools_node", "agent")

        # interestingly, for short-term memory (i.e., agent trajectories or conversation history), we need
        # to explicitly implement it.
        # here, it is implemented as a in-memory checkpointer.
        memory = MemorySaver()
        self.graph = self.graph_builder.compile(checkpointer=memory)

    def graph_step(self, user_input: str):
        if not self.graph:
            raise ValueError("Agent graph is None. Have you built the agent?")
        config = {"configurable": {"thread_id": "1"}}
        last_state = self.graph.get_state(config=config)
        logger.info("last state: %s", last_state)
        if len(last_state.values) != 0:
            msgs = last_state.values["messages"] + [{"role": "user", "content": user_input}]
            state = dict(last_state.values)
            state["messages"] = msgs
            logger.info(f"last state values: %s", state)
        else:
            state = {
                "messages": [{"role": "user", "content": user_input}],
                "workdir": "",
                "curr_file": "",
                "curr_line": 0,
            }
        for event in self.graph.stream(
            state,
            config=config,
            stream_mode="values",
        ):
            event["messages"][-1].pretty_print()


async def main():
    from clients.langgraph_agent.llm_backend.init_backend import get_llm_backend_for_tools

    stateful_tool_url = ""
    transport = SSETransport(
        url=stateful_tool_url
    )
    client = Client(transport)
    llm = get_llm_backend_for_tools()

    agent = NL2KubectlAgent(llm, client)
    agent.build_agent()
    # keep the connection for the whole conversation
    async with client:
        while True:
            user_input = input("User: ")
            if user_input.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break
            agent.graph_step(user_input)


# a short chatbot loop to demonstrate the workflow.
if __name__ == "__main__":
    asyncio.run(main())
