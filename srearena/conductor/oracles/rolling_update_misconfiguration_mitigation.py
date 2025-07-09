import yaml
from srearena.conductor.oracles.base import Oracle

class RollingUpdateMitigationOracle(Oracle):
    def __init__(self, problem, deployment_name: str):
        super().__init__(problem)
        self.deployment_name = deployment_name
        self.namespace = problem.namespace
        self.kubectl = problem.kubectl

    def evaluate(self) -> dict:
        print("== Rolling Update Mitigation Evaluation ==")

        try:
            output = self.kubectl.exec_command(
                f"kubectl get deployment {self.deployment_name} -n {self.namespace} -o yaml"
            )
            deployment = yaml.safe_load(output)

            containers = deployment["spec"]["template"]["spec"]["containers"]
            has_readiness_probe = any("readinessProbe" in container for container in containers)

            strategy = deployment["spec"].get("strategy", {})
            rolling_update = strategy.get("rollingUpdate", {})
            max_unavailable = rolling_update.get("maxUnavailable", "25%")

            if isinstance(max_unavailable, str) and max_unavailable.endswith("%"):
                max_unavailable_value = int(max_unavailable.strip('%'))
            else:
                max_unavailable_value = int(max_unavailable)

            is_safe_unavailability = max_unavailable_value < 100

            if has_readiness_probe or is_safe_unavailability:
                print("✅ Mitigation successful:")
                if has_readiness_probe:
                    print("   - Readiness probe is configured.")
                if is_safe_unavailability:
                    print("   - maxUnavailable is set to a safe value (< 100%).")
                return {"success": True}
            else:
                print("❌ Mitigation failed: neither readiness probe nor safe rolling update configuration")
                return {"success": False}

        except Exception as e:
            print(f"❌ Error during evaluation: {str(e)}")
            return {"success": False}
