<div align="center">

<h1>A Unified Framework for Benchmarking SRE Agents</h1>

<!-- [🤖Overview](#🤖overview) |  -->
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

We have some Ansible playbooks to setup clusters on providers like [CloudLab](https://www.cloudlab.us/) and our own machines. Follow this [README](./scripts/ansible/README.md) to set up your own cluster.

<h2 id="⚙️usage">⚙️ Usage</h2>

SREGym can be used in the following ways:
- [Run agent on SREGym](#run-agent-on-SREGym)
- [Add new applications to SREGym](#how-to-add-new-applications-to-SREGym)
- [Add new problems to SREGym](#how-to-add-new-problems-to-SREGym)

### b) Emulated cluster
SREGym can be run on an emulated cluster using [kind](https://kind.sigs.k8s.io/) on your local machine. However, not all problems are supported.

```bash
# For x86 machines
kind create cluster --config kind/kind-config-x86.yaml

# For ARM machines
kind create cluster --config kind/kind-config-arm.yaml
```

If you're running into issues, consider building a Docker image for your machine by following this [README](kind/README.md). Please also open an issue.

When using kind, each node pulls images from docker hub independently, which can easily hit the rate limitation. You can uncomment `containerdConfigPatches` in the corresponding kind config file to pull images from our exclusive image registry without rate limiting.


### Run agent on SREGym

#### Run our demo agent "Stratus"
We have ported [the Stratus agent](https://anonymous.4open.science/r/stratus-agent/README.md) to SREGym as a demo agent.

To run the benchmark with Stratus as the demo agent, uncomment [this line](https://github.com/xlab-uiuc/SREGym/blob/180731a32a436fa4d369703998287d70a4e7f20e/main.py#L48C3-L48C3) in `main.py`.
It allows the benchmark to kick start the agent when the problem setup is done.

If you would like to run Stratus by itself, please take a look at [`driver.py`](https://github.com/xlab-uiuc/SREGym/blob/main/clients/stratus/stratus_agent/driver/driver.py)

We evaluated Stratus with `llama-3-3-70b-instruct`, here is a quick glance of the results:
- NOOP detection success rate: 34.7%
- Faulty system detection success rate: 89.8%
- Localization success rate: 16.3%
   - percentage of agent answer subsets ground truth: 18.4%
- Mitigation success rate: 22.4%

Detailed evaluation, with token usages and step counts, will be released soon.

##### Try other LLMs on "Stratus"
Stratus is implemented to be LLM-agnostic. You can feel free to try "Stratus" on the benchmark with different LLMs. You should configure the choice of LLM in `.env`.

Three kinds of LLM are supported:

1. LiteLLM-supported models
   Basically you can use the providers in [LiteLLM's list](https://docs.litellm.ai/docs/providers), including OpenAI, Anthropic and Gemini. (Note that not all of them are available due to the version issue)
2. IBM WatsonX 
3. other model not supported by LiteLLM (or self-deployed)
   **You custom provider or deployment platform must have OpenAI-compatible API.**

We have examples at [.env.example](https://github.com/xlab-uiuc/SREGym/blob/main/.env.example) for them respectively.

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



### How to add new applications to SREGym?

SREGym provides a default [list of applications](/SREGym/service/apps/) to evaluate agents for operations tasks. However, as a developer you can add new applications to SREGym and design problems around them.

> *Note*: for auto-deployment of some apps with K8S, we integrate Helm charts (you can also use `kubectl` to install as [HotelRes application](/SREGym/service/apps/hotelres.py)). More on Helm [here](https://helm.sh).

To add a new application to SREGym with Helm, you need to:

1. **Add application metadata**
    - Application metadata is a JSON object that describes the application.
    - Include *any* field such as the app's name, desc, namespace, etc.
    - We recommend also including a special `Helm Config` field, as follows:

        ```json
        "Helm Config": {
            "release_name": "<name for the Helm release to deploy>",
            "chart_path": "<path to the Helm chart of the app>",
            "namespace": "<K8S namespace where app should be deployed>"
        }
        ```
        > *Note*: The `Helm Config` is used by the conductor to auto-deploy your app when a problem associated with it is started.

        > *Note*: The conductor will auto-provide *all other* fields as context to the agent for any problem associated with this app.

    Create a JSON file with this metadata and save it in the [`metadata`](/SREGym/service/metadata) directory. For example the `social-network` app: [social-network.json](/SREGym/service/metadata/social-network.json)

2. **Add application class**

    Extend the base class in a new Python file in the [`apps`](/SREGym/service/apps) directory:

    ```python
    from SREGym.service.apps.base import Application

    class MyApp(Application):
        def __init__(self):
            super().__init__("<path to app metadata JSON>")
    ```

    The `Application` class provides a base implementation for the application. You can override methods as needed and add new ones to suit your application's requirements, but the base class should suffice for most applications.



### How to add new problems to SREGym?

Similar to applications, SREGym provides a default [list of problems](/SREGym/conductor/problems/registry.py) to evaluate agents. However, as a developer you can add new problems to SREGym and design them around your applications.

Each problem in SREGym has 3 components:
1. *Application*: The application on which the problem is based.
2. *Fault*: The fault being injected.
3. *Oracle*: How the problem is evaluated on the relevant tasks; detection, localization, and mitigation.

To add a new problem to SREGym, create a new Python file 
in the [`problems`](/SREGym/conductor/problems) directory, as follows:

1. **Setup**. Import your chosen application, the problem interface, and relevant oracles:

    ```python
    from SREGym.service.apps.myapp import MyApp
    from SREGym.conductor.oracles.detection import DetectionOracle
    from SREGym.conductor.oracles.localization import LocalizationOracle
    from SREGym.conductor.oracles.mitigation import MitigationOracle # or custom oracle
    from SREGym.conductor.problems.base import Problem
    from SREGym.utils.decorators import mark_fault_injected
    ```

2. **Define**. To define a problem, create a class that inherits from the `Problem` class, and defines 2 methods:, `inject_fault`, and `recover_fault`. Remember to setup your oracles as well!:

    ```python
    class MyProblem(Problem):
        def __init__(self):
            self.app = MyApp()
            self.faulty_service # Used for localization, can be None or a list
            # === Attach evaluation oracles ===
            self.localization_oracle = LocalizationOracle(problem=self, expected=[self.faulty_service])

            self.mitigation_oracle = MitigationOracle(problem=self)
        
        @mark_fault_injected
        def inject_fault(self)
            # <your fault injection logic here>
        
        @mark_fault_injected
        def recover_fault(self):
            # <your fault recovery logic here>
    ```

3. **Register**. Finally, add your problem to the conductor's registry [here](/SREGym/conductor/problems/registry.py).


See a full example of a problem [here](/SREGym/conductor/problems/target_port.py). 
<details>
  <summary>Click to show the description of the problem in detail</summary>

- **`inject_fault`**: Introduces a fault into the application. Use your own injector or SREGym's built-in one which you can also extend. E.g., a misconfig in the K8S layer:

    ```python
    from SREGym.generators.fault.inject_virtual import *

    inj = VirtualizationFaultInjector(testbed="<namespace>")
    inj.inject_fault(microservices=["<service-name>"], fault_type="misconfig")
    ```

    > Relevant Code: [SREGym/generators/fault](/SREGym/generators/fault)
</details>




<h2 id="📂project-structure">📂 Project Structure</h2>

<summary><code>SREGym</code></summary>
<details>
  <summary>Generators</summary>
  <pre>
  generators - the problem generators for SREGym, this is where the fault injection mechanisms are.
  ├── fault - the fault generator organized by fault injection level
  │   ├── base.py
  │   ├── inject_app.py
  │  ...
  │   └── inject_virtual.py
  └── workload - the workload generator organized by workload type
      └── wrk.py - wrk tool interface
  </pre>
</details>

<details>
  <summary>Conductor</summary>
  <pre>
  conductor
  ├── conductor.py - main execution engine coordinating agents and problems
  ├── problems - fault injection problems
  │   ├── base.py - abstract base class for problems
  │   ├── helpers.py - shared utilities (e.g., get_frontend_url)
  │   ├── noop.py - baseline problem with no fault injected
  │   └── registry.py - maps problem IDs to class instances
  ├── oracles - stage-wise evaluation of agent submissions
  │   ├── detection.py - checks if the agent correctly detects the presence of a fault
  │   ├── localization.py - evaluates if the agent correctly identifies the faulty components
  │   └── mitigation.py - validates whether the fault was fixed properly
  </pre>
</details>


<details>
  <summary>Service</summary>
  <pre>
  service
  ├── apps - interfaces/impl. of each app
  ├── helm.py - helm interface to interact with the cluster
  ├── kubectl.py - kubectl interface to interact with the cluster
  ├── shell.py - shell interface to interact with the cluster
  ├── metadata - metadata and configs for each apps
  └── telemetry - observability tools besides observer, e.g., in-memory log telemetry for the agent
  </pre>
</details>

<details>
  <summary>Observer</summary>
  <pre>
  observer
  ├── filebeat - Filebeat installation
  ├── logstash - Logstash installation
  ├── prometheus - Prometheus installation
  ├── log_api.py - API to store the log data on disk
  ├── metric_api.py - API to store the metrics data on disk
  └── trace_api.py - API to store the traces data on disk
  </pre>
</details>

<details>
  <summary>Utils</summary>
  <pre>
  ├── config.py - config parser
  ├── paths.py - paths and constants
  ├── session.py - SREGym session manager
  └── utils
      ├── actions.py - helpers for actions that agents can perform
      ├── cache.py - cache manager
      └── status.py - SREGym status, error, and warnings
  </pre>
</details>

<summary><code>cli.py</code>: A command line interface to interact with SREGym, e.g., used by human operators.</summary>

### [Tips]
If you are running SREGym using a proxy, beware of exporting the HTTP proxy as `172.17.0.1`. When creating the kind cluster, all the nodes in the cluster will inherit the proxy setting from the host environment and the Docker container. 

The `172.17.0.1` address is used to communicate with the host machine. For more details, refer to the official guide: [Configure Kind to Use a Proxy](https://kind.sigs.k8s.io/docs/user/quick-start/#configure-kind-to-use-a-proxy).

Additionally, Docker doesn't support SOCKS5 proxy directly. If you're using a SOCKS5 protocol to proxy, you may need to use [Privoxy](https://www.privoxy.org) to forward SOCKS5 to HTTP.

If you're running VLLM and the LLM agent locally, Privoxy will by default proxy `localhost`, which will cause errors. To avoid this issue, you should set the following environment variable:

```bash
export no_proxy=localhost
```

### Dashboard

You can run the dashboard manually, using the command.
```
python dashboard/dashboard_app.py
```
The dashboard will be hosted at localhost:11451 by default.


## License
Licensed under the [MIT](LICENSE.txt) license.
