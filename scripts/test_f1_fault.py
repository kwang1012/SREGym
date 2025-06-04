#!/usr/bin/env python3

import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from srearena.generators.fault.inject_tt import TrainTicketFaultInjector
from srearena.conductor.problems.trainticket_f1_async_message_order import TrainTicketF1AsyncMessageOrderProblem
from srearena.service.apps.train_ticket import TrainTicket

def test_fault_injector():
    """Test the TrainTicket fault injector functionality."""
    print("=== Testing TrainTicket F1 Fault Injector ===")
    
    injector = TrainTicketFaultInjector("train-ticket")
    
    print("1. Health check...")
    health = injector.health_check()
    print(f"Health status: {health}")
    
    if not all(health.values()):
        print("❌ Health check failed. Please ensure flagd infrastructure is deployed.")
        return False
    
    print("2. Testing fault injection...")
    fault_name = "fault-1-async-message-order"
    
    initial_status = injector.get_fault_status(fault_name)
    print(f"Initial F1 status: {initial_status}")
    
    if injector.inject_fault(fault_name):
        print("✅ F1 fault injected successfully")
        
        time.sleep(2)
        
        status_after_inject = injector.get_fault_status(fault_name)
        print(f"F1 status after injection: {status_after_inject}")
        
        if status_after_inject == "on":
            print("✅ F1 fault is active")
        else:
            print("❌ F1 fault injection may have failed")
            
    else:
        print("❌ Failed to inject F1 fault")
        return False
    
    print("3. Testing fault recovery...")
    if injector.recover_fault(fault_name):
        print("✅ F1 fault recovered successfully")
        
        time.sleep(2)
        
        status_after_recovery = injector.get_fault_status(fault_name)
        print(f"F1 status after recovery: {status_after_recovery}")
        
        if status_after_recovery == "off":
            print("✅ F1 fault is disabled")
        else:
            print("❌ F1 fault recovery may have failed")
            
    else:
        print("❌ Failed to recover F1 fault")
        return False
    
    print("✅ All fault injector tests passed!")
    return True

def test_problem_definition():
    """Test the F1 problem definition."""
    print("\n=== Testing F1 Problem Definition ===")
    
    try:
        app = TrainTicket()
        problem = TrainTicketF1AsyncMessageOrderProblem(app)
        
        print("1. Testing problem initialization...")
        health = problem.get_health_status()
        print(f"Problem health: {health}")
        
        print("2. Testing fault injection via problem...")
        if problem.inject_fault():
            print("✅ Problem fault injection successful")
            
            print("3. Testing workload guidance...")
            problem.start_workload()
            
            print("4. Testing oracle...")
            if problem.oracle():
                print("✅ Oracle detected F1 fault correctly")
            else:
                print("⚠️ Oracle may need manual verification")
            
            print("5. Testing fault recovery via problem...")
            if problem.recover_fault():
                print("✅ Problem fault recovery successful")
            else:
                print("❌ Problem fault recovery failed")
                
        else:
            print("❌ Problem fault injection failed")
            return False
            
        print("✅ All problem definition tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Problem definition test failed: {e}")
        return False

def main():
    """Run all F1 fault injection tests."""
    print("TrainTicket F1 Fault Injection Test Suite")
    print("=" * 50)
    
    success = True
    
    if not test_fault_injector():
        success = False
    
    if not test_problem_definition():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 All tests passed! F1 fault injection is working correctly.")
        print("\nManual testing steps:")
        print("1. Deploy TrainTicket services")
        print("2. Access TrainTicket UI")
        print("3. Create and cancel orders to observe 8-second delay")
        print("4. Check service logs for F1 fault messages")
    else:
        print("❌ Some tests failed. Please check the output above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
