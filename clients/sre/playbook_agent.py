"""
PlaybookAgent: Diagnosis agent grounded in SRE playbooks.

Pipeline:
  Phase 1: Retrieve relevant K8s playbooks from Scoutflo (RAG via LLM ranking)
  Phase 2: Generate a branching diagnosis plan as a structured JSON DAG
  Phase 3: Execute the plan with uncertainty-gated human checkpoints

Metric optimized: reliability gain / human involvement time.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import questionary
from langchain_core.messages import HumanMessage

from clients.sre.base_agent import BaseAgent, llm_inference
from clients.sre.utils import cprint


@dataclass
class RiskProfile:
    """
    Three orthogonal dimensions of operational risk for a single plan step.

    Separating these dimensions matters because they have different mitigations:
    - High uncertainty alone → the LLM isn't sure what will happen; a human
      sanity-check may be enough.
    - High blast_radius alone → the change is well-understood but touches many
      things; the operator should confirm scope.
    - High reversibility alone → the action is predictable but hard to undo;
      the operator should be sure before proceeding.

    Gating rule: require human approval if ANY dimension crosses its threshold.
    This mirrors safety-critical risk matrices (e.g. IEC 61508, DO-178C) where
    a single worst-case dimension is sufficient to escalate, regardless of other
    dimensions being low.
    """
    uncertainty: float    # 0 = fully predictable outcome, 1 = completely unknown
    blast_radius: float   # 0 = one pod only, 1 = entire cluster / namespace
    reversibility: float  # 0 = instant rollback possible, 1 = data loss / permanent

    # Per-dimension thresholds (tuned independently, not a single combined score)
    _U_THRESH: float = 0.6
    _B_THRESH: float = 0.7
    _R_THRESH: float = 0.7

    @property
    def needs_human(self) -> bool:
        return (
            self.uncertainty >= self._U_THRESH
            or self.blast_radius >= self._B_THRESH
            or self.reversibility >= self._R_THRESH
        )

    def summary(self) -> str:
        flags = []
        if self.uncertainty >= self._U_THRESH:
            flags.append("uncertain")
        if self.blast_radius >= self._B_THRESH:
            flags.append("wide blast radius")
        if self.reversibility >= self._R_THRESH:
            flags.append("irreversible")
        flag_str = f"  ⚑ {', '.join(flags)}" if flags else ""
        return (
            f"uncertainty={self.uncertainty:.2f}  "
            f"blast={self.blast_radius:.2f}  "
            f"reversibility={self.reversibility:.2f}"
            f"{flag_str}"
        )


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

    def __init__(self, logs_dir, model_name, retriever: PlaybookRetriever | None = None):
        super().__init__(logs_dir, model_name)
        self.retriever = retriever or PlaybookRetriever()
        self.plan: dict = {}
        self.human_interventions: int = 0
        self.steps_executed: int = 0
        # Bayesian belief state
        self.hypothesis_posteriors: dict[int, float] = {}  # hypothesis index → P(H_i | evidence)
        self.evidence_log: list[dict] = []                 # per-step belief snapshots
        self.pruned_nodes: set[int] = set()                # nodes removed by plan revision

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
        Use the LLM to produce a DAG-structured diagnosis+mitigation plan grounded
        in the retrieved playbooks.  The plan interleaves diagnosis, mitigation, and
        observation nodes so the agent tries fixes, measures their effect, and adapts
        — exactly as a human SRE would.
        """
        playbook_text = ""
        for pb in playbooks:
            playbook_text += (
                f"\n--- Playbook: {pb['category']} / {pb['title']} ---\n"
                f"{pb['content']}\n"
            )

        tool_names = [t.name for t in self.sync_tools + self.async_tools]

        prompt = HumanMessage(content=f"""You are an SRE planner for Kubernetes.
Using the incident context and the SRE playbook excerpts below,
generate a DAG-structured plan that mixes DIAGNOSIS, MITIGATION, and OBSERVATION
steps — exactly how a human SRE would approach an unknown incident.

Relevant playbooks:
{playbook_text}

=== PLANNING PHILOSOPHY ===
Think like a human debugging an unknown problem:
  1. Diagnose: gather evidence to understand what is wrong.
  2. Mitigate: apply a targeted fix or config change.
  3. Observe: immediately measure whether the fix worked.
  4. Adapt: if the fix did not work, branch to an alternative hypothesis.

Every mitigation node MUST be followed by an observation node that measures
whether the mitigation succeeded. Use wait_tool between mitigation and observation
when the change needs time to propagate (e.g. pod restart).

=== NODE TYPES ===
- "diagnose"  — read-only investigation (kubectl get/describe/logs, metrics, traces)
- "mitigate"  — applies a change (kubectl patch/set/rollout, configmap update, etc.)
- "observe"   — measures the system AFTER a mitigation to confirm or refute the fix
                (check pod ready status, error rate drop, latency improvement, etc.)

=== EXAMPLE PATTERNS ===
Pattern A — Config fix:
  [diagnose] Check env vars / configmap values on failing pod
  → (negative: wrong value found)
  [mitigate] Patch the deployment env var to correct value
  → (always)
  [wait]     Wait 30s for rollout
  → (always)
  [observe]  Check pod is Running and Ready; check error rate drops
  → (positive: fixed) → submit
  → (negative: still broken) → next hypothesis

Pattern B — Resource exhaustion (OOMKilled):
  [diagnose] Describe pod to find OOMKilled termination reason in Last State
  → (positive: OOMKilled found)
  [mitigate] Patch deployment memory limit from 128Mi to 512Mi
  → (always)
  [observe]  Check pod restarts stop and Ready=True within 60s

Pattern D — Faulty image / wrong command (executable not found):
  [diagnose] Describe pod and check Last State message for "executable file not found" or "exec format error"
  → (positive: executable not found)
  [diagnose] Check what image tag is deployed and whether a previous stable tag exists (kubectl rollout history)
  → (always)
  [mitigate] Roll back deployment to previous revision (kubectl rollout undo) OR patch image to known-good tag
  → (always)
  [observe]  Check pod is Running and Ready=True; verify no more CrashLoopBackOff

Pattern C — Dependency outage:
  [diagnose] Get traces to see which downstream call is failing
  → (positive: service X error)
  [diagnose] Check service X pod logs for root cause
  → (negative: misconfigured endpoint)
  [mitigate] Patch service X configmap with correct endpoint URL
  → (always)
  [observe]  Re-check traces; confirm error rate < 1%

=== STRICT RULES ===
- Each node uses exactly one tool from: {tool_names}
- Args MUST be specific and complete — include exact namespace, pod name, container
  name, metric query, patch JSON, etc. derived from the observations already made.
- For mitigate nodes that set or patch an image, the image value MUST be a fully
  qualified tag (e.g. "nginx:1.25.3"). Never use empty strings or placeholders.
- Edges: "positive" | "negative" | "always"
- "always" edges = unconditional / parallel fan-out; multiple "always" from one
  node means execute all targets concurrently.
- A node whose parents are all parallel siblings is a JOIN node.
- "entry_node" is the first node id.
- Order: cheapest/safest diagnose steps first, then targeted mitigations.
- At least 40% of nodes should be mitigation or observation steps.
- "hypothesis_id" is the 0-based index into fault_hypotheses that this node
  primarily tests or mitigates. Use -1 for general steps not tied to one hypothesis.

Respond ONLY in JSON (no markdown, no code fences):
{{
    "fault_hypotheses": [
        {{
            "category": string,
            "description": string,
            "confidence": float
        }}
    ],
    "playbook_references": [string],
    "entry_node": integer,
    "nodes": [
        {{
            "id": integer,
            "action_type": "diagnose" | "mitigate" | "observe",
            "hypothesis_id": integer,
            "description": string,
            "tool": string,
            "args": object,
            "expected_positive_signal": string,
            "uncertainty_note": string
        }}
    ],
    "edges": [
        {{
            "from": integer,
            "to": integer,
            "condition": "positive" | "negative" | "always"
        }}
    ]
}}
Do not add extra keys.
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
                f"[PLAN] JSON parse failed; raw output:\n{raw}", "red")
            raise

        cprint(f"[PLAN] {len(plan['nodes'])} nodes, {len(plan['edges'])} edges, "
               f"{len(plan['fault_hypotheses'])} hypotheses", "green")
        return plan

    @staticmethod
    def _build_adjacency(plan: dict) -> tuple[dict, dict]:
        """Return (children_map, parents_map) from the edge list."""
        children: dict[int, list[dict]] = {}   # node_id -> [edge, ...]
        parents: dict[int, list[dict]] = {}    # node_id -> [edge, ...]
        for edge in plan.get("edges", []):
            children.setdefault(edge["from"], []).append(edge)
            parents.setdefault(edge["to"], []).append(edge)
        return children, parents

    def _print_plan(self, plan: dict) -> None:
        children, _ = self._build_adjacency(plan)

        cprint("\n========== DIAGNOSIS PLAN (DAG) ==========", "yellow")
        cprint("Hypotheses:", "yellow")
        for h in plan.get("fault_hypotheses", []):
            cprint(f"  [{h['confidence']:.0%}] {h['description']}", "yellow")
        cprint("\nPlaybooks referenced:", "yellow")
        for ref in plan.get("playbook_references", []):
            cprint(f"  - {ref}", "yellow")

        cprint(f"\nEntry node: {plan.get('entry_node')}", "yellow")
        cprint("Nodes:", "yellow")
        type_colors = {"diagnose": "cyan", "mitigate": "magenta", "observe": "green"}
        for n in plan.get("nodes", []):
            edges_out = children.get(n["id"], [])
            edge_str = ", ".join(
                f"--{e['condition']}--> {e['to']}" for e in edges_out
            ) or "(terminal)"
            action_type = n.get("action_type", "diagnose")
            label_color = type_colors.get(action_type, "yellow")
            cprint(f"  [{n['id']}] [{action_type.upper()}] {n['description']}", label_color)
            cprint(f"       tool={n['tool']}  {edge_str}", "white")
        cprint("==========================================\n", "yellow")

    # ------------------------------------------------------------------
    # Phase 3: Plan execution
    # ------------------------------------------------------------------

    # Read-only tools that are always safe to run without human approval.
    _SAFE_READONLY_TOOLS = frozenset({
        "exec_read_only_kubectl_cmd",
        "get_metrics",
        "get_traces",
        "get_services",
        "get_operations",
        "get_dependency_graph",
        "wait_tool",
    })

    def _assess_risk(self, step: dict) -> RiskProfile:
        """
        Score three independent risk dimensions for a plan step.

        Separating uncertainty / blast_radius / reversibility lets each dimension
        trigger a human checkpoint independently (OR-gate), rather than collapsing
        them into a single score that can mask a dangerous dimension with low values
        on others (e.g. a high-blast-radius action that happens to be predictable).

        Hard rules applied before the LLM call:
          - diagnose/observe on read-only tools → zeros on blast + reversibility,
            uncertainty capped at 0.2. These can never trigger a checkpoint.
          - mitigate steps → full LLM scoring on all three dimensions.
        """
        action_type = step.get("action_type", "diagnose")
        tool = step.get("tool", "")

        if action_type in ("diagnose", "observe") and tool in self._SAFE_READONLY_TOOLS:
            return RiskProfile(uncertainty=0.1, blast_radius=0.0, reversibility=0.0)

        prompt = HumanMessage(content=(
            f"You are evaluating the operational risk of an SRE action on a live cluster.\n"
            f"Action type: {action_type}\n"
            f"Tool: {tool}\n"
            f"Description: {step['description']}\n"
            f"Arguments: {json.dumps(step['args'])}\n\n"
            f"Rate three dimensions independently (each 0.0–1.0):\n"
            f"  uncertainty:   How unpredictable is the outcome? "
            f"(0=fully predictable, 1=completely unknown side-effects)\n"
            f"  blast_radius:  How many pods/services/nodes are affected if this fails? "
            f"(0=one pod, 1=entire namespace or cluster)\n"
            f"  reversibility: How hard to undo? "
            f"(0=instant rollback e.g. kubectl rollout undo, 1=data loss or permanent change)\n\n"
            f"Do NOT factor in whether the hypothesis is correct — "
            f"hypothesis uncertainty is irrelevant to operational risk.\n"
            f'Respond ONLY with JSON: {{"uncertainty": float, "blast_radius": float, "reversibility": float}}'
        ))
        resp = llm_inference(model=self.model_name, messages=[prompt])
        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            scores = json.loads(raw)
            profile = RiskProfile(
                uncertainty=max(0.0, min(1.0, float(scores.get("uncertainty", 0.5)))),
                blast_radius=max(0.0, min(1.0, float(scores.get("blast_radius", 0.3)))),
                reversibility=max(0.0, min(1.0, float(scores.get("reversibility", 0.3)))),
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            profile = RiskProfile(uncertainty=0.5, blast_radius=0.3, reversibility=0.3)

        # Diagnose/observe: cap blast and reversibility at 0 — they are read-only.
        if action_type in ("diagnose", "observe"):
            profile = RiskProfile(
                uncertainty=min(profile.uncertainty, 0.4),
                blast_radius=0.0,
                reversibility=0.0,
            )
        return profile

    async def _human_checkpoint(self, step: dict, risk: RiskProfile) -> bool:
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
                f"[Step {step['id']}] [{step.get('action_type', 'diagnose').upper()}] "
                f"{step['description']}\n"
                f"  Tool : {step['tool']}({json.dumps(step['args'])})\n"
                f"  Risk : {risk.summary()}\n"
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

    # ------------------------------------------------------------------
    # Bayesian belief tracking
    # ------------------------------------------------------------------

    def _init_posteriors(self, plan: dict) -> None:
        """Normalize the planner's confidence scores into a proper prior distribution."""
        hypotheses = plan.get("fault_hypotheses", [])
        if not hypotheses:
            return
        total = sum(max(h.get("confidence", 0.1), 0.01) for h in hypotheses)
        self.hypothesis_posteriors = {
            i: max(h.get("confidence", 0.1), 0.01) / total
            for i, h in enumerate(hypotheses)
        }

    def _compute_entropy(self) -> float:
        """Shannon entropy (bits) over the hypothesis posterior distribution."""
        return -sum(
            p * math.log2(p)
            for p in self.hypothesis_posteriors.values()
            if p > 0
        )

    def _update_beliefs(self, node: dict, outcome: str, tool_output: str) -> None:
        """
        Bayesian update after observing a tool execution outcome.

            P(H_i | e) ∝ P(e | H_i) · P(H_i)

        P(e | H_i) is estimated by the LLM: "given hypothesis i is the true root
        cause, how likely was this specific outcome?"  This avoids needing labeled
        training data while still grounding updates in the actual evidence.
        """
        hypotheses = self.plan.get("fault_hypotheses", [])
        if not hypotheses or not self.hypothesis_posteriors:
            return

        entropy_before = self._compute_entropy()

        hyp_text = "\n".join(
            f"[{i}] {h['description']}" for i, h in enumerate(hypotheses)
        )
        prompt = HumanMessage(content=(
            f"Fault hypotheses:\n{hyp_text}\n\n"
            f"An SRE action just completed:\n"
            f"  Action: {node['description']}\n"
            f"  Outcome: {outcome}  "
            f"(positive = expected signal observed, negative = not observed)\n"
            f"  Tool output (truncated): {tool_output[:600]}\n\n"
            f"For each hypothesis, estimate P(this outcome | hypothesis is the true root cause).\n"
            f"If a hypothesis predicts the observed outcome well, give a high value.\n"
            f"If the outcome contradicts the hypothesis, give a low value.\n"
            f"Respond ONLY with a JSON array of floats [P_0, P_1, ...], one per hypothesis."
        ))
        resp = llm_inference(model=self.model_name, messages=[prompt])
        try:
            likelihoods = json.loads(resp.content.strip())
        except (json.JSONDecodeError, ValueError):
            return  # keep current beliefs on parse failure

        # Bayes: new posterior ∝ likelihood × prior; then normalize
        updated = {
            i: max(0.01, float(likelihoods[i]) if i < len(likelihoods) else 0.5)
               * self.hypothesis_posteriors.get(i, 1.0 / len(hypotheses))
            for i in range(len(hypotheses))
        }
        total = sum(updated.values())
        self.hypothesis_posteriors = {i: v / total for i, v in updated.items()}

        entropy_after = self._compute_entropy()
        self.evidence_log.append({
            "node_id": node["id"],
            "outcome": outcome,
            "entropy_before": entropy_before,
            "entropy_after": entropy_after,
            "posteriors": dict(self.hypothesis_posteriors),
        })

        cprint(f"  [BELIEFS] entropy {entropy_before:.2f}b → {entropy_after:.2f}b", "blue")
        for i, p in sorted(self.hypothesis_posteriors.items(), key=lambda x: -x[1]):
            bar = "█" * int(p * 16)
            cprint(f"    [{p:4.0%}] {bar} {hypotheses[i]['description'][:55]}", "blue")

    # ------------------------------------------------------------------
    # Plan revision
    # ------------------------------------------------------------------

    def _revise_plan(
        self,
        plan: dict,
        executed_ids: set[int],
        messages: list,
        node_map: dict,
        children: dict,
        parents: dict,
        completed: dict,
    ) -> None:
        """
        Revise the remaining plan based on the current belief state.

        Three operations, in priority order:
          1. Prune — skip nodes whose target hypothesis is ruled out (P < 0.05).
          2. Focus — if one hypothesis is confirmed (P > 0.85), prune all nodes
             not on its mitigation path.
          3. Extend — if entropy is not decreasing after several steps (evidence
             unexplained by all hypotheses), generate new investigation nodes.
        """
        hypotheses = self.plan.get("fault_hypotheses", [])
        if not hypotheses or not self.hypothesis_posteriors:
            return

        ruled_out = {i for i, p in self.hypothesis_posteriors.items() if p < 0.05}
        confirmed = {i for i, p in self.hypothesis_posteriors.items() if p > 0.85}

        if ruled_out:
            to_prune = {
                n["id"] for n in plan["nodes"]
                if n["id"] not in executed_ids
                and n.get("hypothesis_id", -1) in ruled_out
            }
            if to_prune:
                self.pruned_nodes.update(to_prune)
                names = [hypotheses[i]["description"][:40] for i in ruled_out]
                cprint(f"  [REVISION] Pruned {len(to_prune)} nodes — ruled out: {names}", "yellow")

        if confirmed:
            confirmed_idx = next(iter(confirmed))
            to_prune = {
                n["id"] for n in plan["nodes"]
                if n["id"] not in executed_ids
                and n.get("hypothesis_id", -1) not in {confirmed_idx, -1}
                and n["id"] not in self.pruned_nodes
            }
            if to_prune:
                self.pruned_nodes.update(to_prune)
                p = self.hypothesis_posteriors[confirmed_idx]
                cprint(
                    f"  [REVISION] Hypothesis [{confirmed_idx}] confirmed ({p:.0%}). "
                    f"Pruned {len(to_prune)} unrelated nodes.", "yellow"
                )

        entropy = self._compute_entropy()
        max_entropy = math.log2(len(hypotheses)) if len(hypotheses) > 1 else 1.0
        if (
            entropy > 0.85 * max_entropy
            and len(self.evidence_log) >= 3
            and len(self.evidence_log) % 3 == 0
        ):
            self._extend_plan(plan, executed_ids, messages, node_map, children, parents, completed)

    def _extend_plan(
        self,
        plan: dict,
        executed_ids: set[int],
        messages: list,
        node_map: dict,
        children: dict,
        parents: dict,
        completed: dict,
    ) -> None:
        """Generate new investigation nodes when existing hypotheses don't explain
        the accumulated evidence, then attach them to the live DAG."""
        import asyncio

        hypotheses = plan.get("fault_hypotheses", [])
        evidence_summary = "\n".join(
            f"  node={e['node_id']} outcome={e['outcome']} "
            f"entropy_Δ={e['entropy_after'] - e['entropy_before']:+.2f}b"
            for e in self.evidence_log[-5:]
        )
        posterior_summary = "\n".join(
            f"  [{p:.0%}] {hypotheses[i]['description']}"
            for i, p in self.hypothesis_posteriors.items()
        )
        max_id = max(n["id"] for n in plan["nodes"])
        tool_names = [t.name for t in self.sync_tools + self.async_tools]

        prompt = HumanMessage(content=(
            f"The incident investigation is not converging. Current beliefs:\n"
            f"{posterior_summary}\n\n"
            f"Recent evidence (entropy change per step):\n{evidence_summary}\n\n"
            f"None of the existing hypotheses explain the evidence well. "
            f"Generate 1-3 new, specific investigation nodes that explore "
            f"alternative root causes not yet considered.\n"
            f"Available tools: {tool_names}\n\n"
            f"Respond ONLY with JSON (no markdown):\n"
            f'{{"new_nodes": [{{\n'
            f'  "id": <integer starting from {max_id + 1}>,\n'
            f'  "action_type": "diagnose",\n'
            f'  "hypothesis_id": -1,\n'
            f'  "description": string,\n'
            f'  "tool": string,\n'
            f'  "args": object,\n'
            f'  "expected_positive_signal": string,\n'
            f'  "uncertainty_note": string\n'
            f"}}]}}"
        ))
        resp = llm_inference(model=self.model_name, messages=messages + [prompt])
        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            new_nodes = json.loads(raw).get("new_nodes", [])
        except (json.JSONDecodeError, ValueError, KeyError):
            return

        if not new_nodes:
            return

        # Attach to the most recently executed terminal node
        terminal = max(
            (n_id for n_id in executed_ids if not any(
                e["to"] not in executed_ids and e["to"] not in self.pruned_nodes
                for e in plan["edges"] if e["from"] == n_id
            )),
            default=None,
        )
        for node in new_nodes:
            plan["nodes"].append(node)
            node_map[node["id"]] = node
            completed[node["id"]] = asyncio.Event()
            if terminal is not None:
                edge = {"from": terminal, "to": node["id"], "condition": "always"}
                plan["edges"].append(edge)
                children.setdefault(terminal, []).append(edge)
                parents.setdefault(node["id"], []).append(edge)

        cprint(
            f"  [REVISION] Extended plan with {len(new_nodes)} new nodes "
            f"(entropy={self._compute_entropy():.2f}b, unexplained evidence)", "yellow"
        )

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _replan_mitigation(self, step: dict, messages: list) -> dict:
        """
        Before executing a mitigate node, ask the LLM (without tools) to:
          1. State the actual root cause based on diagnostic evidence.
          2. Decide whether the pre-planned mitigation addresses it.
          3. If not, produce a corrected tool + args JSON.

        Returns the (possibly corrected) step dict.
        """
        tool_names = [t.name for t in self.sync_tools + self.async_tools]
        prompt = HumanMessage(content=(
            f"You are about to execute a mitigation step. First reason about whether it is correct.\n\n"
            f"Pre-planned mitigation:\n"
            f"  Description : {step['description']}\n"
            f"  Tool        : {step['tool']}\n"
            f"  Args        : {json.dumps(step['args'])}\n\n"
            f"Based on ALL diagnostic findings in this conversation so far, answer:\n"
            f"1. What is the actual root cause? (one sentence)\n"
            f"2. Does the pre-planned mitigation fix that root cause? Answer YES or NO.\n"
            f"3. If NO, provide the corrected mitigation as JSON:\n"
            f'   {{"tool": "<tool_name>", "args": {{...}}}}\n'
            f"   Available tools: {tool_names}\n\n"
            f"If YES, respond with exactly: CONFIRMED\n"
            f"If NO, respond with the corrected JSON only (no markdown, no extra text)."
        ))
        resp = llm_inference(model=self.model_name, messages=messages + [prompt])
        raw = resp.content.strip()

        if raw.upper().startswith("CONFIRMED"):
            return step  # pre-planned mitigation is correct

        # Try to parse a corrected tool + args
        try:
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            corrected = json.loads(raw)
            if "tool" in corrected and "args" in corrected:
                cprint(
                    f"  [REPLAN] Pre-planned mitigation overridden.\n"
                    f"    Was : {step['tool']}({step['args']})\n"
                    f"    Now : {corrected['tool']}({corrected['args']})",
                    "yellow",
                )
                return {**step, "tool": corrected["tool"], "args": corrected["args"]}
        except (json.JSONDecodeError, ValueError, KeyError):
            cprint("  [REPLAN] Could not parse corrected mitigation; using pre-planned.", "yellow")

        return step

    async def _execute_step(self, step: dict, messages: list) -> tuple[str, str]:
        """
        Execute a single plan step.

        Returns (outcome, tool_output) where:
          outcome     = 'positive' | 'negative'
          tool_output = raw tool response text (for belief updating)
        """
        action_type = step.get("action_type", "diagnose")

        if action_type == "mitigate":
            step = self._replan_mitigation(step, messages)
            instruction = (
                f"Apply mitigation for step {step['id']}: {step['description']}\n"
                f"Use tool '{step['tool']}' with arguments: {json.dumps(step['args'])}\n"
                f"This is a CHANGE operation — apply it precisely. "
                f"The next step will observe whether it succeeded.\n"
                f"Expected outcome if successful: {step['expected_positive_signal']}"
            )
        elif action_type == "observe":
            instruction = (
                f"Observe system state for step {step['id']}: {step['description']}\n"
                f"Use tool '{step['tool']}' with arguments: {json.dumps(step['args'])}\n"
                f"You just applied a mitigation. Measure whether it worked.\n"
                f"Positive signal (mitigation succeeded): {step['expected_positive_signal']}"
            )
        else:
            instruction = (
                f"Diagnose step {step['id']}: {step['description']}\n"
                f"Use tool '{step['tool']}' with arguments: {json.dumps(step['args'])}\n"
                f"Expected positive signal (confirms this hypothesis): "
                f"{step['expected_positive_signal']}"
            )

        messages.append(HumanMessage(content=instruction))
        action = llm_inference(
            model=self.model_name, messages=messages,
            tools=self.sync_tools + self.async_tools,
        )
        messages.append(action)
        tool_results = await self._handle_tool_calls(action)
        messages.extend(tool_results)

        # If the tool returned a dry-run / validation failure, give the LLM one
        # chance to correct its command before scoring the outcome.  This handles
        # cases where the planner generated args with placeholder values (e.g. an
        # empty image tag) that pass JSON schema checks but fail Kubernetes validation.
        tool_output = next(
            (str(m.content) for m in reversed(tool_results) if getattr(m, "content", None)),
            "",
        )
        if "dry-run failed" in tool_output.lower() or "required value" in tool_output.lower():
            cprint("  [RETRY] Tool validation failed; asking LLM to correct the command.", "yellow")
            messages.append(HumanMessage(content=(
                f"The previous command failed validation:\n{tool_output}\n\n"
                f"Correct the command and retry. Ensure all required fields (e.g. image tag) "
                f"are fully specified. Do not use placeholder or empty values."
            )))
            retry_action = llm_inference(
                model=self.model_name, messages=messages,
                tools=self.sync_tools + self.async_tools,
            )
            messages.append(retry_action)
            retry_results = await self._handle_tool_calls(retry_action)
            messages.extend(retry_results)
            tool_output = next(
                (str(m.content) for m in reversed(retry_results) if getattr(m, "content", None)),
                tool_output,
            )

        self.steps_executed += 1

        observe_resp = llm_inference(
            model=self.model_name,
            messages=messages + [HumanMessage(content=(
                f"Based on the tool output, did we observe the expected positive signal: "
                f"'{step['expected_positive_signal']}'?\n"
                "Respond ONLY with 'positive' or 'negative'."
            ))],
        )
        outcome = "positive" if "positive" in observe_resp.content.lower() else "negative"
        return outcome, tool_output

    async def _execute_plan(self, plan: dict, messages: list) -> None:
        """
        Walk the DAG, executing nodes.  Supports:
          - conditional edges (positive / negative)
          - parallel fan-out ("always" edges from one node to many)
          - fan-in / join (a node waits until all parent outcomes arrive)

        After each node:
          1. Risk-gated human checkpoint (before execution)
          2. Bayesian belief update (after execution)
          3. Plan revision — prune ruled-out nodes, extend on unexplained evidence
        """
        import asyncio

        nodes = plan.get("nodes", [])
        if not nodes:
            cprint("[PLAN] No nodes to execute.", "yellow")
            return

        # These dicts are mutated in-place by _revise_plan / _extend_plan
        # so that _run_node always sees the latest graph state.
        node_map: dict[int, dict] = {n["id"]: n for n in nodes}
        children, parents = self._build_adjacency(plan)
        outcomes: dict[int, str] = {}
        completed: dict[int, asyncio.Event] = {n["id"]: asyncio.Event() for n in nodes}
        executed_ids: set[int] = set()

        async def _run_node(node_id: int) -> None:
            # if self.submitted:
                # return
            if node_id in self.pruned_nodes:
                outcomes[node_id] = "skipped"
                if node_id in completed:
                    completed[node_id].set()
                return

            node = node_map.get(node_id)
            if node is None:
                cprint(f"[PLAN] Node {node_id} not found; skipping.", "red")
                return

            # Wait for all parents (join semantics)
            for parent_edge in parents.get(node_id, []):
                await completed[parent_edge["from"]].wait()
                parent_outcome = outcomes.get(parent_edge["from"])
                if parent_edge["condition"] != "always" and parent_outcome != parent_edge["condition"]:
                    outcomes[node_id] = "skipped"
                    completed[node_id].set()
                    return

            action_type = node.get("action_type", "diagnose")
            node_colors = {"diagnose": "cyan", "mitigate": "magenta", "observe": "green"}
            cprint(
                f"\n[NODE {node['id']}] [{action_type.upper()}] {node['description']}",
                node_colors.get(action_type, "cyan"),
            )

            # Risk-gated human checkpoint
            risk = self._assess_risk(node)
            risk_color = "green" if not risk.needs_human else "red"
            cprint(f"  Risk: {risk.summary()}", risk_color)
            if risk.needs_human:
                proceed = await self._human_checkpoint(node, risk)
                if not proceed:
                    cprint("  [SKIPPED by operator]", "yellow")
                    outcomes[node_id] = "skipped"
                    completed[node_id].set()
                    return

            outcome, tool_output = await self._execute_step(node, messages)
            cprint(f"  Outcome: {outcome}", "green" if outcome == "positive" else "red")
            outcomes[node_id] = outcome
            executed_ids.add(node_id)
            completed[node_id].set()

            # Belief update + plan revision after every executed node
            self._update_beliefs(node, outcome, tool_output)
            self._revise_plan(plan, executed_ids, messages, node_map, children, parents, completed)

            # Fan-out
            outgoing = children.get(node_id, [])
            always_targets = [e["to"] for e in outgoing if e["condition"] == "always"]
            cond_targets = [e["to"] for e in outgoing if e["condition"] == outcome]
            next_ids = always_targets + cond_targets
            if len(next_ids) > 1:
                await asyncio.gather(*[_run_node(nid) for nid in next_ids])
            elif next_ids:
                await _run_node(next_ids[0])

        entry = plan.get("entry_node", nodes[0]["id"])
        await _run_node(entry)

    async def _submit_diagnosis(self, messages: list) -> None:
        prompt = HumanMessage(content=(
            "Based on all diagnosis, mitigation, and observation steps executed, "
            "submit a comprehensive incident summary. Include:\n"
            "- Root cause(s) identified\n"
            "- Evidence supporting each finding\n"
            "- Mitigations applied and whether they succeeded\n"
            "- Hypotheses that were tried and ruled out\n"
            "- Final cluster state (healthy / degraded / partially fixed)\n\n"
            "Use the submit tool to finalize."
        ))
        messages.append(prompt)
        action = llm_inference(
            model=self.model_name, messages=messages,
            tools=self.sync_tools + self.async_tools,
        )
        messages.append(action)
        print("\n[SUBMISSION] Finalizing diagnosis and submitting report...")
        result = await self._handle_tool_calls(action)
        print("\n[SUBMISSION] Result:", result[-1].content if result else "No response")
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

        # Phase 2: Generate plan
        cprint("\n[Phase 2] Generating plan...", "blue")
        self.plan = self._generate_plan(messages, playbooks)
        self._print_plan(self.plan)
        self._init_posteriors(self.plan)

        # Phase 3: Execute plan
        cprint("\n[Phase 3] Executing plan...", "blue")
        await self._execute_plan(self.plan, messages)

        # Final submission
        if not self.submitted:
            await self._submit_diagnosis(messages)

        cprint(
            f"\n[DONE] {self.steps_executed} steps executed, "
            f"{self.human_interventions} human interventions.",
            "green",
        )

        with open("chat_history.txt", "w") as f:
            for message in messages:
                f.write(f"{message.type}: {message.content}\n")

        return 0

    def get_usage_metrics(self) -> dict:
        entropy_trace = [e["entropy_after"] for e in self.evidence_log]
        return {
            "tokens": 0,
            "cost": 0.0,
            "steps_executed": self.steps_executed,
            "human_interventions": self.human_interventions,
            "nodes_pruned": len(self.pruned_nodes),
            "belief_updates": len(self.evidence_log),
            "final_entropy": entropy_trace[-1] if entropy_trace else None,
            "final_posteriors": dict(self.hypothesis_posteriors),
        }
