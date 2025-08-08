import atexit
import shutil
import threading
import time
from contextlib import nullcontext
from json.decoder import JSONDecodeError

from srearena.conductor.oracles.detection import DetectionOracle
from srearena.conductor.problems.registry import ProblemRegistry
from srearena.service.apps.registry import AppRegistry
from srearena.service.kubectl import KubeCtl
from srearena.service.telemetry.prometheus import Prometheus
from srearena.utils.critical_section import CriticalSection
from srearena.utils.sigint_aware_section import SigintAwareSection


class Conductor:
    def __init__(self):
        # core services
        self.problems = ProblemRegistry()
        self.kubectl = KubeCtl()
        self.prometheus = Prometheus()
        self.apps = AppRegistry()
        self.agent_name = None

        # runtime state
        self.problem_id = None
        self.problem = None
        self.app = None
        self.detection_oracle = None
        self.execution_start_time = None

        # grading flow state
        self.submission_stage = None  # "noop", "detection", "localization", "mitigation", "done"
        self.results = {}

    def register_agent(self, name="agent"):
        self.agent_name = name

    def dependency_check(self, binaries: list[str]):
        for b in binaries:
            if shutil.which(b) is None:
                raise RuntimeError(f"[❌] Required dependency '{b}' not found.")

    async def start_problem(self):
        """
        1) Provision infra & workload
        2) Flip to NO-OP grading stage
        """
        self.execution_start_time = time.time()
        self.problem = self.problems.get_problem_instance(self.problem_id)
        self.app = self.problem.app
        self.detection_oracle = DetectionOracle(self.problem)
        self.results = {}

        # Only install SIGINT handler in main thread
        ctx = SigintAwareSection() if threading.current_thread() is threading.main_thread() else nullcontext()

        # 1) Environment setup
        self.dependency_check(["kubectl", "helm"])
        with ctx:
            print(f"[Session Start] Problem ID: {self.problem_id}")
            self.deploy_app()

        # 2) Ready for NO-OP detection
        self.submission_stage = "noop"
        print("✅ Environment ready—now POST /submit to grade NO-OP detection.")

    async def ask_env(self, wrapped_cmd: str):
        """
        Called by CLI or HTTP /submit.  Parses & grades the `submit(...)` call,
        advances submission_stage, records results—and when we hit “done”,
        triggers undeploy_app (which also tears down infra if nothing else is live).
        """
        from srearena.conductor.parser import ResponseParser

        parser = ResponseParser()
        parsed = parser.parse(wrapped_cmd)
        if parsed["api_name"] != "submit":
            return "[❌] Only `submit(...)` is supported."
        sol = parsed["args"][0] if parsed["args"] else None

        # NO-OP
        if self.submission_stage == "noop":
            r = self.detection_oracle.evaluate(sol)
            self.results["NOOP Detection"] = r
            if r.get("reason") == "Invalid Format":
                return "[⚠️] Invalid NO-OP format."

            # inject fault
            with CriticalSection():
                self.problem.inject_fault()
                atexit.register(self.exit_cleanup_and_recover_fault)

            self.submission_stage = "detection"
            return "[✅] NO-OP passed — fault injected. Now submit detection."

        # DETECTION
        if self.submission_stage == "detection":
            r = self.detection_oracle.evaluate(sol)
            self.results["Detection"] = r
            self.results["TTD"] = time.time() - self.execution_start_time

            if self.problem.localization_oracle:
                self.submission_stage = "localization"
                return "[✅] Detection recorded — now submit localization."
            elif self.problem.mitigation_oracle:
                self.submission_stage = "mitigation"
                return "[✅] Detection recorded — now submit mitigation."
            else:
                self.submission_stage = "done"
                self.undeploy_app()
                return "[✅] Detection recorded — all done."

        # LOCALIZATION
        if self.submission_stage == "localization":
            r = self.problem.localization_oracle.evaluate(sol)
            self.results["Localization"] = r
            self.results["TTL"] = time.time() - self.execution_start_time

            if self.problem.mitigation_oracle:
                self.submission_stage = "mitigation"
                return "[✅] Localization recorded — now submit mitigation."
            else:
                self.submission_stage = "done"
                self.undeploy_app()
                return "[✅] Localization recorded — all done."

        # MITIGATION
        if self.submission_stage == "mitigation":
            r = self.problem.mitigation_oracle.evaluate()
            self.results["Mitigation"] = r
            self.results["TTM"] = time.time() - self.execution_start_time

            self.submission_stage = "done"
            self.undeploy_app()
            return "[✅] Mitigation recorded — all done."

        return "[✅] All stages completed."

    def deploy_app(self):
        """
        Kubectl + Prometheus + problem.app deployment.
        """
        print("Setting up metrics-server...")
        self.kubectl.exec_command(
            "kubectl apply -f "
            "https://github.com/kubernetes-sigs/metrics-server/"
            "releases/latest/download/components.yaml"
        )
        self.kubectl.exec_command(
            "kubectl -n kube-system patch deployment metrics-server "
            "--type=json -p='["
            '{"op":"add","path":"/spec/template/spec/containers/0/args/-",'
            '"value":"--kubelet-insecure-tls"},'
            '{"op":"add","path":"/spec/template/spec/containers/0/args/-",'
            '"value":"--kubelet-preferred-address-types=InternalIP"}'
            "]'"
        )
        self.kubectl.wait_for_ready("kube-system")

        print("Setting up OpenEBS...")
        self.kubectl.exec_command("kubectl apply -f https://openebs.github.io/charts/openebs-operator.yaml")
        self.kubectl.exec_command(
            "kubectl patch storageclass openebs-hostpath "
            '-p \'{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}\''
        )
        self.kubectl.wait_for_ready("openebs")

        print("Deploying Prometheus...")
        self.prometheus.deploy()

        print("Deploying and starting workload...")
        self.problem.app.delete()
        self.problem.app.deploy()
        self.problem.app.start_workload()

    def undeploy_app(self):
        """Teardown problem.app and, if no other apps running, OpenEBS/Prometheus."""
        self.problem.app.cleanup()
        deployed = self.get_deployed_apps()
        if not deployed:
            self.prometheus.teardown()
            self.kubectl.exec_command("kubectl delete sc openebs-hostpath openebs-device --ignore-not-found")
            self.kubectl.exec_command("kubectl delete -f https://openebs.github.io/charts/openebs-operator.yaml")
            self.kubectl.wait_for_namespace_deletion("openebs")

    def get_deployed_apps(self) -> list[str]:
        """
        Used by CLI 'list' to show which apps are live.
        """
        live = []
        for name in self.apps.get_app_names():
            meta = self.apps.get_app_metadata(name)
            ns = meta["Namespace"]
            if self.kubectl.get_namespace_deployment_status(ns):
                live.append(name)
        return live

    def exit_cleanup_and_recover_fault(self):
        """Called on SIGINT or atexit to reset the cluster."""
        try:
            if self.problem:
                self.problem.recover_fault()
                self.problem.app.cleanup()
        except (JSONDecodeError, RuntimeError):
            pass

        # always teardown infra if nothing else is running
        self.prometheus.teardown()
        self.kubectl.exec_command("kubectl delete sc openebs-hostpath openebs-device --ignore-not-found")
        self.kubectl.exec_command("kubectl delete -f https://openebs.github.io/charts/openebs-operator.yaml")


def exit_cleanup_fault(conductor):
    conductor.exit_cleanup_and_recover_fault()
