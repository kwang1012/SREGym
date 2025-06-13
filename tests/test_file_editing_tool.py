import os
from unittest.mock import MagicMock, patch

import pytest
from langchain.agents.chat.prompt import HUMAN_MESSAGE
from langchain_core.messages import HumanMessage, ToolMessage
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


USER_INPUTS = [
    (
        [
            # open an existing file
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.txt at line 1"
        ],
        "hello world",
    ),
    (
        [
            # open a nonexistent file
            "open /Users/yms/tianyins_group/srearena/mcp_server/nonexistent.txt at line 1"
        ],
        "does not exist",
    ),
    (
        [
            # find if a line is returned by the tool
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/file_manip.py at line 80"
        ],
        "if not os.path.exists(path):",
    ),
    # (
    #     [
    #         # test basic goto_line
    #         "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/file_manip.py at line 1",
    #         "goto line 100",
    #     ],
    #     "line_num = wf.n_lines",
    # ),
    # (
    #     [
    #         # test goto_line if no file is opened
    #         "goto line 100"
    #     ],
    #     "No file is opened",
    # ),
    (
        [
            # test basic edit
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.txt at line 1",
            "use the edit tool to search for 'hello world' and replace with 'world hello'",
            "goto line 1",
        ],
        "world hello",
    ),
    (
        [
            # test editing python file.
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.py at line 1",
            "use the edit tool to rewrite the while loop with a for loop.",
            "goto line 1",
        ],
        "for i:",
    ),
    (
        [
            # test basic insert
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.txt at line 1",
            "use the insert tool to insert 'world hello' at the end of the file.",
            "goto line 1",
        ],
        "world hello",
    ),
    (
        [
            # test insert to python file
            "open /Users/yms/tianyins_group/srearena/clients/langgraph_agent/tools/text_editing/example.py at line 1",
            "use the insert tool to insert a print statement that prints 'hello world' at the end of the file.",
            "goto line 1",
        ],
        "print('hello world')",
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
