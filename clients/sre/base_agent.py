import json
import yaml

from langchain_cerebras import ChatCerebras
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from clients.sre.utils import cprint
from clients.stratus.stratus_utils.str_to_tool import str_to_tool


def llm_inference(model, messages, tools: list | None = None, **kwargs):

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        **kwargs,
    )
    # llm = ChatCerebras(
    #     model=model,
    #     **kwargs,
    # )
    # llm = ChatOpenAI(
    #     base_url="http://localhost:8000/v1",
    #     model="meta-llama/Llama-3.3-70B-Instruct",
    #     **kwargs,
    # )
    if tools:
        llm = llm.bind_tools(tools)

    response = llm.invoke(input=messages)

    return response


def build_tools(file_path: str):
    diagnosis_agent_tools = yaml.safe_load(open(file_path))
    sync_tools = []
    async_tools = []
    tool_descriptions = ""
    if diagnosis_agent_tools["sync_tools"] is not None:
        for sync_tool_struct in diagnosis_agent_tools["sync_tools"]:
            sync_tools.append(str_to_tool(sync_tool_struct))
            tool_descriptions += (
                f"tool name: {sync_tool_struct['name']}"
                + "\n\n"
                + f"tool descriptions {sync_tool_struct['description']}"
                + "\n\n"
            )
    if diagnosis_agent_tools["async_tools"] is not None:
        for async_tool_struct in diagnosis_agent_tools["async_tools"]:
            async_tools.append(str_to_tool(async_tool_struct))
            tool_descriptions += (
                f"tool name: {async_tool_struct['name']}"
                + "\n\n"
                + f"tool description: {async_tool_struct['description']}"
                + "\n\n"
            )
    return sync_tools, async_tools, tool_descriptions


class BaseAgent:
    def __init__(self, logs_dir, model_name):
        self.log_dir = logs_dir
        self.model_name = model_name

        self.sync_tools, self.async_tools, self.tool_descs = build_tools(
            "./clients/sre/agent_tools.yaml")

        self.submitted = False
        self.sync_tools_by_name = {t.name: t for t in self.sync_tools}
        self.async_tools_by_name = {t.name: t for t in self.async_tools}

    async def _handle_tool_calls(self, message: AIMessage):
        if not isinstance(message, AIMessage):
            raise ValueError(
                "Last message is not an AIMessage; skipping tool invocation.")

        new_messages = []
        for tool_call in message.tool_calls:
            arg_list = [f"{key} = {value}" for key,
                        value in tool_call["args"].items()]
            tools_str = f"\n- {tool_call['name']}({', '.join(arg_list)})"
            # print(f"[AGENT] AI Tool Calls: {tools_str}")
            if tool_call["name"] == "n_submit_tool":
                self.submitted = True
                continue
            try:
                if tool_call["name"] in self.sync_tools_by_name:
                    tool_result = self.sync_tools_by_name[tool_call["name"]].invoke(
                        {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "args": {"state": {}, **tool_call["args"]},
                            "id": tool_call["id"],
                        }
                    )
                elif tool_call["name"] in self.async_tools_by_name:
                    tool_result = await self.async_tools_by_name[tool_call["name"]].ainvoke(
                        {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "args": {"state": {}, **tool_call["args"]},
                            "id": tool_call["id"],
                        }
                    )
                else:
                    tool_result = Command(
                        update={
                            "messages": [
                                ToolMessage(
                                    content=f"Tool {tool_call['name']} does not exist!",
                                    tool_call_id=tool_call["id"],
                                )
                            ]
                        }
                    )

                assert isinstance(
                    tool_result, Command
                ), f"Tool {tool_call['name']} should return a Command object, but return {type(tool_result)}"
                if not tool_result.update:
                    continue
                # print(
                #     f"[AGENT] Tool {tool_call['name']} returns: {tool_result.update['messages']}")
                new_messages += tool_result.update["messages"]

                if tool_call["name"] == "n_submit_tool":
                    print(tool_result.update)
                if "submitted" in tool_result.update and tool_result.update["submitted"]:
                    self.submitted = True
            except Exception as e:
                cprint(f"[AGENT] Error calling tool {tool_call['name']}: {e}", "red")
                new_messages += [
                    ToolMessage(
                        content=f"Error: {e}; This happens usually because you are "
                        f"passing inappropriate arguments to the tool.",
                        tool_call_id=tool_call["id"],
                    )
                ]

        return new_messages
