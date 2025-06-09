from srearena.conductor.oracles.base import Oracle
from srearena.conductor.oracles.utils import is_exact_match


class DetectionOracle(Oracle):
    def __init__(self, problem, expected="Yes"):
        super().__init__(problem)
        self.expected = expected

    def evaluate(self, solution) -> dict:
        print("== Detection Evaluation ==")
        results = {}

        if isinstance(solution, str):
            is_correct = is_exact_match(solution.strip().lower(), self.expected.lower())
            results["accuracy"] = 100.0 if is_correct else 0.0
            results["success"] = is_correct
            print(f"{'✅' if is_correct else '❌'} Detection: {solution}")
        else:
            results["accuracy"] = 0.0
            results["success"] = False
            results["reason"] = "Invalid Format"
            print("❌ Invalid detection format")

        return results
