"""Otel demo adServiceHighCpu feature flag fault."""

from aiopslab.conductor.oracles.detection import DetectionOracle
from aiopslab.conductor.oracles.localization import LocalizationOracle
from aiopslab.conductor.oracles.mitigation import MitigationOracle
from aiopslab.conductor.problems.base import Problem
from aiopslab.generators.fault.inject_otel import OtelFaultInjector
from aiopslab.service.apps.astronomy_shop import AstronomyShop
from aiopslab.service.kubectl import KubeCtl


class AdServiceHighCpu(Problem):
    def __init__(self):
        self.app = AstronomyShop()
        self.kubectl = KubeCtl()
        self.namespace = self.app.namespace
        self.injector = OtelFaultInjector(namespace=self.namespace)
        self.faulty_service = "ad"
        # === Attach evaluation oracles ===
        self.detection_oracle = DetectionOracle(problem=self, expected="Yes")

        self.localization_oracle = LocalizationOracle(problem=self, expected=[self.faulty_service])

    def start_workload(self):
        print("== Start Workload ==")
        print("Workload skipped since AstronomyShop has a built-in load generator.")

    def inject_fault(self):
        print("== Fault Injection ==")
        self.injector.inject_fault("adHighCpu")
        print(f"Fault: AdServiceHighCpu | Namespace: {self.namespace}\n")

    def recover_fault(self):
        print("== Fault Recovery ==")
        self.injector.recover_fault("adHighCpu")
