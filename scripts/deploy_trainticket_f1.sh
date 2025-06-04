#!/bin/bash

set -e

echo "=== TrainTicket F1 Fault Injection Deployment Script ==="

NAMESPACE="train-ticket"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Project root: $PROJECT_ROOT"
echo "Target namespace: $NAMESPACE"

echo "1. Creating namespace..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

echo "2. Deploying flagd infrastructure..."
kubectl apply -f "$PROJECT_ROOT/aiopslab-applications/train-ticket/templates/flagd-config.yaml"
kubectl apply -f "$PROJECT_ROOT/aiopslab-applications/train-ticket/templates/flagd-deployment.yaml"

echo "3. Waiting for flagd to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/flagd -n $NAMESPACE

echo "4. Checking flagd status..."
kubectl get pods -l app=flagd -n $NAMESPACE
kubectl get svc flagd -n $NAMESPACE

echo "5. Testing fault injector..."
cd "$PROJECT_ROOT"
python3 -c "
from srearena.generators.fault.inject_tt import TrainTicketFaultInjector
injector = TrainTicketFaultInjector('$NAMESPACE')
health = injector.health_check()
print('Health check:', health)
print('Available faults:', len(injector.list_available_faults()))
print('F1 status:', injector.get_fault_status('fault-1-async-message-order'))
"

echo "✅ TrainTicket F1 infrastructure deployed successfully!"
echo ""
echo "Next steps:"
echo "1. Deploy TrainTicket services with OpenFeature integration"
echo "2. Test F1 fault injection: python3 -c \"from srearena.generators.fault.inject_tt import TrainTicketFaultInjector; injector = TrainTicketFaultInjector('$NAMESPACE'); injector.inject_fault('fault-1-async-message-order')\""
echo "3. Test F1 recovery: python3 -c \"from srearena.generators.fault.inject_tt import TrainTicketFaultInjector; injector = TrainTicketFaultInjector('$NAMESPACE'); injector.recover_fault('fault-1-async-message-order')\""
