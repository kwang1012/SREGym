<div align="center">

<h1>SREGym: A Unified Framework for Benchmarking SRE Agents</h1>

[🚀Quick Start](#🚀quickstart) |
[📦Installation](#📦installation) |
[⚙️Usage](#⚙️usage) |
[📂Project Structure](#📂project-structure) |
[![Slack](https://img.shields.io/badge/-Slack-4A154B?style=flat-square&logo=slack&logoColor=white)](https://join.slack.com/t/SREGym/shared_invite/zt-3gvqxpkpc-RvCUcyBEMvzvXaQS9KtS_w)
</div>


SREGym is a unified framework to enable the design, development, and evaluation of autonomous AIOps agents and, additionally, serve the purpose of building reproducible, standardized, interoperable, and scalable benchmarks. SREGym offers a Kubernetes-based experiment environment that deploy cloud applications, inject faults, generate workloads, and export telemetry data, while orchestrating these components with programmable interfaces. 

Moreover, SREGym provides a benchmark suite with a set of problems to evaluate AIOps agents in an interactive environment. The benchmark suite can be easily extended to meet user- and application-specific needs.

### Problems
See a complete problem list with descriptions [here](https://docs.google.com/spreadsheets/d/1FGIeLNcKsHjrZGQ_VJcQRGl6oTmYyzjW0_ve5tfM_eg/edit?usp=sharing).

<h2 id="📦installation">📦 Installation</h2>

### Requirements
- Python >= 3.12
- [Helm](https://helm.sh/)
- [brew](https://docs.brew.sh/Homebrew-and-Python)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [uv](https://github.com/astral-sh/uv)

### Recommendations
- [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) to test MCP tools.
- [k9s](https://k9scli.io/) to observe the cluster.

```bash
git clone --recurse-submodules https://github.com/xlab-uiuc/SREGym
cd SREGym
uv sync
uv run pre-commit install
```

<h2 id="🚀quickstart">🚀 Setup Your Cluster</h2>

Choose either a) or b) to set up your cluster and then proceed to the next steps.

### a) Kubernetes Cluster (Recommended)
SREGym supports any remote kubernetes cluster that your `kubectl` context is set to, whether it's a cluster from a cloud provider or one you build yourself. 

We have an Ansible playbook to setup clusters on providers like [CloudLab](https://www.cloudlab.us/) and our own machines. Follow this [README](./scripts/ansible/README.md) to set up your own cluster.

### b) Emulated cluster
SREGym can be run on an emulated cluster using [kind](https://kind.sigs.k8s.io/) on your local machine. However, not all problems are supported.

```bash
# For x86 machines
kind create cluster --config kind/kind-config-x86.yaml

# For ARM machines
kind create cluster --config kind/kind-config-arm.yaml
```

<h2 id="⚙️usage">⚙️ Usage</h2>

SREGym can be used in the following ways:
- [Evaluating agents on SREGym](#run-agent-on-SREGym)
- [Add new problems to SREGym](#how-to-add-new-problems-to-SREGym)
- [Add new applications to SREGym](#how-to-add-new-applications-to-SREGym)

### Evaluate agent on SREGym

#### Run our demo agent "Stratus"
We have ported [the Stratus agent](https://github.com/xlab-uiuc/stratus) to SREGym as a demo agent.

To start, first create your `.env`:
```bash
mv .env.example .env
```

Then, select your model and paste your API key.

Finally:
```bash
python main.py
```

#### Run your agent on SREGym
SREGym makes it extremely easy to develop and evaluate your agents, thanks to its decoupled design. 
There are at most 4 phases in each problem of SREGym:
1. **NOOP Detection**: The cluster has no incidents. The agent should detect no incident in the cluster. 
   
   **Expected submission**: "Yes" or "No" to indicate incident.
2. **Incident Detection**: The cluster has a running incident. The agent should detect an incident in the cluster.

   **Expected submission**: "Yes" or "No" to indicate incident.
3. **Fault Localization**: The agent should localize where the incident originates.

   **Expected submission**: A list of strings representing the faulty components in the cluster.
4. **Incident Mitigation**: The agent should try to mitigate the incident and bring the cluster back online.

   **Expected submission**: empty submission to indicate that the agent is satisfied with the cluster.

To configure what tasks you want the conductor to run on a particular problem, edit its entry (identified by problem_id) in [`tasklist.yml`](./SREGym/conductor/tasklist.yml). Specify any task(s) of `detection`, `localization` or `mitigation` (in this order) to tell the conductor to run them. `noop` is automatically assumed to be the starting stage of a problem. If there is no entry for a problem, the conductor will assume that all tasks are to be run for that one. `localization` and `mitigation` may be skipped if there is corresponding oracle attached to the problem. Example:

```yaml
k8s_target_port-misconfig:
  - detection
  - mitigation
```

The entry above tells the conductor to start at `noop` then run `detection` and `mitigation` when starting `k8s_target_port-misconfig`, skipping `localization`.

The benchmark is driven by agent submissions. The benchmark expects the agent to submit a `POST` HTTP API call to the `http://localhost:8000/submit` HTTP endpoint.
Each submission pushes the benchmark to the next phase.

Therefore, if you would like to test your agent on SREGym, simply run [`main.py`](https://github.com/xlab-uiuc/SREGym/blob/main/main.py) to start the benchmark,
then instruct your agent to submit answers with HTTP API call in each phase of the benchmark problem.

SREGym also provides a suite of MCP Servers that support basic cluster management needs.

1. **Jaeger MCP Server**: Allows the agent to query the Jaeger tracing service in the cluster.
2. **Prometheus MCP Server**: Allows the agent to query metrics traced by Prometheus in the cluster.
3. **Kubernetes MCP Server**: Allows the agent to execute `kubectl` commands against the k8s cluster.
4. **Submission MCP Server**: Allows the agent to submit answers to the benchmark.

The Stratus agent in [`clients/stratus`](https://github.com/xlab-uiuc/SREGym/tree/main/clients/stratus)
demonstrates basic usages of these MCP servers in an agent.

### Dashboard

You can run the dashboard manually, using the command.
```
python dashboard/dashboard_app.py
```
The dashboard will be hosted at localhost:11451 by default.

## Acknowledgements
Thank you to [Laude Institute](https://www.laude.org/) for supporting this project.


## License
Licensed under the [MIT](LICENSE.txt) license.
