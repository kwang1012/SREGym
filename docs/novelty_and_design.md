# Novelty Analysis & Systematic Design

## The Precise Novelty Claim

The closest prior works are:
- **MAI-DxO** (Microsoft, 2025) — information-theoretic test selection, but clinical medicine only
- **BED-LLM** (arXiv 2508.21184) — Bayesian experimental design with LLMs, but domain-general
- **CaRT** (arXiv 2510.08517) — agents that know when to defer, but no formal belief state
- **STRATUS / ITBench** — strongest SRE baselines, but purely reactive with no belief tracking

**No published system combines these in Kubernetes/SRE.** The claim is:

> *We present the first SRE agent that maintains an explicit posterior distribution over fault hypotheses and uses it as a unified mechanism for (1) ordering diagnostic actions by expected information gain, (2) triggering plan revision when hypotheses are confirmed or ruled out, and (3) gating human escalation on operational risk rather than budget exhaustion.*

---

## Related Work

| System | Venue | What it does | Limitation |
|--------|-------|-------------|------------|
| AIOpsLab | MLSys 2025 | Benchmark: microservice envs, fault injection, telemetry, evaluation | Framework only; no agent |
| STRATUS | NeurIPS 2025 | Multi-agent cloud reliability; ≥1.5× over prior agents on AIOpsLab | Reactive ReAct loop; no belief state |
| ITBench SRE Agent | ICML 2025 | Diagnoses K8s failures, traces root causes, implements remediations | Single-shot; no replanning criterion |
| KubeIntellect | arXiv 2025 | Modular LLM-orchestrated K8s management | No hypothesis tracking |
| Plan-and-Act | arXiv 2503.09572 | Separate Planner/Executor LLMs; replanning after each action | Replanning triggered by observation change, not belief threshold |
| BED-LLM | arXiv 2508.21184 | Bayesian experimental design with LLMs | Domain-general; not applied to infrastructure |
| CaRT | arXiv 2510.08517 | Agents learn to defer when uncertain | Learned scalar; no formal belief state; not SRE |
| MAI-DxO | Microsoft 2025 | Panel-of-physicians sim; test selection by expected info gain; 80% accuracy, 70% cost reduction | Clinical medicine only |
| Sequential Diagnosis / SDBench | arXiv 2506.22405 | Benchmark for stepwise diagnostic reasoning under cost constraints | Medical domain |

---

## Three Contributions with Independent Metrics

### Contribution 1: Bayesian Hypothesis Tracking as a Unified Belief State

**What it does.** After each tool execution, update `P(H_i | evidence)` via Bayes' rule, using the LLM to estimate the likelihood term `P(evidence | H_i)`:

```
P(H_i | e) ∝ P(e | H_i) · P(H_i)
```

This gives a proper posterior distribution over fault hypotheses that updates coherently with each observation, without requiring labeled training data.

**Why it's novel.** Every prior SRE agent is stateless with respect to hypotheses — outcomes only affect the next immediate edge. Here, all future decisions (ordering, pruning, escalation) condition on the full posterior history. The LLM-estimated likelihood term is borrowed from BED-LLM but applied for the first time to operational diagnosis.

**Metrics:**

| Metric | Definition | How to measure |
|--------|-----------|----------------|
| **Posterior calibration** | Does `P(H_true)` increase monotonically? | Record posterior of ground-truth hypothesis after each step; plot mean ± std across incidents |
| **Entropy reduction rate** | Information gained per step: `ΔH / steps_executed` | `(H_initial − H_final) / steps_executed` in bits |
| **Convergence step** | Step at which `P(H_true) > 0.85` | Median convergence step vs. no-belief-tracking baseline |

**Ablation baseline:** Agent with belief updating disabled (uniform prior, no Bayes updates). Compare entropy reduction rate and convergence step.

---

### Contribution 2: Belief-Driven Plan Revision (Prune + Extend)

**What it does.** Three operations triggered by the posterior:

1. **Prune** — skip nodes targeting hypotheses with `P < 0.05` (ruled out by evidence)
2. **Focus** — when `P(H_i) > 0.85`, prune all nodes not on hypothesis *i*'s mitigation path
3. **Extend** — when entropy remains `> 85%` of maximum after ≥3 steps, generate new investigation nodes for alternative root causes not in the original plan

**Why it's novel.** Plan-and-Act (arXiv 2503.09572) does replanning, but triggers it on raw observation changes with no formal criterion. CostBench shows uncontrolled replanning degrades performance; our entropy-gated trigger is a principled solution to that instability problem. The extend operation addresses a gap not present in any prior SRE system: recovery when the true fault was not anticipated by the initial planner.

**Metrics:**

| Metric | Definition | How to measure |
|--------|-----------|----------------|
| **Steps saved by pruning** | `nodes_pruned / len(original_plan)` | `len(pruned_nodes) / len(plan['nodes'])` per run |
| **MTTR** | Time from fault injection to `n_submit_tool` | Benchmark clock; compare with/without revision |
| **Wasted mitigations** | Mitigations on wrong hypothesis | `mitigate` nodes executed where `hypothesis_id ≠ ground_truth_id` |
| **Novel-hypothesis recovery rate** | % of runs where `_extend_plan` fired and extension contained true root cause | Manual annotation of extended node content |

**Ablation:** Three variants: (a) full system, (b) no pruning, (c) no extension. Report all 4 metrics per variant.

---

### Contribution 3: Multi-Dimensional Operational Risk Gating for Human Escalation

