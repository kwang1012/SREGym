from clients.sre.base_agent import BaseAgent, llm_inference
import json

from langchain_core.messages import HumanMessage

from clients.sre.utils import cprint


class HypoAgent(BaseAgent):
    def __init__(self, logs_dir, model_name):
        super().__init__(logs_dir, model_name)
        self.hypotheses = []
        self.evidence_history = []

    async def _initial_observation_step(self, messages):
        content = """Given the incident description, please examinate:

1. What are the status of the system components?
2. Are there any recent changes or deployments that could be relevant?

Generate tool calls to gather this initial information. 
You don't need to justify your choices at this step."""
        human_prompt = HumanMessage(content=content)
        messages.append(human_prompt)

        resp = llm_inference(model=self.model_name,
                             messages=messages)
        print(f"Initial observation tool calls: {resp.content}")

        messages.append(resp)
        new_messages = await self._action_step(messages)
        messages.extend(new_messages)

    def _planning_step(self, messages):
        content = """Based on the incident description and current observations:

1. Generate a set of competing hypotheses explaining the issue.
2. Assign an initial confidence score (0-1).
3. For each hypothesis, specify:
   - What evidence would increase confidence?
   - What evidence would falsify it?
   
Respond ONLY in this exact JSON format:
{
  "hypotheses": [
    {
      "id": integer,
      "description": string,
      "confidence": float,
      "evidence_supporting": [string],
      "evidence_falsifying": [string]
    }
  ]
}
Do not add extra keys.

Respond in structured JSON.
You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON."""
        human_prompt = HumanMessage(content=content)

        resp = llm_inference(model=self.model_name,
                             messages=messages + [human_prompt])
        try:
            hypotheses = json.loads(resp.content)  # type: ignore
        except json.JSONDecodeError:
            cprint(
                f"[ERROR] Failed to parse hypotheses JSON: {resp.content}", "red")
            raise

        self.hypotheses = hypotheses["hypotheses"]

    def _thinking_step(self, messages):
        content = f"""Given the current hypothesis set: {json.dumps(self.hypotheses)}

1. What more information do you need to gather to differentiate between these hypotheses?
2. What actions would maximally reduce the number of competing hypotheses?
3. Justify why these actions are chosen.

If you think you have enough confidence to submit a diagnosis, use the submit tool to submit your current most likely hypothesis and its justification.

Only respond with the chosen actions and justification."""
        messages.append(HumanMessage(content=content))
        return llm_inference(model=self.model_name, messages=messages)

    async def _action_step(self, messages):
        human_prompt = HumanMessage(
            content="Now generate tool calls according to your last chosen tools.")
        messages.append(human_prompt)
        action_message = llm_inference(
            model=self.model_name, messages=messages, tools=self.sync_tools + self.async_tools)

        new_messages = await self._handle_tool_calls(action_message)
        return new_messages

    def _evidence_extraction_step(self, messages, tool_messages):
        tool_outputs = [
            m.content for m in tool_messages
            if getattr(m, "tool_call_id", None) or m.type == "tool"
        ]

        content = f"""Given the following raw tool outputs:

{tool_outputs}

Extract structured evidence relevant to the current hypotheses:
{json.dumps(self.hypotheses)}

Respond ONLY in this exact JSON format:

{{
    "evidence": [
        {{
        "observation": string,
        "relevance": string
        }}
    ]
}}
Do not add extra keys.

Respond in structured JSON.
You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON."""

        messages.append(HumanMessage(content=content))
        resp = llm_inference(model=self.model_name, messages=messages)

        try:
            return json.loads(resp.content)["evidence"]  # type: ignore
        except json.JSONDecodeError:
            cprint(
                f"[ERROR] Failed to parse evidence JSON: {resp.content}", "red")
            raise

    def _reflect_step(self, messages, evidence):
        content = f"""Given:

Current hypotheses:
{json.dumps(self.hypotheses)}

New evidence:
{json.dumps(evidence)}

For each hypothesis:
- Does this evidence strongly support, weakly support, neutral, weakly contradict, or strongly contradict it?
- Estimate likelihood P(E | H)
- Estimate likelihood P(E | not H)

Respond ONLY in JSON:

{{
  "updates": [
    {{
      "id": integer,
      "p_e_given_h": float,
      "p_e_given_not_h": float
    }}
  ]
}}

You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON."""
        messages.append(HumanMessage(content=content))
        resp = llm_inference(model=self.model_name, messages=messages)
        try:
            return json.loads(resp.content)["updates"]  # type: ignore
        except json.JSONDecodeError:
            cprint(
                f"[ERROR] Failed to parse updates JSON: {resp.content}", "red")
            raise

    def _hypo_expansion_step(self, messages, evidence):
        if len(self.hypotheses) > 5:
            return  # Don't expand if there are more than 5 hypotheses
        content = f"""Current hypotheses:
{json.dumps(self.hypotheses, indent=2)}

New evidence:
{json.dumps(evidence, indent=2)}

The current hypothesis set may be incomplete.

1. Does the evidence suggest a new plausible explanation not covered?
2. If yes, propose up to {5 - len(self.hypotheses)} new hypotheses.
3. Ensure new hypotheses are mutually distinct from existing ones.
4. Assign initial confidence such that total confidence remains normalized.
5. Auto increment hypothesis IDs.

Respond ONLY in JSON:
{{
    "new_hypotheses": [
        {{
            "id": integer,
            "description": string,
            "confidence": float,
            "evidence_supporting": [string],
            "evidence_falsifying": [string]
        }}
    ]
}}
Do not add extra keys.

Respond in structured JSON.
You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON."""
        human_prompt = HumanMessage(content=content)

        resp = llm_inference(model=self.model_name,
                             messages=messages + [human_prompt])

        try:
            hypotheses = json.loads(resp.content)  # type: ignore
        except json.JSONDecodeError:
            cprint(
                f"[ERROR] Failed to parse new hypotheses JSON: {resp.content}", "red")
            raise

        self.hypotheses.extend(hypotheses["new_hypotheses"])

    def _bayesian_update(self, prior, p_e_h, p_e_not_h):
        numerator = prior * p_e_h
        denominator = numerator + (1 - prior) * p_e_not_h
        if denominator == 0:
            return prior
        return numerator / denominator

    def _apply_updates(self, updates):
        to_remove = []
        for (hypo, update) in zip(self.hypotheses, updates):
            old_confidence = hypo["confidence"]
            new_confidence = self._bayesian_update(
                old_confidence,
                update["p_e_given_h"],
                update["p_e_given_not_h"]
            )
            cprint(
                f"Hypothesis: {hypo['description']}, Confidence: {old_confidence:.2f} -> {new_confidence:.2f}", "magenta")
            hypo["confidence"] = new_confidence
            if new_confidence < 0.01:
                to_remove.append(hypo)
        for hypo in to_remove:
            self.hypotheses.remove(hypo)

    async def arun(self, messages):
        print(f"Running SRE agent with model {self.model_name}")

        messages = messages + \
            [HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)]

        await self._initial_observation_step(messages)
        prompt = HumanMessage(content="Based on the incident description and the tool call outputs, please summarize the situation and issues.")
        ai_message = llm_inference(model=self.model_name, messages=messages + [prompt])
        print(f"Initial summary: {ai_message.content}")
        return
        # self._planning_step(messages)

        step_count = 0
        while step_count < 10:
            cprint("Current Hypotheses:", "yellow")
            for hypo in self.hypotheses:
                cprint(
                    f"  {hypo['id']}: {hypo['description']} (confidence: {hypo['confidence']:.2f})", "yellow")
            local_messages = messages.copy()
            ai_message = self._thinking_step(local_messages)
            cprint(f"[THINKING]\n{ai_message.content}", "cyan")
            local_messages.append(ai_message)

            tool_messages = await self._action_step(local_messages)
            local_messages.extend(tool_messages)

            evidence = self._evidence_extraction_step(
                local_messages, tool_messages)
            self.evidence_history.extend(evidence)

            try:
                cprint(f"[EVIDENCE]", "green")
                for i, ev in enumerate(evidence):
                    cprint(f"{i}. {ev['observation']}", "green")
            except KeyError:
                cprint(f"[ERROR] Evidence: {evidence}", "red")

            updates = self._reflect_step(local_messages, evidence)
            self._apply_updates(updates)

            self._hypo_expansion_step(local_messages, evidence)
            step_count += 1

        return 0

    def get_usage_metrics(self):
        return {"tokens": 1000, "cost": 0.01}
