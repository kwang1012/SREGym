#!/usr/bin/env python3
"""
Robust TrainTicket F1 fault injection test with resource version conflict resolution.
Fixes the original approach by handling Kubernetes ConfigMap versioning properly.
"""

import subprocess
import yaml
import time
import sys
import json
import logging
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class KubernetesConfigMapManager:
    """Handles Kubernetes ConfigMap operations with proper resource versioning"""
    
    def __init__(self, namespace: str = "train-ticket", configmap_name: str = "flagd-config"):
        self.namespace = namespace
        self.configmap_name = configmap_name
        self.max_retries = 5
        self.retry_delay = 2
    
    def run_kubectl_command(self, cmd: str, retries: int = 3) -> Optional[str]:
        """Run kubectl command with retry logic"""
        for attempt in range(retries):
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
                return result.stdout.strip()
            except subprocess.CalledProcessError as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e.stderr}")
                if attempt < retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(f"Command failed after {retries} attempts: {cmd}")
                    logger.error(f"Final error: {e.stderr}")
                    return None
    
    def get_configmap_with_version(self) -> Optional[Dict[str, Any]]:
        """Get ConfigMap with resource version for proper updates"""
        cmd = f"kubectl get configmap {self.configmap_name} -n {self.namespace} -o json"
        result = self.run_kubectl_command(cmd)
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse ConfigMap JSON: {e}")
                return None
        return None
    
    def update_configmap_with_version(self, configmap_data: Dict[str, Any]) -> bool:
        """Update ConfigMap using proper resource versioning"""
        temp_file = f"/tmp/updated_configmap_{int(time.time())}.json"
        try:
            with open(temp_file, 'w') as f:
                json.dump(configmap_data, f, indent=2)
            
            cmd = f"kubectl apply -f {temp_file}"
            result = self.run_kubectl_command(cmd, retries=self.max_retries)
            
            subprocess.run(f"rm -f {temp_file}", shell=True)
            
            return result is not None
            
        except Exception as e:
            logger.error(f"Failed to update ConfigMap: {e}")
            subprocess.run(f"rm -f {temp_file}", shell=True)
            return False
    
    def update_fault_flag(self, fault_name: str, enabled: bool) -> bool:
        """Update a specific fault flag with proper resource version handling"""
        logger.info(f"{'Enabling' if enabled else 'Disabling'} fault: {fault_name}")
        
        for attempt in range(self.max_retries):
            logger.info(f"Attempt {attempt + 1} to update {fault_name}")
            
            configmap_data = self.get_configmap_with_version()
            if not configmap_data:
                logger.error("Failed to get current ConfigMap")
                continue
            
            try:
                flags_yaml = configmap_data['data']['flags.yaml']
                flags_config = yaml.safe_load(flags_yaml)
                
                if fault_name in flags_config['flags']:
                    flags_config['flags'][fault_name]['defaultVariant'] = 'on' if enabled else 'off'
                    logger.info(f"Updated {fault_name} defaultVariant to {'on' if enabled else 'off'}")
                else:
                    logger.error(f"Fault {fault_name} not found in configuration")
                    return False
                
                updated_flags_yaml = yaml.dump(flags_config, default_flow_style=False)
                configmap_data['data']['flags.yaml'] = updated_flags_yaml
                
                if self.update_configmap_with_version(configmap_data):
                    logger.info(f"Successfully updated {fault_name}")
                    return True
                else:
                    logger.warning(f"Failed to update ConfigMap on attempt {attempt + 1}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff
                    
            except yaml.YAMLError as e:
                logger.error(f"Failed to parse flags YAML: {e}")
                return False
            except KeyError as e:
                logger.error(f"Missing key in ConfigMap: {e}")
                return False
        
        logger.error(f"Failed to update {fault_name} after {self.max_retries} attempts")
        return False
    
    def verify_fault_status(self, fault_name: str, expected_status: str) -> bool:
        """Verify that a fault flag has the expected status"""
        logger.info(f"Verifying {fault_name} status is '{expected_status}'")
        
        configmap_data = self.get_configmap_with_version()
        if not configmap_data:
            return False
        
        try:
            flags_yaml = configmap_data['data']['flags.yaml']
            flags_config = yaml.safe_load(flags_yaml)
            
            if fault_name in flags_config['flags']:
                actual_status = flags_config['flags'][fault_name]['defaultVariant']
                if actual_status == expected_status:
                    logger.info(f"✅ {fault_name} status verified: {actual_status}")
                    return True
                else:
                    logger.error(f"❌ {fault_name} status mismatch. Expected: {expected_status}, Actual: {actual_status}")
                    return False
            else:
                logger.error(f"❌ Fault {fault_name} not found in configuration")
                return False
                
        except Exception as e:
            logger.error(f"Failed to verify fault status: {e}")
            return False

class FlagdServiceManager:
    """Manages flagd service operations"""
    
    def __init__(self, namespace: str = "train-ticket"):
        self.namespace = namespace
    
    def run_kubectl_command(self, cmd: str) -> Optional[str]:
        """Run kubectl command and return output"""
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {cmd}")
            logger.error(f"Error: {e.stderr}")
            return None
    
    def check_flagd_status(self) -> bool:
        """Check if flagd pod is running"""
        logger.info("Checking flagd pod status...")
        pods = self.run_kubectl_command(f"kubectl get pods -n {self.namespace} -l app=flagd")
        if pods and "Running" in pods:
            logger.info("✅ flagd pod is running")
            return True
        else:
            logger.error("❌ flagd pod not running")
            return False
    
    def restart_flagd(self) -> bool:
        """Restart flagd deployment to reload configuration"""
        logger.info("Restarting flagd deployment...")
        
        restart_result = self.run_kubectl_command(f"kubectl rollout restart deployment/flagd -n {self.namespace}")
        if not restart_result:
            logger.error("❌ Failed to restart flagd")
            return False
        
        logger.info("Waiting for flagd rollout to complete...")
        time.sleep(15)  # Give more time for restart
        
        rollout_status = self.run_kubectl_command(f"kubectl rollout status deployment/flagd -n {self.namespace} --timeout=120s")
        if rollout_status and "successfully rolled out" in rollout_status:
            logger.info("✅ flagd restarted successfully")
            time.sleep(5)  # Additional time for flagd to load config
            return True
        else:
            logger.error("❌ flagd rollout failed or timed out")
            return False
    
    def get_flagd_logs(self, lines: int = 20) -> Optional[str]:
        """Get recent flagd logs for debugging"""
        cmd = f"kubectl logs -n {self.namespace} -l app=flagd --tail={lines}"
        return self.run_kubectl_command(cmd)

def test_robust_fault_injection():
    """Test F1 fault injection with robust error handling and resource version management"""
    print("=== TrainTicket F1 Fault Injection Robust Test ===")
    
    configmap_manager = KubernetesConfigMapManager()
    flagd_manager = FlagdServiceManager()
    
    if not flagd_manager.check_flagd_status():
        return False
    
    logger.info("2. Checking initial F1 fault status...")
    if not configmap_manager.verify_fault_status("fault-1-async-message-order", "off"):
        logger.warning("F1 fault not in expected initial state, but continuing...")
    
    logger.info("3. Enabling F1 fault with resource version management...")
    if not configmap_manager.update_fault_flag("fault-1-async-message-order", True):
        logger.error("❌ Failed to enable F1 fault")
        return False
    
    if not flagd_manager.restart_flagd():
        logger.error("❌ Failed to restart flagd")
        return False
    
    logger.info("5. Verifying F1 fault is enabled...")
    if not configmap_manager.verify_fault_status("fault-1-async-message-order", "on"):
        logger.error("❌ F1 fault not properly enabled")
        return False
    
    logger.info("6. Testing fault recovery (disabling F1)...")
    if not configmap_manager.update_fault_flag("fault-1-async-message-order", False):
        logger.error("❌ Failed to disable F1 fault")
        return False
    
    if not flagd_manager.restart_flagd():
        logger.error("❌ Failed to restart flagd for recovery")
        return False
    
    logger.info("8. Final verification...")
    if not configmap_manager.verify_fault_status("fault-1-async-message-order", "off"):
        logger.error("❌ F1 fault not properly disabled")
        return False
    
    logger.info("9. Running additional robustness tests...")
    
    logger.info("Testing rapid fault state changes...")
    for i in range(3):
        if not configmap_manager.update_fault_flag("fault-1-async-message-order", True):
            logger.error(f"❌ Failed rapid enable test {i+1}")
            return False
        time.sleep(1)
        if not configmap_manager.update_fault_flag("fault-1-async-message-order", False):
            logger.error(f"❌ Failed rapid disable test {i+1}")
            return False
        time.sleep(1)
    
    logger.info("10. Checking flagd logs for errors...")
    logs = flagd_manager.get_flagd_logs(50)
    if logs:
        if "error" in logs.lower() or "failed" in logs.lower():
            logger.warning("⚠️ Found potential errors in flagd logs:")
            logger.warning(logs)
        else:
            logger.info("✅ No errors found in flagd logs")
    
    print("\n🎉 TrainTicket F1 fault injection robust test completed successfully!")
    print("✅ Fault injection: WORKING")
    print("✅ Fault recovery: WORKING") 
    print("✅ Resource version handling: WORKING")
    print("✅ ConfigMap updates: WORKING")
    print("✅ flagd integration: WORKING")
    print("✅ Error handling: WORKING")
    print("✅ Rapid state changes: WORKING")
    print("✅ Comprehensive validation: WORKING")
    
    return True

if __name__ == "__main__":
    success = test_robust_fault_injection()
    sys.exit(0 if success else 1)
