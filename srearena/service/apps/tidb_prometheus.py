import subprocess
import shlex
import json
import time
from typing import Optional, List

PROM_NAMESPACE = "observe"
PROM_RELEASE = "prometheus"
PROM_CHART = "prometheus-community/prometheus"
PROM_SVC_NAME = "prometheus-server"
PROM_PORT = 9090
DESIRED_NODEPORT = 32000

FLEETCAST_NS = "fleetcast"
FLEETCAST_DEP = "fleetcast-satellite-app-backend"
FLEETCAST_METRICS_PORT = "5000"

PROM_VALUES_PATH = "/Users/lilygniedz/Documents/SREArena/SREArena/aiopslab-applications/FleetCast/prometheus/prometheus.yaml"

RETRY_SECS = 60
SLEEP_BETWEEN = 3

def run_cmd(cmd, shell=False, check=True, capture=False) -> str:
    print("Running:", cmd if isinstance(cmd, str) else " ".join(cmd))
    if capture:
        if shell:
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
        else:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out.strip()
    else:
        subprocess.run(cmd, shell=shell, check=check)
        return ""

def ns_exists(ns: str) -> bool:
    return subprocess.run(["kubectl", "get", "ns", ns], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def ensure_ns(ns: str):
    if not ns_exists(ns):
        run_cmd(["kubectl", "create", "ns", ns])

def helm_repo_setup():
    try:
        run_cmd(["helm", "repo", "add", "prometheus-community", "https://prometheus-community.github.io/helm-charts"])
    except subprocess.CalledProcessError:
        pass
    run_cmd(["helm", "repo", "update"])

def delete_pending_extra_pvcs():
    out = run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","pvc","-o","json"], capture=True)
    data = json.loads(out)
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        phase = item.get("status", {}).get("phase")
        if name != "prometheus-server" and phase == "Pending":
            run_cmd(["kubectl","-n",PROM_NAMESPACE,"delete","pvc",name], check=False)

def helm_apply_values():
    pvc = json.loads(run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","pvc","prometheus-server","-o","json"], capture=True))
    pv_name = pvc["spec"]["volumeName"]
    pv = json.loads(run_cmd(["kubectl","get","pv",pv_name,"-o","json"], capture=True))
    # try to read the hostname from PV nodeAffinity; fall back to first Ready node
    node = None
    na = pv.get("spec",{}).get("nodeAffinity",{}).get("required",{}).get("nodeSelectorTerms",[])
    for term in na:
        for me in term.get("matchExpressions",[]):
            if me.get("key") in ("kubernetes.io/hostname","kubernetes.io/arch","topology.kubernetes.io/zone"):
                vals = me.get("values") or []
                if vals and me["key"]=="kubernetes.io/hostname":
                    node = vals[0]
                    break
        if node: break
    if not node:
        # fallback: pick node where the PV was last used (if recorded) or any Ready node
        try:
            node = run_cmd(["kubectl","get","nodes","-o","jsonpath={.items[0].metadata.name}"], capture=True)
        except subprocess.CalledProcessError:
            node = ""

    # clean up any stray pending PVCs created by older values
    try:
        out = run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","pvc","-o","json"], capture=True)
        for item in json.loads(out).get("items",[]):
            if item["metadata"]["name"] != "prometheus-server" and item.get("status",{}).get("phase")=="Pending":
                run_cmd(["kubectl","-n",PROM_NAMESPACE,"delete","pvc",item["metadata"]["name"]], check=False)
    except Exception:
        pass

    set_flags = [
        "--set","server.persistentVolume.existingClaim=prometheus-server",
        "--set","server.service.type=NodePort",
        "--set",f"server.service.nodePort={DESIRED_NODEPORT}",
        "--set",f"server.service.port={PROM_PORT}",
        "--set",f"server.service.targetPort={PROM_PORT}",
    ]
    if node:
        set_flags += ["--set",f"server.nodeSelector.kubernetes\\.io/hostname={node}"]

    run_cmd([
        "helm","upgrade",PROM_RELEASE,PROM_CHART,
        "-n",PROM_NAMESPACE,"--install","--reuse-values",
        "-f",PROM_VALUES_PATH,
        *set_flags
    ])


def wait_for_prometheus_ready(timeout_seconds=300):
    try:
        run_cmd(["kubectl","-n",PROM_NAMESPACE,"rollout","status","deploy/"+PROM_SVC_NAME,"--timeout",f"{timeout_seconds}s"])
        return
    except subprocess.CalledProcessError:
        pass
    deadline = time.time() + timeout_seconds
    selectors = [
        "app=prometheus,component=server",
        f"app.kubernetes.io/instance={PROM_RELEASE},app.kubernetes.io/name=prometheus,component=server",
        f"app.kubernetes.io/instance={PROM_RELEASE},app=prometheus",
    ]
    while time.time() < deadline:
        ready_any = False
        for sel in selectors:
            out = run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","pods","-l",sel,"-o","json"],capture=True,check=False)
            if not out:
                continue
            data = json.loads(out)
            items = data.get("items", [])
            if not items:
                continue
            all_ready = True
            for item in items:
                conds = item.get("status",{}).get("conditions",[])
                if not any(c.get("type")=="Ready" and c.get("status")=="True" for c in conds):
                    all_ready = False
                    break
            if all_ready:
                ready_any = True
                break
        if ready_any:
            return
        time.sleep(2)
    run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","deploy","prometheus-server","-o","wide"], check=False)
    run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","rs,po","-l","app.kubernetes.io/instance="+PROM_RELEASE,"-o","wide"], check=False)
    run_cmd(["kubectl","-n",PROM_NAMESPACE,"get","events","--sort-by=.lastTimestamp"], check=False)
    raise RuntimeError("Timed out waiting for Prometheus to be Ready")

def get_service_json(name: str) -> dict:
    out = run_cmd(["kubectl", "-n", PROM_NAMESPACE, "get", "svc", name, "-o", "json"], capture=True)
    return json.loads(out)

def ensure_nodeport(service_name: str, target_port: int, node_port: int):
    run_cmd([
        "kubectl", "-n", PROM_NAMESPACE, "patch", "svc", service_name,
        "--type=json",
        "-p=[{\"op\":\"replace\",\"path\":\"/spec/type\",\"value\":\"NodePort\"}]"
    ], check=False)
    svc = get_service_json(service_name)
    ports = svc.get("spec", {}).get("ports", [])
    target_idx = None
    for i, p in enumerate(ports):
        tp = p.get("targetPort", p.get("port"))
        if (isinstance(tp, int) and tp == target_port) or (isinstance(tp, str) and str(tp) == str(target_port)):
            target_idx = i
            break
    if target_idx is None:
        for i, p in enumerate(ports):
            if p.get("port") == target_port:
                target_idx = i
                break
    if target_idx is None:
        raise RuntimeError(f"Could not find a port entry for {target_port} on service {service_name}")
    patch_path = f"/spec/ports/{target_idx}/nodePort"
    try:
        run_cmd([
            "kubectl", "-n", PROM_NAMESPACE, "patch", "svc", service_name,
            "--type=json",
            f"-p=[{{\"op\":\"replace\",\"path\":\"{patch_path}\",\"value\":{node_port}}}]"
        ])
    except subprocess.CalledProcessError:
        run_cmd([
            "kubectl", "-n", PROM_NAMESPACE, "patch", "svc", service_name,
            "--type=json",
            f"-p=[{{\"op\":\"add\",\"path\":\"{patch_path}\",\"value\":{node_port}}}]"
        ])
    svc2 = get_service_json(service_name)
    np = svc2["spec"]["ports"][target_idx].get("nodePort")
    print(f"Service {service_name} now NodePort={np} (wanted {node_port})")

def list_ready_node_ips() -> List[str]:
    out = run_cmd(["kubectl", "get", "nodes", "-o", "json"], capture=True)
    data = json.loads(out)
    ips = []
    for n in data.get("items", []):
        conds = n.get("status", {}).get("conditions", [])
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
        if not ready:
            continue
        addr = n.get("status", {}).get("addresses", [])
        external = [a["address"] for a in addr if a["type"] == "ExternalIP"]
        internal = [a["address"] for a in addr if a["type"] == "InternalIP"]
        if external:
            ips.append(external[0])
        elif internal:
            ips.append(internal[0])
    return ips

def find_first_reachable_prom_url(node_port: int, timeout=RETRY_SECS) -> Optional[str]:
    ips = list_ready_node_ips()
    print(f"Candidate node IPs: {ips}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ip in ips:
            url = f"http://{ip}:{node_port}"
            try:
                run_cmd(f"curl -sS {shlex.quote(url)}/-/ready", shell=True, check=True)
                print(f"Prometheus is reachable at {url}")
                return url
            except subprocess.CalledProcessError:
                continue
        time.sleep(SLEEP_BETWEEN)
    return None

def find_dep_namespace(dep_name: str) -> Optional[str]:
    if ns_exists(FLEETCAST_NS):
        if subprocess.run(["kubectl", "-n", FLEETCAST_NS, "get", "deploy", dep_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return FLEETCAST_NS
    try:
        ns = run_cmd(
            "kubectl get deploy -A -o jsonpath='{range .items[*]}{.metadata.namespace} {.metadata.name}{\"\\n\"}{end}' | awk '$2==\"%s\"{print $1; exit}'" % dep_name,
            shell=True, capture=True
        )
        return ns if ns else None
    except subprocess.CalledProcessError:
        return None

def annotate_fleetcast_and_rollout():
    ns = FLEETCAST_NS if ns_exists(FLEETCAST_NS) else find_dep_namespace(FLEETCAST_DEP)
    if not ns:
        print(f"WARNING: Could not find namespace for deployment '{FLEETCAST_DEP}'. Skip annotations.")
        return
    print(f"Using namespace: {ns}")
    run_cmd(f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/scrape=\"true\" --overwrite", shell=True)
    run_cmd(f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/path=\"/metrics\" --overwrite", shell=True)
    run_cmd(f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/port=\"{FLEETCAST_METRICS_PORT}\" --overwrite", shell=True)
    run_cmd(f"kubectl -n {shlex.quote(ns)} rollout status deploy/{shlex.quote(FLEETCAST_DEP)}", shell=True)

def jq_exists() -> bool:
    return subprocess.run(["bash", "-lc", "command -v jq >/dev/null 2>&1"]).returncode == 0

def check(prom_url: str):
    run_cmd(["kubectl", "-n", PROM_NAMESPACE, "get", "svc", PROM_SVC_NAME])
    run_cmd(f"curl -sS {prom_url}/-/ready", shell=True)
    if jq_exists():
        run_cmd(
            f"curl -sS {prom_url}/api/v1/targets | "
            "jq -r '.data.activeTargets[] | select(.labels.job==\"kubernetes-pods\") | "
            "(.labels.namespace + \" \" + .labels.pod + \" \" + .health)' | sort -u",
            shell=True
        )
    else:
        run_cmd(f"curl -sS {prom_url}/api/v1/targets", shell=True)
    if jq_exists():
        run_cmd(
            f"curl -sS '{prom_url}/api/v1/query' --get --data-urlencode 'query=sum(up{{namespace=\"{FLEETCAST_NS}\"}})' | jq .",
            shell=True
        )
    else:
        run_cmd(
            f"curl -sS '{prom_url}/api/v1/query?query=sum(up{{namespace=\"{FLEETCAST_NS}\"}})'",
            shell=True
        )

def verify_fleetcast(prom_url: str):
    q1 = "count(up{job=\"kubernetes-pods\",namespace=\"%s\"}==1)" % FLEETCAST_NS
    q2 = "sum(scrape_samples_scraped{job=\"kubernetes-pods\",namespace=\"%s\"})" % FLEETCAST_NS
    out1 = run_cmd(f"curl -sS --get '{prom_url}/api/v1/query' --data-urlencode 'query={q1}'", shell=True, capture=True)
    out2 = run_cmd(f"curl -sS --get '{prom_url}/api/v1/query' --data-urlencode 'query={q2}'", shell=True, capture=True)
    try:
        r1 = json.loads(out1); r2 = json.loads(out2)
        up = int(float(r1.get("data",{}).get("result",[{"value":[0,"0"]}])[0]["value"][1])) if r1.get("data",{}).get("result") else 0
        samples = int(float(r2.get("data",{}).get("result",[{"value":[0,"0"]}])[0]["value"][1])) if r2.get("data",{}).get("result") else 0
    except Exception:
        up = 0; samples = 0
    print(f"fleetcast targets_up={up} samples_scraped={samples}")
    if up == 0 or samples == 0:
        raise SystemExit("FleetCast not being scraped (up==0 or samples==0)")

def main():
    ensure_ns(PROM_NAMESPACE)
    helm_repo_setup()
    helm_apply_values()
    wait_for_prometheus_ready()
    try:
        run_cmd(["kubectl", "-n", PROM_NAMESPACE, "get", "svc", PROM_SVC_NAME])
    except subprocess.CalledProcessError:
        print("Could not find expected Service. Available services in namespace:")
        run_cmd(["kubectl", "-n", PROM_NAMESPACE, "get", "svc", "-o", "wide"], check=False)
        raise
    ensure_nodeport(PROM_SVC_NAME, target_port=PROM_PORT, node_port=DESIRED_NODEPORT)
    prom_url = find_first_reachable_prom_url(DESIRED_NODEPORT) or "http://localhost:9090"
    annotate_fleetcast_and_rollout()
    time.sleep(8)
    check(prom_url)
    verify_fleetcast(prom_url)

if __name__ == "__main__":
    main()
