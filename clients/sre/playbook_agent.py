"""
PlaybookAgent: Diagnosis agent grounded in SRE playbooks.

Pipeline:
  Phase 1: Retrieve relevant K8s playbooks from Scoutflo (RAG via LLM ranking)
  Phase 2: Generate a branching diagnosis plan as a structured JSON DAG
  Phase 3: Execute the plan with uncertainty-gated human checkpoints

Metric optimized: reliability gain / human involvement time.
"""

import json
from pathlib import Path

import questionary
from langchain_core.messages import HumanMessage

from clients.sre.base_agent import BaseAgent, llm_inference
from clients.sre.utils import cprint


# ---------------------------------------------------------------------------
# Playbook Retrieval
# ---------------------------------------------------------------------------

# Default location: <this file's directory>/playbooks/
# Run clients/sre/download_playbooks.py to populate it.
DEFAULT_PLAYBOOKS_DIR = Path(__file__).parent / "playbooks"


class PlaybookRetriever:
    """
    Retrieves K8s SRE playbooks from a local directory populated by
    download_playbooks.py.  Uses an LLM to rank entries by relevance to the
    observed symptoms.
    """

    def __init__(self, playbooks_dir: Path = DEFAULT_PLAYBOOKS_DIR):
        self.playbooks_dir = playbooks_dir
        self._index: list[dict] = []  # [{title, category, path}]

    def build_index(self) -> None:
        """Walk the local playbooks directory and build a flat index."""
        if not self.playbooks_dir.exists():
            cprint(
                f"[PLAYBOOK] Directory not found: {self.playbooks_dir}. "
                "Run clients/sre/download_playbooks.py first.",
                "yellow",
            )
            return

        for md_file in sorted(self.playbooks_dir.rglob("*.md")):
            self._index.append({
                "title": md_file.stem.replace("-", " "),
                "category": md_file.parent.name,
                "path": md_file,
            })
        cprint(
            f"[PLAYBOOK] Indexed {len(self._index)} local playbooks", "green")

    def retrieve(self, symptoms: str, model_name: str, top_k: int = 3) -> list[dict]:
        """
        Select the top_k most relevant playbooks for the given symptoms using
        an LLM to rank the local index.
        """
        if not self._index:
            self.build_index()

        if not self._index:
            cprint("[PLAYBOOK] Index is empty; skipping retrieval.", "yellow")
            return []

        index_lines = "\n".join(
            f"[{i}] {e['category']}/{e['title']}"
            for i, e in enumerate(self._index)
        )
        prompt = HumanMessage(content=(
            f"Given these Kubernetes incident symptoms:\n{symptoms}\n\n"
            f"Select the {top_k} most relevant playbooks from this index "
            f"(one line per entry):\n{index_lines}\n\n"
            f"Respond ONLY with a JSON array of integer indices, e.g. [3, 17, 42].\n"
            f"You ONLY output raw JSON. No markdown. No explanation."
        ))

        resp = llm_inference(model=model_name, messages=[prompt])
        try:
            indices = json.loads(resp.content.strip())
        except (json.JSONDecodeError, ValueError):
            cprint(
                "[PLAYBOOK] Could not parse LLM selection; using first entries.", "yellow")
            indices = list(range(min(top_k, len(self._index))))

        results = []
        for idx in indices[:top_k]:
            if 0 <= idx < len(self._index):
                entry = self._index[idx]
                content = entry["path"].read_text(encoding="utf-8")
                results.append({
                    "title": entry["title"],
                    "category": entry["category"],
                    "content": content,
                })
        return results


# ---------------------------------------------------------------------------
# PlaybookAgent
# ---------------------------------------------------------------------------

