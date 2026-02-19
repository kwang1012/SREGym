from clients.sre.base_agent import BaseAgent, llm_inference

from langchain_core.messages import HumanMessage

from clients.sre.utils import cprint


class ReactAgent(BaseAgent):

    def _thinking_step(self, messages):
        content = "You are now in the thinking stage. Choose a tool from the available tools and justify your choice."
        messages.append(HumanMessage(content=content))
        return llm_inference(model=self.model_name, messages=messages)

    def _action_step(self, messages):
        human_prompt = HumanMessage(
            content="Now generate a tool call according to your last chosen tool.")
        return llm_inference(model=self.model_name, messages=messages + [human_prompt], tools=self.sync_tools + self.async_tools)

    async def arun(self, messages):
        print(f"Running SRE agent with model {self.model_name}")

        messages = messages + \
            [HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)]

        step = 0
        while step < 10 and not self.submitted:
            ai_message = self._thinking_step(messages)
            cprint(f"[Thought]: {ai_message.content}", "cyan")

            messages.append(ai_message)
            ai_message = self._action_step(messages)
            tool_results = await self._handle_tool_calls(ai_message)

            messages.extend(tool_results)
            step += 1

        return 0

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
