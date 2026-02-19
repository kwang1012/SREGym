import json

from graphviz import Digraph

from clients.sre.base_agent import BaseAgent, llm_inference

from langchain_core.messages import HumanMessage

from clients.sre.utils import cprint


class DFSAgent(BaseAgent):

    def __init__(self, logs_dir, model_name):
        super().__init__(logs_dir, model_name)
        self._action_tree = {}

    async def _action_step(self, messages):
        prompt = "Now generate the tool calls according to your last chosen tools."
        messages.append(HumanMessage(content=prompt))
        ai_message = llm_inference(
            model=self.model_name, messages=messages, tools=self.sync_tools + self.async_tools)
        messages.append(ai_message)
        tool_results = await self._handle_tool_calls(ai_message)
        tool_str_list = []
        for tool_call in ai_message.tool_calls:
            arg_list = [f"{key}={value}" for key,
                        value in tool_call["args"].items()]
            tools_str = f"- {tool_call['name']}({', '.join(arg_list)})"
            cprint(tools_str, "magenta")
            tool_str_list.append(tools_str)
        return tool_results, tool_str_list

    def _thinking_step(self, messages):
        # pick three differnt tools for three different branches, and justify the choice for each branch
        prompt = """You are now in the thinking stage. 
You should propose N (N<=3) different tools from the available tools that you think best address the current task and justify your choice for each tool.

Respond ONLY in JSON:
{
    "actions": [
        {
            "tool_name": string,
            "justification": string
        }
    ]
}
Do not add extra keys.

Respond in structured JSON.
You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON."""
        human_message = HumanMessage(content=prompt)
        resp = llm_inference(model=self.model_name,
                             messages=messages + [human_message])

        try:
            actions = json.loads(resp.content).get("actions", [])
        except json.JSONDecodeError:
            cprint(
                "Failed to parse JSON response from the model. Response content:", "red")
            cprint(resp.content, "red")
            actions = []
        return actions

    def visualize_action_tree(self, output_file="action_tree"):
        dot = Digraph(comment="Agent Action Tree")

        def add_nodes(node, parent_id=None, counter=[0]):
            node_id = str(counter[0])
            counter[0] += 1

            action = node.get("action", "root")
            step = node.get("step", -1)
            justification = node.get("justification", "")

            label = f"{action}"
            # if justification:
            #     label += f"\n{justification}"

            dot.node(node_id, label)

            if parent_id is not None:
                dot.edge(parent_id, node_id)

            for child in node.get("children", []):
                add_nodes(child, node_id)

        add_nodes(self._action_tree)

        dot.render(output_file, view=True, format="png")

    async def dfs(self, messages, step, parent_node):
        if step == 20 or self.submitted:
            return

        if step not in self._action_tree:
            self._action_tree[step] = []

        actions = self._thinking_step(messages)
        for action in actions:
            self._action_tree[step].append(action)
            tool_name = action.get("tool_name")
            justification = action.get("justification")

            thought_prompt = "You are now in the thinking stage. Choose a tool from the available tools and justify your choice."

            current_messages = messages + [HumanMessage(content=thought_prompt), HumanMessage(
                content=f"Chosen tool: {tool_name}, Justification: {justification}")]
            tool_results, tool_str_list = await self._action_step(current_messages)

            node = {
                "step": step,
                "action": ", ".join(tool_str_list),
                "justification": justification,
                "messages": current_messages,
                "children": []
            }
            parent_node["children"].append(node)
            await self.dfs(current_messages + tool_results,
                           step=step + 1, parent_node=node)

    async def arun(self, messages):
        print(f"Running SRE agent with model {self.model_name}")

        messages = messages + \
            [HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)]

        self._action_tree = {
            "step": -1,
            "action": "root",
            "messages": messages,
            "children": []
        }
        await self.dfs(messages, step=0, parent_node=self._action_tree)

        print("\n=== Action Tree ===")
        self._print_action_tree(self._action_tree)
        return 0

    def _print_action_tree(self, node, indent=0):
        prefix = "  " * indent

        action = node.get("action", "root")
        step = node.get("step", -1)
        justification = node.get("justification", "")

        print(f"{prefix}Step {step} | Action: {action}")
        # if justification:
        #     print(f"{prefix}  Reason: {justification}")

        for child in node.get("children", []):
            self._print_action_tree(child, indent + 1)

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
