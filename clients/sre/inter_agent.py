import json

import questionary

from clients.sre.base_agent import BaseAgent, llm_inference

from langchain_core.messages import HumanMessage, ToolMessage

from clients.sre.utils import cprint


class InteractiveAgent(BaseAgent):

    def __init__(self, logs_dir, model_name):
        super().__init__(logs_dir, model_name)
        self.proposal_history = []

    def _analyze_uncertainty(self, proposal, alternatives):
        content = (
            "You are a analyze uncertainty agent. Your job is to analyze the uncertainty of the proposed direction/subtask by the generated alternative proposals."
            "Given: "
            "Proposed direction/subtask: {proposed_direction}"
            "Alternative proposals: {alternative_proposals}"
            "Propsal history: {proposal_history}"
            "Now analyze the uncertainty of the proposed direction/subtask"
            "Respond ONLY with the number in scale of 0 to 1, where 0 means no uncertainty and 1 means high uncertainty."
        ).format(proposed_direction=proposal,
                 alternative_proposals=alternatives,
                 proposal_history=self.proposal_history)
        ai_message = llm_inference(model=self.model_name, messages=[
                                   HumanMessage(content=content)])
        return float(ai_message.content.strip())

    async def _propose_step(self, messages):
        content = (
            f"Given the problem and the proposal history: {self.proposal_history}:"
            "You are now in the proposing stage. Propose a direction/subtask to solve the problem. You don't need to justify your choice."
            "Just output the proposed direction/subtask."
        )
        messages.append(HumanMessage(content=content))
        proposed_message = llm_inference(
            model=self.model_name, messages=messages)
        messages.append(proposed_message)

        cprint(f"[Proposed Task]: {proposed_message.content}", "green")
        content = (
            f"You just proposed the direction/subtask: {proposed_message.content}."
            "Now analyze the uncertainty of the proposed direction/subtask and provide the alternative proposals and justify how they are different from the original proposal in one sentence."
            """Respond ONLY in JSON:
            {
                "alternatives": [
                    {
                        "description": string,
                        "justification": string,
                    }
                ]
            }
            Do not add extra keys.

            Respond in structured JSON.
            You NEVER output markdown.
            You NEVER output code fences.
            You ONLY output raw JSON."""
        )
        alternatives_message = llm_inference(
            model=self.model_name, messages=messages + [HumanMessage(content=content)])

        alternatives = json.loads(alternatives_message.content)["alternatives"]

        uncertainty = self._analyze_uncertainty(
            proposed_message.content, alternatives_message.content)

        if uncertainty > 0.5:

            custom_style = questionary.Style([
                ('continue', 'fg:#00cd00 bold'),
                ('alternative', 'fg:#cdcd00'),
                ('comment', 'fg:#808080 italic'),
                ('highlighted', 'bold'),
            ])
            choice = await questionary.select(
                f"The uncertainty ({uncertainty}) is high. Do you want to continue with the current proposal?",
                choices=[
                    questionary.Choice(title=[("class:continue", "Continue")],
                                       value=proposed_message.content),
                    *[
                        questionary.Choice(title=[
                            ("class:alternative", alternative['description']),
                            ("class:comment",
                             f" #{alternative['justification']}")
                        ],
                            value=alternative['description'])
                        for alternative in alternatives
                    ]
                ],
                style=custom_style
            ).ask_async()

            if choice is not None and choice != proposed_message.content:
                cprint(
                    "You chose to switch to the alternative proposal:", "yellow")
                cprint(choice, "green")

                messages.append(HumanMessage(
                    content=f"User decided to switch to the alternative proposal: {choice}"))
                return choice
        return proposed_message.content

    def _thinking_step(self, messages):
        content = (
            "You are now in the thinking stage. Choose the tools from the available tools. You don't need to justify your choice."
        )
        messages.append(HumanMessage(content=content))
        return llm_inference(model=self.model_name, messages=messages)

    async def _action_step(self, messages) -> list[ToolMessage] | None:
        content = "Now generate the tool calls according to your last chosen tools."
        messages.append(HumanMessage(content=content))
        ai_message = llm_inference(
            model=self.model_name, messages=messages, tools=self.sync_tools + self.async_tools)
        messages.append(ai_message)
        tool_results = await self._handle_tool_calls(ai_message)
        for tool_call in ai_message.tool_calls:
            arg_list = [f"{key}={value}" for key,
                        value in tool_call["args"].items()]
            tools_str = f"- {tool_call['name']}({', '.join(arg_list)})"
            cprint(tools_str, "magenta")
            # TODO: summarize tool results and print them in a more readable way
        return tool_results

    async def _observe_step(self, messages):
        content = (
            "You are now in the observe stage. Observe the tool outputs and justify if you have done with the direction/subtask."
            "Output [COMPLETE] tag if you think the subtask is completed."
        )
        messages.append(HumanMessage(content=content))
        return llm_inference(model=self.model_name, messages=messages)

    async def _subtask_step(self, messages):
        content = "Given the proposed direction/subtask, your job is to complete the subtask. You can use the tools to help you complete the subtask."
        messages.append(HumanMessage(content=content))
        step = 0
        while step < 20:
            ai_message = self._thinking_step(messages)
            # cprint(f"[Thinking]: {ai_message.content}", "cyan")
            messages.append(ai_message)

            tool_results = await self._action_step(messages)
            messages.extend(tool_results)

            observe_message = await self._observe_step(messages)
            # cprint(f"[Observe]: {observe_message.content}", "magenta")
            messages.append(observe_message)

            if "[COMPLETE]" in observe_message.content:
                # cprint(f"Subtask completed!", "green")
                return
            step += 1
        cprint(f"Subtask failed with {step} iterations.", "red")

    async def arun(self, messages):
        print(f"Running Interactive agent with model {self.model_name}")

        messages = messages + \
            [HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)]

        self.loop_count = 0
        while self.loop_count < 10 and not self.submitted:
            proposal = await self._propose_step(messages)
            self.proposal_history.append(proposal)

            await self._subtask_step(messages)
            self.loop_count += 1

        with open(f"chat_history.txt", "w") as f:
            for message in messages:
                f.write(f"{message.type}: {message.content}\n")

        return 0

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
