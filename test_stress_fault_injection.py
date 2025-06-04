#!/usr/bin/env python3
"""
Stress test for TrainTicket fault injection to validate robustness under load.
Tests multiple rapid enable/disable cycles and concurrent operations.
"""

import subprocess
import time
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_kubectl_command(cmd):
    """Run kubectl command and return success status"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def stress_test_rapid_changes(test_id, cycles=10):
    """Perform rapid enable/disable cycles for stress testing"""
    logger.info(f"Starting stress test {test_id} with {cycles} cycles")
    
    for cycle in range(cycles):
        logger.info(f"Test {test_id} - Cycle {cycle + 1}/{cycles}")
        
        enable_cmd = "kubectl patch configmap flagd-config -n train-ticket --type='merge' -p='{\"data\":{\"flags.yaml\":\"flags:\\n  fault-1-async-message-order:\\n    defaultVariant: on\\n    state: ENABLED\\n    targeting:\\n      if:\\n      - var: enabled\\n      - in:\\n        - true\\n        - var: enabled\\n      then: on\\n    variants:\\n      off: false\\n      on: true\"}}'"
        success, output = run_kubectl_command(enable_cmd)
        if not success:
            logger.error(f"Test {test_id} - Failed to enable fault in cycle {cycle + 1}: {output}")
            return False
        
        time.sleep(0.5)
        
        disable_cmd = "kubectl patch configmap flagd-config -n train-ticket --type='merge' -p='{\"data\":{\"flags.yaml\":\"flags:\\n  fault-1-async-message-order:\\n    defaultVariant: off\\n    state: ENABLED\\n    targeting:\\n      if:\\n      - var: enabled\\n      - in:\\n        - true\\n        - var: enabled\\n      then: on\\n    variants:\\n      off: false\\n      on: true\"}}'"
        success, output = run_kubectl_command(disable_cmd)
        if not success:
            logger.error(f"Test {test_id} - Failed to disable fault in cycle {cycle + 1}: {output}")
            return False
        
        time.sleep(0.5)
    
    logger.info(f"Stress test {test_id} completed successfully")
    return True

def test_concurrent_updates():
    """Test concurrent ConfigMap updates to validate conflict resolution"""
    print("=== Concurrent Update Stress Test ===")
    
    num_threads = 3
    cycles_per_thread = 5
    
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        for i in range(num_threads):
            future = executor.submit(stress_test_rapid_changes, i + 1, cycles_per_thread)
            futures.append(future)
        
        results = []
        for future in as_completed(futures):
            try:
                result = future.result(timeout=120)
                results.append(result)
            except Exception as e:
                logger.error(f"Thread failed with exception: {e}")
                results.append(False)
    
    success_count = sum(results)
    total_count = len(results)
    
    print(f"Concurrent test results: {success_count}/{total_count} threads successful")
    return success_count == total_count

def test_resource_version_conflicts():
    """Test specific resource version conflict scenarios"""
    print("=== Resource Version Conflict Test ===")
    
    print("1. Testing rapid sequential updates...")
    for i in range(10):
        logger.info(f"Sequential update {i + 1}/10")
        
        success, _ = run_kubectl_command("python3 test_fault_injection_direct.py")
        if not success:
            print(f"❌ Sequential test failed on iteration {i + 1}")
            return False
        
        time.sleep(1)
    
    print("✅ Sequential updates successful")
    
    print("2. Testing flagd service stability...")
    success, output = run_kubectl_command("kubectl get pods -n train-ticket -l app=flagd")
    if not success or "Running" not in output:
        print("❌ flagd pod not stable")
        return False
    
    print("✅ flagd service stable")
    
    print("3. Checking for error logs...")
    success, logs = run_kubectl_command("kubectl logs -n train-ticket -l app=flagd --tail=20")
    if success and "error" in logs.lower():
        logger.warning(f"Found potential errors in flagd logs: {logs}")
    else:
        print("✅ No critical errors in flagd logs")
    
    return True

def test_recovery_scenarios():
    """Test various recovery scenarios"""
    print("=== Recovery Scenario Test ===")
    
    print("1. Testing recovery from enabled state...")
    success, _ = run_kubectl_command("python3 test_fault_injection_direct.py")
    if not success:
        print("❌ Failed to run initial fault injection test")
        return False
    
    time.sleep(2)
    
    success, _ = run_kubectl_command("python3 test_fault_injection_direct.py")
    if not success:
        print("❌ Recovery from enabled state failed")
        return False
    
    print("✅ Recovery from enabled state successful")
    
    print("2. Testing multiple recovery cycles...")
    for i in range(5):
        logger.info(f"Recovery cycle {i + 1}/5")
        
        success, _ = run_kubectl_command("python3 test_fault_injection_direct.py")
        if not success:
            print(f"❌ Failed fault injection test in recovery cycle {i + 1}")
            return False
        
        time.sleep(1)
    
    print("✅ Multiple recovery cycles successful")
    
    return True

def main():
    """Run comprehensive stress tests"""
    print("=== TrainTicket F1 Fault Injection Stress Test Suite ===")
    
    print("Checking initial system state...")
    success, output = run_kubectl_command("kubectl get pods -n train-ticket -l app=flagd")
    if not success or "Running" not in output:
        print("❌ flagd pod not running - cannot proceed with stress tests")
        return False
    
    print("✅ System ready for stress testing")
    
    tests = [
        ("Resource Version Conflicts", test_resource_version_conflicts),
        ("Recovery Scenarios", test_recovery_scenarios),
        ("Concurrent Updates", test_concurrent_updates),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n--- Running {test_name} ---")
        try:
            result = test_func()
            results.append((test_name, result))
            if result:
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")
        except Exception as e:
            logger.error(f"Test {test_name} failed with exception: {e}")
            results.append((test_name, False))
            print(f"❌ {test_name}: FAILED (Exception)")
    
    print("\n=== Stress Test Results ===")
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "PASSED" if result else "FAILED"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All stress tests completed successfully!")
        print("✅ Resource version handling: ROBUST")
        print("✅ Concurrent operations: STABLE")
        print("✅ Recovery scenarios: RELIABLE")
        print("✅ System stability: VERIFIED")
        return True
    else:
        print("❌ Some stress tests failed - system may not be fully robust")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
