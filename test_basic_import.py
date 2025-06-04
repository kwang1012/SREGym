#!/usr/bin/env python3

import sys
import os

sys.path.insert(0, '/home/ubuntu/sre-arena-xlab-dev')

def test_imports():
    """Test basic imports of our new modules."""
    print("=== Testing Basic Imports ===")
    
    try:
        from srearena.generators.fault.inject_tt import TrainTicketFaultInjector
        print("✅ TrainTicketFaultInjector imported successfully")
        
        injector = TrainTicketFaultInjector('train-ticket')
        faults = injector.list_available_faults()
        print(f"✅ TrainTicketFaultInjector instantiated: {len(faults)} faults available")
        
        details = injector.get_fault_details()
        if "fault-1-async-message-order" in details:
            print("✅ F1 fault details available")
        else:
            print("❌ F1 fault details missing")
            return False
        
    except Exception as e:
        print(f"❌ TrainTicketFaultInjector import failed: {e}")
        return False
    
    try:
        from srearena.conductor.problems.trainticket_f1_async_message_order import TrainTicketF1AsyncMessageOrderProblem
        print("✅ TrainTicketF1AsyncMessageOrderProblem imported successfully")
        
    except Exception as e:
        print(f"❌ TrainTicketF1AsyncMessageOrderProblem import failed: {e}")
        return False
    
    try:
        from srearena.service.apps.train_ticket import TrainTicket
        print("✅ TrainTicket app class imported successfully")
        
        app = TrainTicket()
        print(f"✅ TrainTicket app instantiated: namespace={app.namespace}")
        
    except Exception as e:
        print(f"❌ TrainTicket app import failed: {e}")
        return False
    
    try:
        from srearena.conductor.problems.registry import ProblemRegistry
        registry = ProblemRegistry()
        
        if "trainticket_f1_async_message_order" in registry.get_problem_ids():
            print("✅ F1 problem registered in registry")
        else:
            print("❌ F1 problem not found in registry")
            return False
            
    except Exception as e:
        print(f"❌ Registry import failed: {e}")
        return False
    
    print("✅ All imports successful!")
    return True

if __name__ == "__main__":
    success = test_imports()
    if not success:
        sys.exit(1)
    print("\n🎉 Basic import tests passed!")