class PlaybookAgent(BaseAgent):
    """
    Three-phase diagnosis agent:
      1. Retrieve relevant SRE playbooks matching the observed symptoms.
      2. Generate a branching diagnosis plan grounded in those playbooks.
      3. Execute the plan step-by-step with uncertainty-gated human checkpoints.

    Human interventions are minimized by:
      - Branching logic (IF positive → step A, ELSE → step B) that skips
        irrelevant branches automatically.
      - Per-step uncertainty scoring: only pause when uncertainty > threshold.
      - Plan-level human approval before execution starts.
    """

    # Steps below this uncertainty score execute automatically.
    AUTO_THRESHOLD = 0.35
    # Steps above this threshold trigger a human checkpoint.
    HUMAN_THRESHOLD = 0.65

    def __init__(self, logs_dir, model_name, retriever: PlaybookRetriever | None = None):
        super().__init__(logs_dir, model_name)
        self.retriever = retriever or PlaybookRetriever()
        self.plan: dict = {}
        self.human_interventions: int = 0
        self.steps_executed: int = 0

    # ------------------------------------------------------------------
    # Phase 1: Initial observation + playbook retrieval
    # ------------------------------------------------------------------

    async def _initial_observation(self, messages: list) -> None:
        """Quick kubectl/metrics sweep to surface obvious symptoms before planning."""
        content = (
            "Run an initial observation of the system state. "
            "Check pod statuses, recent events, and any obvious anomalies. "
            "Be concise — the output will inform plan generation."
        )
        messages.append(HumanMessage(content=content))
        resp = llm_inference(
            model=self.model_name, messages=messages,
            tools=self.sync_tools + self.async_tools,
        )
        messages.append(resp)
        tool_results = await self._handle_tool_calls(resp)
        messages.extend(tool_results)

    def _summarize_observations(self, messages: list) -> str:
        """Ask the LLM to summarize what was observed into a symptoms string."""
        prompt = HumanMessage(content=(
            "Summarize the current system observations as a concise symptom description "
            "for use in playbook retrieval. Focus on: pod states, error messages, "
            "failing components, and any anomalous behaviour. Output plain text only."
        ))
        resp = llm_inference(model=self.model_name,
                             messages=messages + [prompt])
        return resp.content.strip()

    # ------------------------------------------------------------------
    # Phase 2: Plan generation
    # ------------------------------------------------------------------

    def _generate_plan(self, messages: list, playbooks: list[dict]) -> dict:
        """
        Use the LLM to produce a branching JSON diagnosis plan grounded in the
        retrieved playbooks.
        """
        playbook_text = ""
        for pb in playbooks:
            playbook_text += (
                f"\n--- Playbook: {pb['category']} / {pb['title']} ---\n"
                f"{pb['content']}\n"
            )

        tool_names = [t.name for t in self.sync_tools + self.async_tools]

        prompt = HumanMessage(content=f"""You are an SRE diagnosis planner for Kubernetes.
Using the incident context and the SRE playbook excerpts below,
generate a structured, branching diagnosis plan.

Relevant playbooks:
{playbook_text}

Rules:
- Steps must use one of these tools: {tool_names}
- Use next_if_positive / next_if_negative to branch; null means end.
- Order steps from most likely / cheapest to most specific expensive.

Respond ONLY in JSON:
{{
    "fault_hypotheses": [
        {{
            "category": string, 
            "description": string, 
            "confidence": float
        }}
    ],
    "playbook_references": [string],
    "steps":
        {{
            "id": integer,
            "description": string,
            "tool": string,
            "args": object,
            "expected_positive_signal": string,
            "next_if_positive": integer or null,
            "next_if_negative": integer or null,
            "uncertainty_note": string
        }}
    ]
}}
Do not add extra keys.

Respond in structured JSON.
You NEVER output markdown.
You NEVER output code fences.
You ONLY output raw JSON.""")

        resp = llm_inference(model=self.model_name,
                             messages=messages + [prompt])
        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            plan = json.loads(raw)
        except json.JSONDecodeError:
            cprint(
                f"[PLAN] JSON parse failed; raw output:\n{resp.content}", "red")
            raise

        cprint(f"[PLAN] {len(plan['steps'])} steps, "
               f"{len(plan['fault_hypotheses'])} hypotheses", "green")
        return plan

    def _print_plan(self, plan: dict) -> None:
        cprint("\n========== DIAGNOSIS PLAN ==========", "yellow")
        cprint("Hypotheses:", "yellow")
        for h in plan.get("fault_hypotheses", []):
            cprint(f"  [{h['confidence']:.0%}] {h['description']}", "yellow")
        cprint("\nPlaybooks referenced:", "yellow")
        for ref in plan.get("playbook_references", []):
            cprint(f"  - {ref}", "yellow")
        cprint("\nSteps:", "yellow")
        for s in plan.get("steps", []):
            branch = f"pos→{s['next_if_positive']} | neg→{s['next_if_negative']}"
            cprint(f"  [{s['id']}] {s['description']}", "yellow")
            cprint(f"       tool={s['tool']}  {branch}", "white")
        cprint("=====================================\n", "yellow")

    # ------------------------------------------------------------------
    # Phase 3: Plan execution
    # ------------------------------------------------------------------

    def _score_uncertainty(self, step: dict) -> float:
        """Score the uncertainty of a step using the LLM (0 = certain, 1 = very uncertain)."""
        note = step.get("uncertainty_note", "").strip()
        if not note:
            return 0.1

        prompt = HumanMessage(content=(
            f"Rate the uncertainty of this diagnosis step on a scale of 0.0 (certain) "
            f"to 1.0 (very uncertain).\n"
            f"Step: {step['description']}\n"
            f"Uncertainty note: {note}\n"
            f"Respond ONLY with a single float."
        ))
        resp = llm_inference(model=self.model_name, messages=[prompt])
        try:
            return max(0.0, min(1.0, float(resp.content.strip())))
        except ValueError:
            return 0.5

    async def _human_checkpoint(self, step: dict) -> bool:
        """
        Pause and ask the operator whether to proceed with this step.
        Returns True to proceed, False to skip (take negative branch).
        """
        self.human_interventions += 1

        style = questionary.Style([
            ("proceed", "fg:#00cd00 bold"),
            ("skip", "fg:#cd0000"),
            ("comment", "fg:#808080 italic"),
        ])

        choice = await questionary.select(
            (
                f"[Step {step['id']}] {step['description']}\n"
                f"  Tool : {step['tool']}({json.dumps(step['args'])})\n"
                f"  Note : {step.get('uncertainty_note', 'N/A')}\n"
                "Proceed with this step?"
            ),
            choices=[
                questionary.Choice(
                    title=[("class:proceed", "Proceed")], value="proceed"
                ),
                questionary.Choice(
                    title=[
                        ("class:skip", "Skip"),
                        ("class:comment", " (take negative branch)"),
                    ],
                    value="skip",
                ),
            ],
            style=style,
        ).ask_async()

        return choice == "proceed"

    async def _execute_step(self, step: dict, messages: list) -> str:
        """
        Execute a single plan step and return 'positive' or 'negative'
        based on whether the expected signal was observed.
        """
        messages.append(HumanMessage(content=(
            f"Execute step {step['id']}: {step['description']}\n"
            f"Use tool '{step['tool']}' with arguments: {json.dumps(step['args'])}\n"
            f"Expected positive signal: {step['expected_positive_signal']}"
        )))

        action = llm_inference(
            model=self.model_name, messages=messages,
            tools=self.sync_tools + self.async_tools,
        )
        messages.append(action)

        tool_results = await self._handle_tool_calls(action)
        messages.extend(tool_results)
        self.steps_executed += 1

        # Observe: did we see the expected positive signal?
        observe_resp = llm_inference(
            model=self.model_name,
            messages=messages + [HumanMessage(content=(
                f"Did the tool output confirm the expected positive signal: "
                f"'{step['expected_positive_signal']}'?\n"
                "Respond ONLY with 'positive' or 'negative'."
            ))],
        )
        outcome = "positive" if "positive" in observe_resp.content.lower() else "negative"
        return outcome

    async def _execute_plan(self, plan: dict, messages: list) -> None:
        steps = plan.get("steps", [])
        if not steps:
            cprint("[PLAN] No steps to execute.", "yellow")
            return

        step_map = {s["id"]: s for s in steps}
        current_id: int | None = steps[0]["id"]

        while current_id is not None and not self.submitted:
            step = step_map.get(current_id)
            if step is None:
                cprint(f"[PLAN] Step {current_id} not found; stopping.", "red")
                break

            cprint(f"\n[STEP {step['id']}] {step['description']}", "cyan")

            # Decide whether a human checkpoint is needed
            needs_human = step.get("human_checkpoint", False)
            if not needs_human:
                uncertainty = self._score_uncertainty(step)
                color = "green" if uncertainty < self.AUTO_THRESHOLD else (
                    "yellow" if uncertainty < self.HUMAN_THRESHOLD else "red"
                )
                cprint(f"  Uncertainty: {uncertainty:.2f}", color)
                if uncertainty >= self.HUMAN_THRESHOLD:
                    needs_human = True

            if needs_human:
                proceed = await self._human_checkpoint(step)
                if not proceed:
                    cprint(
                        "  [SKIPPED by operator — taking negative branch]", "yellow")
                    current_id = step.get("next_if_negative")
                    continue

            outcome = await self._execute_step(step, messages)
            color = "green" if outcome == "positive" else "red"
            cprint(f"  Outcome: {outcome}", color)

            current_id = (
                step.get("next_if_positive")
                if outcome == "positive"
                else step.get("next_if_negative")
            )

    async def _submit_diagnosis(self, messages: list) -> None:
        prompt = HumanMessage(content=(
            "Based on all evidence gathered during plan execution, submit a comprehensive "
            "diagnosis. Include:\n"
            "- Root cause(s) identified\n"
            "- Evidence supporting each finding\n"
            "- Hypotheses that were ruled out and why\n\n"
            "Use the submit tool to finalize your diagnosis."
        ))
        messages.append(prompt)
        action = llm_inference(
            model=self.model_name, messages=messages,
            tools=self.sync_tools + self.async_tools,
        )
        messages.append(action)
        await self._handle_tool_calls(action)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def arun(self, messages: list) -> int:
        cprint(
            f"[PlaybookAgent] Starting with model {self.model_name}", "green")

        messages = messages + [
            HumanMessage(
                content="Here are all the tools you can use:\n" + self.tool_descs)
        ]

        # Phase 1a: Initial observation
        cprint("\n[Phase 1] Running initial observations...", "blue")
        await self._initial_observation(messages)
        symptoms = self._summarize_observations(messages)
        cprint(f"[Phase 1] Symptoms summary:\n  {symptoms}", "blue")

        # Phase 1b: Retrieve relevant playbooks
        cprint("\n[Phase 1] Retrieving relevant playbooks...", "blue")
        playbooks = self.retriever.retrieve(symptoms, self.model_name, top_k=3)
        if playbooks:
            cprint(
                f"[Phase 1] Retrieved: {[pb['title'] for pb in playbooks]}", "blue")
        else:
            cprint(
                "[Phase 1] No playbooks retrieved; continuing without them.", "yellow")

        # Phase 2: Generate diagnosis plan
        cprint("\n[Phase 2] Generating diagnosis plan...", "blue")
        self.plan = self._generate_plan(messages, playbooks)
        self._print_plan(self.plan)

        # # Phase 3: Execute plan
        # cprint("\n[Phase 3] Executing diagnosis plan...", "blue")
        # await self._execute_plan(self.plan, messages)

        # # Final submission
        # if not self.submitted:
        #     await self._submit_diagnosis(messages)

        # cprint(
        #     f"\n[DONE] {self.steps_executed} steps executed, "
        #     f"{self.human_interventions} human interventions.",
        #     "green",
        # )

        with open("chat_history.txt", "w") as f:
            for message in messages:
                f.write(f"{message.type}: {message.content}\n")

        return 0

    def get_usage_metrics(self) -> dict:
        return {
            "tokens": 0,
            "cost": 0.0,
            "steps_executed": self.steps_executed,
            "human_interventions": self.human_interventions,
        }
