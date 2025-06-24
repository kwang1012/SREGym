from kubernetes import client, config

from srearena.conductor.oracles.detection import DetectionOracle
from srearena.conductor.oracles.localization import LocalizationOracle
from srearena.conductor.oracles.mitigation import MitigationOracle
from srearena.conductor.problems.base import Problem
from srearena.paths import TARGET_MICROSERVICES
from srearena.service.apps.hotelres import HotelReservation
from srearena.service.kubectl import KubeCtl


class NetworkPolicyBlock(Problem):
    def __init__(self, faulty_service="payment-service"):
        self.app = HotelReservation()
        self.kubectl = KubeCtl()
        self.faulty_service = faulty_service
        self.policy_name = f"deny-all-{faulty_service}"

        self.app.payload_script = (
            TARGET_MICROSERVICES / "hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua"
        )
        self.app.create_workload()

        super().__init__(app=self.app, namespace=self.app.namespace)
        config.load_kube_config()
        self.networking_v1 = client.NetworkingV1Api()

        self.localization_oracle = LocalizationOracle(problem=self, expected=[self.faulty_service, "NetworkPolicy"])
        self.mitigation_oracle = MitigationOracle(problem=self)

    def inject_fault(self):
        """Block ALL traffic to/from the target service"""
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": self.policy_name, "namespace": self.namespace},
            "spec": {
                "podSelector": {"matchLabels": {"app": self.faulty_service}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        }
        self.networking_v1.create_namespaced_network_policy(namespace=self.namespace, body=policy)

    def recover_fault(self):
        """Remove the NetworkPolicy"""
        self.networking_v1.delete_namespaced_network_policy(name=self.policy_name, namespace=self.namespace)
