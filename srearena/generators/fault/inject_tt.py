"""TrainTicket Fault Injector for SREArena

Provides Python interface to inject and recover TrainTicket faults
via flagd ConfigMap manipulation.
"""

import subprocess
import yaml
import json
import time
from typing import List, Dict, Optional
import logging

from srearena.generators.fault.base import FaultInjector
from srearena.service.kubectl import KubeCtl

logger = logging.getLogger(__name__)


class TrainTicketFaultInjector(FaultInjector):
    """
    Fault injector for TrainTicket microservices using flagd feature flags.
    
    Manages 22 different fault scenarios by updating Kubernetes ConfigMaps
    and restarting the flagd service to reload configuration.
    """

    def __init__(self, namespace: str = "train-ticket"):
        self.namespace = namespace
        self.configmap_name = "flagd-config"
        self.flagd_deployment = "flagd"
        self.kubectl = KubeCtl()
        
        self.available_faults = [
            "fault-1-async-message-order",
            "fault-2-cpu-occupancy", 
            "fault-3-memory-leak",
            "fault-4-connection-pool-exhaustion",
            "fault-5-network-partition",
            "fault-6-disk-space-full",
            "fault-7-service-unavailable",
            "fault-8-database-lock-timeout",
            "fault-9-message-queue-overflow",
            "fault-10-slow-database-query",
            "fault-11-configuration-error",
            "fault-12-authentication-failure",
            "fault-13-cache-miss-storm",
            "fault-14-http-timeout",
            "fault-15-data-inconsistency",
            "fault-16-service-discovery-failure",
            "fault-17-load-balancer-failure",
            "fault-18-session-timeout",
            "fault-19-payment-gateway-error",
            "fault-20-email-service-failure",
            "fault-21-file-upload-failure",
            "fault-22-monitoring-system-failure"
        ]

    def inject_fault(self, fault_name: str) -> bool:
        """
        Enable a TrainTicket fault by updating the flagd ConfigMap.
        
        Args:
            fault_name: Name of fault to enable (e.g., "fault-1-async-message-order")
            
        Returns:
            bool: True if fault was successfully enabled
        """
        if fault_name not in self.available_faults:
            logger.error(f"Unknown fault: {fault_name}")
            return False
            
        try:
            print(f"[TrainTicket] Enabling fault: {fault_name}")
            
            if not self._update_fault_flag(fault_name, "on"):
                return False
                
            if not self._restart_flagd():
                return False
                
            status = self.get_fault_status(fault_name)
            if status == "on":
                print(f"✅ Fault '{fault_name}' successfully enabled")
                return True
            else:
                print(f"❌ Failed to enable fault '{fault_name}' - status: {status}")
                return False
                
        except Exception as e:
            logger.error(f"Error injecting fault {fault_name}: {e}")
            return False

    def recover_fault(self, fault_name: str) -> bool:
        """
        Disable a TrainTicket fault by updating the flagd ConfigMap.
        
        Args:
            fault_name: Name of fault to disable
            
        Returns:
            bool: True if fault was successfully disabled
        """
        if fault_name not in self.available_faults:
            logger.error(f"Unknown fault: {fault_name}")
            return False
            
        try:
            print(f"[TrainTicket] Disabling fault: {fault_name}")
            
            if not self._update_fault_flag(fault_name, "off"):
                return False
                
            if not self._restart_flagd():
                return False
                
            status = self.get_fault_status(fault_name)
            if status == "off":
                print(f"✅ Fault '{fault_name}' successfully disabled")
                return True
            else:
                print(f"❌ Failed to disable fault '{fault_name}' - status: {status}")
                return False
                
        except Exception as e:
            logger.error(f"Error recovering fault {fault_name}: {e}")
            return False

    def get_fault_status(self, fault_name: str) -> str:
        """
        Get the current status of a fault flag.
        
        Args:
            fault_name: Name of fault to check
            
        Returns:
            str: "on", "off", or "unknown"
        """
        try:
            result = subprocess.run([
                "kubectl", "get", "configmap", self.configmap_name,
                "-n", self.namespace, "-o", "yaml"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return "unknown"
                
            configmap = yaml.safe_load(result.stdout)
            flags_yaml = configmap.get("data", {}).get("flags.yaml", "")
            
            if not flags_yaml:
                return "unknown"
                
            flags_config = yaml.safe_load(flags_yaml)
            fault_config = flags_config.get("flags", {}).get(fault_name, {})
            
            default_variant = fault_config.get("defaultVariant", "off")
            return default_variant
            
        except Exception as e:
            logger.error(f"Error getting fault status for {fault_name}: {e}")
            return "unknown"

    def list_available_faults(self) -> List[str]:
        """
        List all available TrainTicket faults.
        
        Returns:
            List[str]: List of fault names
        """
        return self.available_faults.copy()

    def _update_fault_flag(self, fault_name: str, state: str) -> bool:
        """
        Update a specific fault flag in the ConfigMap.
        
        Args:
            fault_name: Name of the fault
            state: "on" or "off"
            
        Returns:
            bool: True if update successful
        """
        try:
            result = subprocess.run([
                "kubectl", "get", "configmap", self.configmap_name,
                "-n", self.namespace, "-o", "yaml"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error("Failed to get ConfigMap")
                return False
                
            configmap = yaml.safe_load(result.stdout)
            flags_yaml = configmap.get("data", {}).get("flags.yaml", "")
            
            if not flags_yaml:
                logger.error("No flags.yaml found in ConfigMap")
                return False
                
            flags_config = yaml.safe_load(flags_yaml)
            
            if fault_name not in flags_config.get("flags", {}):
                logger.error(f"Fault {fault_name} not found in configuration")
                return False
                
            flags_config["flags"][fault_name]["defaultVariant"] = state
            
            updated_flags_yaml = yaml.dump(flags_config, default_flow_style=False)
            configmap["data"]["flags.yaml"] = updated_flags_yaml
            
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                yaml.dump(configmap, f, default_flow_style=False)
                temp_file = f.name
                
            result = subprocess.run([
                "kubectl", "apply", "-f", temp_file
            ], capture_output=True, text=True)
            
            os.unlink(temp_file)
            
            if result.returncode == 0:
                print(f"✅ Updated {fault_name} flag to '{state}'")
                return True
            else:
                logger.error(f"kubectl apply failed: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error updating fault flag: {e}")
            return False

    def _restart_flagd(self) -> bool:
        """
        Restart the flagd deployment to reload configuration.
        
        Returns:
            bool: True if restart successful
        """
        try:
            print("🔄 Restarting flagd deployment to reload configuration...")
            
            result = subprocess.run([
                "kubectl", "rollout", "restart", f"deployment/{self.flagd_deployment}",
                "-n", self.namespace
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to restart flagd: {result.stderr}")
                return False
                
            result = subprocess.run([
                "kubectl", "rollout", "status", f"deployment/{self.flagd_deployment}",
                "-n", self.namespace, "--timeout=60s"
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print("✅ flagd deployment restarted successfully")
                time.sleep(5)
                return True
            else:
                logger.error(f"Failed to wait for rollout: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error restarting flagd: {e}")
            return False

    def health_check(self) -> Dict[str, bool]:
        """
        Perform health check on the fault injection system.
        
        Returns:
            Dict: Component health status
        """
        health = {
            "namespace_exists": False,
            "configmap_exists": False,
            "flagd_running": False,
            "flagd_accessible": False
        }
        
        try:
            result = subprocess.run([
                "kubectl", "get", "namespace", self.namespace
            ], capture_output=True, text=True)
            health["namespace_exists"] = result.returncode == 0
            
            result = subprocess.run([
                "kubectl", "get", "configmap", self.configmap_name, "-n", self.namespace
            ], capture_output=True, text=True)
            health["configmap_exists"] = result.returncode == 0
            
            result = subprocess.run([
                "kubectl", "get", "deployment", self.flagd_deployment, "-n", self.namespace
            ], capture_output=True, text=True)
            health["flagd_running"] = result.returncode == 0
            
            result = subprocess.run([
                "kubectl", "get", "pods", "-l", "app=flagd", "-n", self.namespace,
                "-o", "jsonpath={.items[0].status.phase}"
            ], capture_output=True, text=True)
            health["flagd_accessible"] = result.stdout.strip() == "Running"
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            
        return health


def main():
    """Example usage of the TrainTicket fault injector."""
    injector = TrainTicketFaultInjector("train-ticket")
    
    print("=== TrainTicket Fault Injector ===")
    print(f"Available faults: {len(injector.list_available_faults())}")
    
    health = injector.health_check()
    print(f"System health: {health}")
    
    fault_name = "fault-1-async-message-order"
    print(f"\nCurrent {fault_name} status: {injector.get_fault_status(fault_name)}")
    
    if injector.inject_fault(fault_name):
        print(f"✅ {fault_name} enabled successfully")
    else:
        print(f"❌ Failed to enable {fault_name}")
        
    print(f"New {fault_name} status: {injector.get_fault_status(fault_name)}")


if __name__ == "__main__":
    main()
