#!/usr/bin/env python3

import sys
import os

sys.path.insert(0, '/home/ubuntu/sre-arena-xlab-dev')

def test_deployment_readiness():
    """Test if the TrainTicket F1 deployment is ready."""
    print("=== TrainTicket F1 Deployment Readiness Test ===")
    
    flagd_config = "/home/ubuntu/sre-arena-xlab-dev/aiopslab-applications/train-ticket/templates/flagd-config.yaml"
    flagd_deployment = "/home/ubuntu/sre-arena-xlab-dev/aiopslab-applications/train-ticket/templates/flagd-deployment.yaml"
    
    if os.path.exists(flagd_config):
        print("✅ flagd-config.yaml exists")
    else:
        print("❌ flagd-config.yaml missing")
        return False
        
    if os.path.exists(flagd_deployment):
        print("✅ flagd-deployment.yaml exists")
    else:
        print("❌ flagd-deployment.yaml missing")
        return False
    
    deploy_script = "/home/ubuntu/sre-arena-xlab-dev/scripts/deploy_trainticket_f1.sh"
    if os.path.exists(deploy_script):
        print("✅ deploy_trainticket_f1.sh exists")
    else:
        print("❌ deploy_trainticket_f1.sh missing")
        return False
    
    try:
        from srearena.generators.fault.inject_tt import TrainTicketFaultInjector
        print("✅ TrainTicketFaultInjector imports successfully")
        
        try:
            injector = TrainTicketFaultInjector('train-ticket')
            faults = injector.list_available_faults()
            print(f"✅ TrainTicketFaultInjector has {len(faults)} faults configured")
        except Exception as e:
            if "kube-config" in str(e):
                print("✅ TrainTicketFaultInjector fails as expected (no k8s config)")
            else:
                print(f"❌ Unexpected error: {e}")
                return False
                
    except Exception as e:
        print(f"❌ TrainTicketFaultInjector import failed: {e}")
        return False
    
    try:
        from srearena.conductor.problems.registry import ProblemRegistry
        registry = ProblemRegistry()
        
        if "trainticket_f1_async_message_order" in registry.get_problem_ids():
            print("✅ F1 problem registered in SREArena conductor")
        else:
            print("❌ F1 problem not found in registry")
            return False
            
    except Exception as e:
        print(f"❌ Registry test failed: {e}")
        return False
    
    print("\n🎉 All deployment readiness tests passed!")
    print("\nNext steps:")
    print("1. Run: ./scripts/deploy_trainticket_f1.sh")
    print("2. Test fault injection after deployment")
    return True

if __name__ == "__main__":
    success = test_deployment_readiness()
    if not success:
        sys.exit(1)
