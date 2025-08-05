"""Interface to the Train Ticket application"""

import os
import tempfile
import time
from pathlib import Path

from srearena.generators.workload.locust import LocustWorkloadManager
from srearena.paths import TARGET_MICROSERVICES, TRAIN_TICKET_METADATA
from srearena.service.apps.base import Application
from srearena.service.helm import Helm
from srearena.service.kubectl import KubeCtl


class TrainTicket(Application):
    def __init__(self):
        super().__init__(str(TRAIN_TICKET_METADATA))
        self.load_app_json()
        self.kubectl = KubeCtl()
        self.workload_manager = None
        self.create_namespace()

    def load_app_json(self):
        super().load_app_json()
        metadata = self.get_app_json()
        self.frontend_service = None
        self.frontend_port = None

    def deploy(self):
        """Deploy the Helm configurations and flagd infrastructure."""
        if self.namespace:
            self.kubectl.create_namespace_if_not_exist(self.namespace)

        Helm.install(**self.helm_configs)
        self.kubectl.wait_for_job_completion(name="train-ticket-deploy", namespace="train-ticket") 

        self._deploy_flagd_infrastructure()
        self._deploy_load_generator()

    def delete(self):
        """Delete the Helm configurations."""
        # Helm.uninstall(**self.helm_configs) # Don't helm uninstall until cleanup job is fixed on train-ticket
        if self.namespace:
            self.kubectl.delete_namespace(self.namespace)
        self.kubectl.wait_for_namespace_deletion(self.namespace)

    def cleanup(self):
        # Helm.uninstall(**self.helm_configs)
        if self.namespace:
            self.kubectl.delete_namespace(self.namespace)

    def create_workload(self):
        """Create workload manager for log collection (like astronomy shop)."""  
        self.wrk = LocustWorkloadManager(
            namespace=self.namespace,
            locust_url="load-generator:8089",
        )

    def start_workload(self):
        """Start workload log collection (like astronomy shop)."""
        if not hasattr(self, "wrk"):
            self.create_workload()
        self.wrk.start()
        print("[TrainTicket] Workload log collection started")

    def stop_workload(self):
        """Stop the workload log collection."""
        if hasattr(self, "wrk"):
            self.wrk.stop()
            print("[TrainTicket] Workload log collection stopped")

    def _deploy_flagd_infrastructure(self):
        """Deploy flagd service and ConfigMap for fault injection."""
        try:
            flagd_templates_path = TARGET_MICROSERVICES / "train-ticket" / "templates"

            if (flagd_templates_path / "flagd-deployment.yaml").exists():
                result = self.kubectl.exec_command(f"kubectl apply -f {flagd_templates_path / 'flagd-deployment.yaml'}")
                print(f"[TrainTicket] Deployed flagd service: {result}")

            if (flagd_templates_path / "flagd-config.yaml").exists():
                result = self.kubectl.exec_command(f"kubectl apply -f {flagd_templates_path / 'flagd-config.yaml'}")
                print(f"[TrainTicket] Deployed flagd ConfigMap: {result}")

            print(f"[TrainTicket] flagd infrastructure deployed successfully")

        except Exception as e:
            print(f"[TrainTicket] Warning: Failed to deploy flagd infrastructure: {e}")

    def _deploy_load_generator(self):
        """Deploy the auto-starting load generator (like astronomy shop)."""
        try:

            locustfile_path = Path(__file__).parent.parent.parent / "resources" / "trainticket" / "locustfile.py"
            
            if locustfile_path.exists():
                result = self.kubectl.exec_command(f"kubectl create configmap locustfile-config --from-file=locustfile.py={locustfile_path} -n {self.namespace} --dry-run=client -o yaml | kubectl apply -f -")
                print(f"[TrainTicket] Created ConfigMap from file: {result}")
            
            deployment_path = Path(__file__).parent.parent.parent / "resources" / "trainticket" / "locust-deployment.yaml"
            
            if deployment_path.exists():
                with open(deployment_path, "r") as f:
                    content = f.read()
                
                with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
                    tmp.write(content)
                    temp_path = tmp.name
                
                result = self.kubectl.exec_command(f"kubectl apply -f {temp_path}")
                os.unlink(temp_path)
                print(f"[TrainTicket] Deployed load generator: {result}")
            
            print("[TrainTicket] Load generator deployed with auto-start")
            
        except Exception as e:
            print(f"[TrainTicket] Warning: Failed to deploy load generator: {e}")

    def get_flagd_status(self):
        """Check if flagd infrastructure is running."""
        try:
            result = self.kubectl.exec_command(f"kubectl get pods -l app=flagd -n {self.namespace}")
            return "Running" in result
        except Exception:
            return False



# if __name__ == "__main__":
#     app = TrainTicket()
#     app.deploy()
#     app.delete()
