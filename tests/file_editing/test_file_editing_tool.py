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


REPO_ROOT_PATH = "/"


TEST_STATE = {
    "messages": [
        SystemMessage(
            content="You are a helpful assistant.",
            additional_kwargs={},
            response_metadata={},
            id="5736d1a4-5647-440a-9b41-930757358b9b",
        ),
        HumanMessage(
            content=f"open {REPO_ROOT_PATH}/clients/langgraph_agent/k8s_agent.py at line 100",
            additional_kwargs={},
            response_metadata={},
            id="98fb3621-7378-423d-8a9d-1b2fa2c264c3",
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
            response_metadata={
                "token_usage": {
                    "completion_tokens": 39,
                    "prompt_tokens": 588,
                    "total_tokens": 627,
                    "completion_tokens_details": {
                        "accepted_prediction_tokens": 0,
                        "audio_tokens": 0,
                        "reasoning_tokens": 0,
                        "rejected_prediction_tokens": 0,
                    },
                    "prompt_tokens_details": {"audio_tokens": 0, "cached_tokens": 0},
                },
                "model_name": "gpt-4o-2024-08-06",
                "system_fingerprint": "fp_07871e2ad8",
                "id": "chatcmpl-Bhh2o2j0cJ5jw6wTll8TXTzifUvJF",
                "service_tier": "default",
                "finish_reason": "tool_calls",
                "logprobs": None,
            },
            id="run--d19df02c-3833-4866-b9d9-998d43e90179-0",
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
            usage_metadata={
                "input_tokens": 588,
                "output_tokens": 39,
                "total_tokens": 627,
                "input_token_details": {"audio": 0, "cache_read": 0},
                "output_token_details": {"audio": 0, "reasoning": 0},
            },
        ),
    ],
    "curr_file": "/Users/yms/tianyins_group/srearena/clients/langgraph_agent/k8s_agent.py",
    "curr_line": "100",
}

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
        tool_call_state = TEST_STATE["messages"][-1].tool_calls[0]

        open_file.invoke(
            input=tool_call_state,
            # tool_call_id=TEST_STATE["messages"][-1].tool_calls[0]["id"],
            # path=test_tuple[0],
            # line_number=1,
        )
