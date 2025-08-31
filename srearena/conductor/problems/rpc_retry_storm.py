from srearena.service.apps.blueprint_hotel_reservation import BlueprintHotelReservation
from srearena.conductor.oracles.detection import DetectionOracle
from srearena.conductor.oracles.localization import LocalizationOracle
from srearena.conductor.oracles.rpc_retry_storm_mitigation import RPCRetryStormMitigationOracle
from srearena.conductor.problems.base import Problem
from srearena.service.kubectl import KubeCtl
from srearena.generators.fault.inject_virtual import VirtualizationFaultInjector
from srearena.utils.decorators import mark_fault_injected

class RPCRetryStorm(Problem):
    def __init__(self):
        self.app = BlueprintHotelReservation()
        self.kubectl = KubeCtl()
        self.namespace = self.app.namespace
        self.faulty_service = "rpc"

        super().__init__(app=self.app, namespace=self.app.namespace)
        # === Attach evaluation oracles ===
        self.localization_oracle = LocalizationOracle(problem=self, expected=[self.faulty_service])

        self.mitigation_oracle = RPCRetryStormMitigationOracle(problem=self)

    @mark_fault_injected
    def inject_fault(self):
        print("== Fault Injection ==")
        injector = VirtualizationFaultInjector(namespace=self.namespace)
        injector.inject_rpc_timeout_retries_misconfiguration(configmap=self.faulty_service)
        print(f"Service: {self.faulty_service} | Namespace: {self.namespace}\n")

    @mark_fault_injected
    def recover_fault(self):
        print("== Fault Recovery ==")
        injector = VirtualizationFaultInjector(namespace=self.namespace)
        injector.recover_rpc_timeout_retries_misconfiguration(configmap=self.faulty_service)
        print(f"Service: {self.faulty_service} | Namespace: {self.namespace}\n")