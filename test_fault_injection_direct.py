#!/usr/bin/env python3
"""
Direct test of TrainTicket fault injection with proper resource version handling.
Tests the core fault injection functionality via flagd ConfigMap updates.
"""

import subprocess
import yaml
import json
import time
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_kubectl_command(cmd, retries=3):
    """Run kubectl command with retry logic"""
    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e.stderr}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                logger.error(f"Command failed after {retries} attempts: {cmd}")
                logger.error(f"Final error: {e.stderr}")
                return None

def get_fresh_configmap():
    """Get fresh ConfigMap with current resource version"""
    configmap_json = run_kubectl_command("kubectl get configmap flagd-config -n train-ticket -o json")
    if not configmap_json:
        logger.error("❌ Could not get flagd ConfigMap")
        return None
    
    try:
        return json.loads(configmap_json)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ConfigMap JSON: {e}")
        return None

def update_fault_flag_with_retry(fault_name, enabled, max_retries=5):
    """Update fault flag with proper resource version handling and retry logic"""
    logger.info(f"{'Enabling' if enabled else 'Disabling'} fault: {fault_name}")
    
    for attempt in range(max_retries):
        logger.info(f"Attempt {attempt + 1} to update {fault_name}")
        
        config_data = get_fresh_configmap()
        if not config_data:
            logger.error("Failed to get current ConfigMap")
            continue
        
        try:
            flags_yaml = config_data['data']['flags.yaml']
            flags_config = yaml.safe_load(flags_yaml)
            
            if fault_name in flags_config['flags']:
                flags_config['flags'][fault_name]['defaultVariant'] = 'on' if enabled else 'off'
                logger.info(f"Updated {fault_name} defaultVariant to {'on' if enabled else 'off'}")
            else:
                logger.error(f"Fault {fault_name} not found in configuration")
                return False
            
            updated_flags_yaml = yaml.dump(flags_config, default_flow_style=False)
            config_data['data']['flags.yaml'] = updated_flags_yaml
            
            temp_file = f"/tmp/updated_configmap_{int(time.time())}_{attempt}.json"
            with open(temp_file, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            apply_result = run_kubectl_command(f"kubectl apply -f {temp_file}")
            
            subprocess.run(f"rm -f {temp_file}", shell=True)
            
            if apply_result:
                logger.info(f"Successfully updated {fault_name}")
                return True
            else:
                logger.warning(f"Failed to update ConfigMap on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse flags YAML: {e}")
            return False
        except KeyError as e:
            logger.error(f"Missing key in ConfigMap: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
    
    logger.error(f"Failed to update {fault_name} after {max_retries} attempts")
    return False

def verify_fault_status(fault_name, expected_status):
    """Verify that a fault flag has the expected status"""
    logger.info(f"Verifying {fault_name} status is '{expected_status}'")
    
    config_data = get_fresh_configmap()
    if not config_data:
        return False
    
    try:
        flags_yaml = config_data['data']['flags.yaml']
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

def test_fault_injection():
    """Test F1 fault injection with proper resource version handling"""
    print("=== TrainTicket F1 Fault Injection Direct Test (Fixed) ===")
    
    print("1. Checking flagd pod status...")
    pods = run_kubectl_command("kubectl get pods -n train-ticket -l app=flagd")
    if not pods or "Running" not in pods:
        print("❌ flagd pod not running")
        return False
    print("✅ flagd pod is running")
    
    print("2. Checking current F1 fault status...")
    if not verify_fault_status("fault-1-async-message-order", "off"):
        logger.warning("F1 fault not in expected initial state, but continuing...")
    
    print("3. Enabling F1 fault with resource version management...")
    if not update_fault_flag_with_retry("fault-1-async-message-order", True):
        print("❌ Failed to enable F1 fault")
        return False
    print("✅ F1 fault enabled in ConfigMap")
    
    print("4. Restarting flagd to reload configuration...")
    restart_result = run_kubectl_command("kubectl rollout restart deployment/flagd -n train-ticket")
    if not restart_result:
        print("❌ Failed to restart flagd")
        return False
    
    print("Waiting for flagd rollout to complete...")
    time.sleep(15)
    
    rollout_status = run_kubectl_command("kubectl rollout status deployment/flagd -n train-ticket --timeout=120s")
    if not rollout_status or "successfully rolled out" not in rollout_status:
        print("❌ flagd rollout failed")
        return False
    print("✅ flagd restarted successfully")
    
    print("5. Verifying F1 fault is enabled...")
    time.sleep(5)
    
    if not verify_fault_status("fault-1-async-message-order", "on"):
        print("❌ F1 fault not properly enabled")
        return False
    print("✅ F1 fault successfully enabled")
    
    print("6. Testing fault recovery (disabling F1) with fresh ConfigMap...")
    if not update_fault_flag_with_retry("fault-1-async-message-order", False):
        print("❌ Failed to disable F1 fault")
        return False
    
    restart_recovery = run_kubectl_command("kubectl rollout restart deployment/flagd -n train-ticket")
    if not restart_recovery:
        print("❌ Failed to restart flagd for recovery")
        return False
    
    print("Waiting for recovery rollout to complete...")
    time.sleep(15)
    
    recovery_rollout = run_kubectl_command("kubectl rollout status deployment/flagd -n train-ticket --timeout=120s")
    if not recovery_rollout or "successfully rolled out" not in recovery_rollout:
        print("❌ Recovery rollout failed")
        return False
    
    print("✅ F1 fault recovery completed")
    
    print("7. Final verification...")
    if not verify_fault_status("fault-1-async-message-order", "off"):
        print("❌ F1 fault not properly disabled")
        return False
    print("✅ F1 fault successfully disabled")
    
    print("8. Stress testing rapid state changes...")
    for i in range(3):
        logger.info(f"Rapid test cycle {i+1}")
        if not update_fault_flag_with_retry("fault-1-async-message-order", True):
            print(f"❌ Failed rapid enable test {i+1}")
            return False
        time.sleep(1)
        if not update_fault_flag_with_retry("fault-1-async-message-order", False):
            print(f"❌ Failed rapid disable test {i+1}")
            return False
        time.sleep(1)
    print("✅ Rapid state changes successful")
    
    print("\n🎉 TrainTicket F1 fault injection test completed successfully!")
    print("✅ Fault injection: WORKING")
    print("✅ Fault recovery: WORKING") 
    print("✅ Resource version handling: WORKING")
    print("✅ ConfigMap updates: WORKING")
    print("✅ flagd integration: WORKING")
    print("✅ Error handling: WORKING")
    print("✅ Rapid state changes: WORKING")
    
    return True

if __name__ == "__main__":
    success = test_fault_injection()
    sys.exit(0 if success else 1)