**What it does.** Replace a single uncertainty score with a `RiskProfile` of three independent dimensions, OR-gated — any dimension crossing its threshold triggers a human checkpoint:

| Dimension | Meaning | Threshold |
|-----------|---------|-----------|
| `uncertainty` | How unpredictable is the outcome? | 0.60 |
| `blast_radius` | How many services/pods affected if it fails? | 0.70 |
| `reversibility` | How hard to roll back? | 0.70 |

Hard rules enforce read-only tools (`kubectl get/describe/logs`, metrics, traces) can never trigger a checkpoint regardless of LLM output — they have zero blast radius and zero reversibility by construction.

**Why it's novel.** CaRT learns a scalar deferral signal. OpenAI's governance framework recommends risk-based escalation but gives no implementation. No SRE agent has published a formal evaluation of escalation quality. The critical distinction — separating epistemic uncertainty (does the LLM know what will happen?) from operational risk (is the action reversible / wide-blast?) — is not present in any SRE/AIOps literature.

The OR-gate design mirrors safety-critical risk matrices (IEC 61508, DO-178C): a single worst-case dimension is sufficient to escalate, regardless of other dimensions being low. This prevents a high-blast-radius action from avoiding escalation simply because its outcome is predictable.

**Metrics:**

| Metric | Definition | How to measure |
|--------|-----------|----------------|
| **Checkpoint precision** | Risky steps gated / total checkpoints triggered | Log each checkpoint with `risk`; annotate whether it was warranted |
| **Checkpoint recall** | Risky steps gated / total risky steps executed | Label which steps were "risky" post-hoc from ground truth |
| **False positive rate** | Checkpoints on safe steps | Steps where `action_type ∈ {diagnose, observe}` that triggered checkpoint |
| **Human interventions per incident** | `human_interventions` counter | Compare: (a) no checkpoints, (b) single-scalar, (c) multi-dim `RiskProfile` |

**Baseline comparison:** Show precision/recall tradeoff across three gating strategies on a 2D plot.

---

## Experimental Design

### Benchmark

SREGym (this repo) — provides ground-truth fault types, enabling computation of whether `H_true` was in the initial hypotheses and whether the agent converged on it.

### Fault Coverage

At minimum, cover the 3 canonical fault categories (chosen to exercise different risk profiles):

| Fault type | Risk profile of fix |
|-----------|-------------------|
| Wrong image tag | High blast radius (deployment rollout), reversible |
| OOM / resource limit | Medium blast radius, reversible |
| Config / env var misconfiguration | Low blast radius, reversible |

### Sample Size

30+ incidents per configuration. Report **median + IQR**, not mean — incident resolution time is right-skewed.

### Baselines

| Baseline | What it isolates |
|----------|-----------------|
| **ReAct** (no plan) | Value of structured planning |
| **Plan-and-execute, no revision** | Value of belief-driven revision |
| **Plan-and-execute, scalar uncertainty gating** | Value of multi-dim risk gating |
| **Full system, no Bayesian update** | Isolated contribution of belief tracking |
| **Full system** | Proposed method |

### Primary hypothesis to prove

> The full system achieves lower MTTR and fewer human interventions than all baselines, without increasing the number of failed mitigations (mitigations applied to the wrong hypothesis).

---

## Missing Implementation for Full Measurement

Two things not yet in `get_usage_metrics` that are required for a complete experiment:

1. **Per-step execution log** — record `(step_id, action_type, hypothesis_id, outcome, risk_profile, was_human_checkpoint)` for every executed node
2. **Ground-truth hypothesis mapping** — SREGym provides fault type at runtime; need a mapping from fault type → hypothesis index to compute posterior calibration automatically

---

## References

- AIOpsLab — [Semantic Scholar](https://www.semanticscholar.org/paper/AIOpsLab:-A-Holistic-Framework-to-evaluate-AI-for-Chen-Shetty/c8bbe39285da8e427250cc2a80da8f8f2f92b8cb)
- STRATUS — [Paper PDF](https://yinfangchen.github.io/assets/pdf/stratus_paper.pdf)
- ITBench SRE Agent — [GitHub](https://github.com/itbench-hub/To-be-Archived-ITBench-SRE-Agent)
- KubeIntellect — [arXiv 2509.02449](https://arxiv.org/html/2509.02449v1)
- Plan-and-Act — [arXiv 2503.09572](https://arxiv.org/html/2503.09572v3)
- Learning When to Plan — [arXiv 2509.03581](https://arxiv.org/html/2509.03581)
- Are LLM Belief Updates Bayesian? — [arXiv 2507.17951](https://arxiv.org/abs/2507.17951)
- BED-LLM — [arXiv 2508.21184](https://arxiv.org/pdf/2508.21184)
- CaRT — [arXiv 2510.08517](https://arxiv.org/html/2510.08517v1)
- OpenAI Governing Agentic AI — [PDF](https://cdn.openai.com/papers/practices-for-governing-agentic-ai-systems.pdf)
- Sequential Diagnosis with LMs / SDBench — [arXiv 2506.22405](https://arxiv.org/pdf/2506.22405)
- Belief-Driven Multi-Agent via Bayesian Nash Equilibrium — [arXiv 2506.08292](https://arxiv.org/pdf/2506.08292)
- MAI-DxO (Microsoft) — [Microsoft Research](https://www.microsoft.com/en-us/research/project/mai-dxo/)
- Awesome LLM-AIOps — [GitHub](https://github.com/Jun-jie-Huang/awesome-LLM-AIOps)
