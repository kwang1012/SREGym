import time

from srearena.generators.workload.blueprint_hotel_work import BHotelWrk, BHotelWrkWorkloadManager
from srearena.observer.trace_api import TraceAPI
from srearena.paths import FAULT_SCRIPTS, BLUEPRINT_HOTEL_RES_METADATA, TARGET_MICROSERVICES
from srearena.service.apps.base import Application
from srearena.service.kubectl import KubeCtl


class BlueprintHotelReservation(Application):
    def __init__(self):
        super().__init__(BLUEPRINT_HOTEL_RES_METADATA)
        self.kubectl = KubeCtl()
        self.trace_api = None
        self.trace_api = None
        self.script_dir = FAULT_SCRIPTS
        self.helm_deploy = False

        self.load_app_json()

    def load_app_json(self):
        super().load_app_json()
        metadata = self.get_app_json()
        self.app_name = metadata["Name"]
        self.description = metadata["Desc"]
        self.k8s_workload_job_path = TARGET_MICROSERVICES / metadata["K8S Workload Job Path"]

    def deploy(self):
        """Deploy the Kubernetes configurations."""
        print(f"Deploying Kubernetes configurations in namespace: {self.namespace}")
        self.create_namespace()
        self.kubectl.apply_configs(self.namespace, self.k8s_deploy_path)
        self.kubectl.wait_for_ready(self.namespace)
        self.trace_api = TraceAPI(self.namespace)
        self.trace_api.start_port_forward()

    def delete(self):
        """Delete the configmap."""
        self.kubectl.delete_configs(self.namespace, self.k8s_deploy_path)
        """Delete the workload job if exists"""
        self.kubectl.delete_configs(self.namespace, self.k8s_workload_job_path)

    def cleanup(self):
        """Delete the entire namespace for the hotel reservation application."""
        if self.trace_api:
            self.trace_api.stop_port_forward()
        self.kubectl.delete_namespace(self.namespace)
        self.kubectl.wait_for_namespace_deletion(self.namespace)

        if hasattr(self, "wrk"):
            self.wrk.stop()

    # helper methods
    def _read_script(self, file_path: str) -> str:
        with open(file_path, "r") as file:
            return file.read()

    def create_workload(
        self, tput: int = 3000, duration: str = '120s', multiplier: int = 6
    ):
        self.wrk = BHotelWrkWorkloadManager(
            wrk=BHotelWrk(tput=tput, duration=duration, multiplier=multiplier),
        )

    def start_workload(self):
        if not hasattr(self, "wrk"):
            self.create_workload()
        self.wrk.start()
