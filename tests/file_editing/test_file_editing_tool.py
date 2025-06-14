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
    xagent.build_agent(mock=True)
    return xagent


def feed_input_to_agent(xagent: XAgent, input_text: list[str]):
    for user_input in input_text:
        if user_input.lower() in ["quit", "exit", "q"]:
            print("Goodbye!")
            break
        xagent.graph_step(user_input)


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
        feed_input_to_agent(xagent, test_tuple)
        assert (
            xagent.graph.get_state().values["curr_file"]
            == "/Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.txt"
        )
        assert xagent.graph.get_state().values["curr_line"] == 1
        assert xagent.graph.get_state().values["messages"][-1].content == expected_result
