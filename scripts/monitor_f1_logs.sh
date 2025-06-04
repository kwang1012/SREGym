#!/bin/bash

NAMESPACE="train-ticket"

echo "=== TrainTicket F1 Fault Monitoring ==="
echo "Monitoring logs for F1 fault injection evidence..."
echo "Press Ctrl+C to stop monitoring"
echo ""

services=("ts-cancel-service" "ts-inside-payment-service" "ts-order-service")

monitor_service() {
    local service=$1
    echo "--- Monitoring $service ---"
    
    kubectl logs -f deployment/$service -n $NAMESPACE 2>/dev/null | while read line; do
        if [[ $line == *"F1 FAULT"* ]] || [[ $line == *"Feature Flags"* ]] || [[ $line == *"8-second delay"* ]]; then
            echo "[$service] $line"
        fi
    done &
}

for service in "${services[@]}"; do
    if kubectl get deployment $service -n $NAMESPACE >/dev/null 2>&1; then
        monitor_service $service
        echo "Started monitoring $service"
    else
        echo "Warning: $service deployment not found"
    fi
done

echo ""
echo "Monitoring active. Look for lines containing:"
echo "- 'F1 FAULT INJECTED'"
echo "- 'Feature Flags'"
echo "- '8-second delay'"
echo ""

wait
