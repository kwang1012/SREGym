"""Interface to the Train Ticket application"""

import time

from srearena.paths import TARGET_MICROSERVICES, TRAIN_TICKET_METADATA
from srearena.service.apps.base import Application
from srearena.service.helm import Helm
from srearena.service.kubectl import KubeCtl


class TrainTicket(Application):
    def __init__(self):
        super().__init__(str(TRAIN_TICKET_METADATA))
        self.load_app_json()
        self.kubectl = KubeCtl()
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
        
        self._deploy_flagd_infrastructure()
        
        Helm.install(**self.helm_configs)
        Helm.assert_if_deployed(self.helm_configs["namespace"])

    def delete(self):
        """Delete the Helm configurations."""
        # Helm.uninstall(**self.helm_configs) # Don't helm uninstall until cleanup job is fixed on train-ticket
        if self.namespace:
            self.kubectl.delete_namespace(self.namespace)
        time.sleep(30)

    def cleanup(self):
        # Helm.uninstall(**self.helm_configs)
        if self.namespace:
            self.kubectl.delete_namespace(self.namespace)
    
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
