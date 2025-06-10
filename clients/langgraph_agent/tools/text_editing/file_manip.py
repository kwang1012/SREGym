import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from clients.langgraph_agent.k8s_agent import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def update_file_vars_in_state(state: State, message: ToolMessage | AIMessage | HumanMessage):
    match message:
        case ToolMessage():
            if message.tool_call.function.name == "open_file":
                return State(
                    messages=state["messages"] + [message],
                    curr_file=message.tool_call.arguments["file"],
                    curr_line=state["curr_line"],
                )
            elif message.tool_call.function.name == "goto_line":
                return State(
                    messages=state["messages"] + [message],
                    curr_file=state["curr_file"],
                    curr_line=message.tool_call.arguments["line"],
                )
        case _:
            logger.info("Not found open_file or goto_line in message: %s", message)
