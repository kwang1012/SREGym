import subprocess
import shlex

PROM = "http://130.127.133.164:32000"
FLEETCAST_NS = "fleetcast"
FLEETCAST_DEP = "fleetcast-satellite-app-backend"
FLEETCAST_METRICS_PORT = "5000"
PROM_VALUES_PATH = "/Users/lilygniedz/Documents/SREArena/SREArena/aiopslab-applications/FleetCast/prometheus/prometheus.yaml"

def run_cmd(cmd, shell=False):
    print("Running:", cmd if isinstance(cmd, str) else " ".join(cmd))
    subprocess.run(cmd, shell=shell, check=True)

def get_output(cmd):
    if isinstance(cmd, str):
        out = subprocess.check_output(cmd, shell=True, text=True)
    else:
        out = subprocess.check_output(cmd, text=True)
    return out.strip()

def ns_exists(ns):
    return subprocess.run(["kubectl", "get", "ns", ns], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def find_dep_namespace(dep_name):
    if ns_exists(FLEETCAST_NS):
        if subprocess.run(["kubectl", "-n", FLEETCAST_NS, "get", "deploy", dep_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return FLEETCAST_NS
    try:
        ns = get_output(
            "kubectl get deploy -A -o jsonpath='{range .items[*]}{.metadata.namespace} "
            "{.metadata.name}{\"\\n\"}{end}' | awk '$2==\"%s\"{print $1; exit}'" % dep_name
        )
        return ns if ns else None
    except subprocess.CalledProcessError:
        return None

def helm_apply_values():
    run_cmd([
        "helm","upgrade","prometheus","prometheus-community/prometheus",
        "-n","observe",
        "-f",PROM_VALUES_PATH,
        "--reuse-values",
        "--set","server.httpRoute.enabled=false",
        "--set-json",'server.route={"enabled":false,"main":{"enabled":false}}',
        "--set","configmapReload.prometheus.startupProbe.enabled=false",
        "--set","configmapReload.prometheus.livenessProbe.enabled=true",
        "--set","configmapReload.prometheus.readinessProbe.enabled=true"
    ])

def ensure_nodeport_32000():
    run_cmd([
        "kubectl","-n","observe","patch","svc","prometheus-server",
        "--type=json",
        "-p=[{\"op\":\"replace\",\"path\":\"/spec/type\",\"value\":\"NodePort\"}]"
    ])
    try:
        run_cmd([
            "kubectl","-n","observe","patch","svc","prometheus-server",
            "--type=json",
            "-p=[{\"op\":\"replace\",\"path\":\"/spec/ports/0/nodePort\",\"value\":32000}]"
        ])
    except subprocess.CalledProcessError:
        run_cmd([
            "kubectl","-n","observe","patch","svc","prometheus-server",
            "--type=json",
            "-p=[{\"op\":\"add\",\"path\":\"/spec/ports/0/nodePort\",\"value\":32000}]"
        ])

def annotate_fleetcast():
    ns = FLEETCAST_NS if ns_exists(FLEETCAST_NS) else find_dep_namespace(FLEETCAST_DEP)
    if not ns:
        print(f"WARNING: Could not find namespace for deployment '{FLEETCAST_DEP}'. Skip annotations.")
        return
    print(f"Using namespace: {ns}")
    run_cmd(
        f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/scrape=\"true\" --overwrite",
        shell=True
    )
    run_cmd(
        f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/path=\"/metrics\" --overwrite",
        shell=True
    )
    run_cmd(
        f"kubectl -n {shlex.quote(ns)} annotate deploy {shlex.quote(FLEETCAST_DEP)} prometheus.io/port=\"{FLEETCAST_METRICS_PORT}\" --overwrite",
        shell=True
    )
    run_cmd(
        f"kubectl -n {shlex.quote(ns)} rollout status deploy/{shlex.quote(FLEETCAST_DEP)}",
        shell=True
    )

def check():
    run_cmd(["kubectl","-n","observe","get","svc","prometheus-server"])
    run_cmd(f"curl -sS {PROM}/-/ready", shell=True)
    run_cmd(
        f"curl -sS {PROM}/api/v1/targets | jq -r '.data.activeTargets[] | select(.labels.job==\"kubernetes-pods\") | .labels.namespace+\" \"+.labels.pod+\" \"+.health' | sort -u",
        shell=True
    )
    run_cmd(
        f"curl -sS '{PROM}/api/v1/query' --get --data-urlencode 'query=sum(up{{namespace=\"fleetcast\"}})' | jq .",
        shell=True
    )

if __name__ == "__main__":
    helm_apply_values()
    ensure_nodeport_32000()
    annotate_fleetcast()
    check()
