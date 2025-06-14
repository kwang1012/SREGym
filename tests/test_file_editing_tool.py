import os
from unittest.mock import MagicMock, patch

import pytest
from langchain.agents.chat.prompt import HUMAN_MESSAGE
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from clients.langgraph_agent.k8s_agent import XAgent
from clients.langgraph_agent.llm_backend.init_backend import get_llm_backend_for_tools
from clients.langgraph_agent.state import State
from clients.langgraph_agent.tools.text_editing.file_manip import create, edit, goto_line, insert, open_file


def get_agent():
    llm = get_llm_backend_for_tools()
    xagent = XAgent(llm)
    xagent.build_agent()
    return xagent


def feed_input_to_agent(xagent: XAgent, input_text: list[str]):
    for user_input in input_text:
        if user_input.lower() in ["quit", "exit", "q"]:
            print("Goodbye!")
            break
        xagent.graph_step(user_input)


REPO_ROOT_PATH = "/Users/yms/tianyins_group/srearena"


TEST_LLM_RESPONSES = (
    {
        "messages": [
            SystemMessage(
                content="You are a helpful assistant.",
                additional_kwargs={},
                response_metadata={},
                id="",
            ),
            HumanMessage(
                content=f"open {REPO_ROOT_PATH}/clients/langgraph_agent/k8s_agent.py at line 100",
                additional_kwargs={},
                response_metadata={},
                id="",
            ),
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "call_osNIUg8kE7psP360dHinqNbm",
                            "function": {
                                "arguments": '{"path":"/Users/yms/tianyins_group/srearena/clients/langgraph_agent/k8s_agent.py","line_number":"100"}',
                                "name": "open_file",
                            },
                            "type": "function",
                        }
                    ],
                    "refusal": None,
                },
                response_metadata={},
                id="abc",
                tool_calls=[
                    {
                        "name": "open_file",
                        "args": {
                            "path": f"{REPO_ROOT_PATH}/clients/langgraph_agent/k8s_agent.py",
                            "line_number": "100",
                        },
                        "id": "call_osNIUg8kE7psP360dHinqNbm",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={},
            ),
        ],
        "curr_file": "/Users/yms/tianyins_group/srearena/clients/langgraph_agent/k8s_agent.py",
        "curr_line": "100",
    },
)

USER_INPUTS = [
    (
        [
            # open an existing file
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.txt at line 1"
        ],
        "hello world",
    ),
]


@pytest.mark.parametrize("test_tuple, expected_result", USER_INPUTS)
class TestOpenFile:
    def test_open_file_success(self, test_tuple: list[str], expected_result: str):
        xagent = get_agent()
        user_inputs = test_tuple
        feed_input_to_agent(xagent, user_inputs)
        last_state = xagent.graph.get_state(config={"configurable": {"thread_id": "1"}})
        msgs = last_state.values["messages"]

        assert expected_result in msgs[-1].content
