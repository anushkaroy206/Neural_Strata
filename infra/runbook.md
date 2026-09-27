# Neural Strata Phase 12 Incident Procedures

## Overview

This document defines the Phase 12 incident response procedures for the Neural Strata edge server. It covers the five disaster scenarios and provides step-by-step guidance for operators responding to issues in the three-plane architecture.

## Table of Contents

1. [Scenario 1: Missing Hub During Bench Mode (15s Delay)](#scenario-1-missing-hub-during-bench-mode-15s-delay)
2. [Scenario 2: Missing Hub During Bench Mode (30s Delay)](#scenario-2-missing-hub-during-bench-mode-30s-delay)
3. [Scenario 3: Gateway Reconnect After Disconnect](#scenario-3-gateway-reconnect-after-disconnect)
4. [Scenario 4: Outbox Depletion Warnings](#scenario-4-outbox-depletion-warnings)
5. [Scenario 5: Gateway Error Mode](#scenario-5-gateway-error-mode)
6. [General Emergency Procedures](#general-emergency-procedures)
7. [API Contract Violations](#api-contract-violations)
8. [Blackbox Gateway Recovery](#blackbox-gateway-recovery)
9. [Three-Plane Architecture Sync](#three-plane-architecture-sync)

---

## Scenario 1: Missing Hub During Bench Mode (15s Delay)

### Situation
The system is running in bench mode (`--no-serial`) and detects that the hub has been missing for more than 15 seconds without external feed.

### Symptoms
- Blackbox panel shows "SIM mode" for >15 seconds
- No USB serial activity detected
- `/api/extended` reports mode="SIM"
- Alert: "SIM mode active for 15+ seconds - no external feed" (WARNING level)

### Immediate Actions
1. **Verify Hub Connection**
   ```bash
   # Check if USB hub is connected
   lsusb | grep -i hub
   dmesg | grep -i hub
   ```

2. **Check Serial Port**
   ```bash
   # List available serial ports
   ls /dev/tty*
   
   # Check if port is responding
   sudo chmod 666 /dev/ttyUSB0
   ```

3. **Inspect System Status**
   ```bash
   # Check current system status
   curl http://localhost:5000/api/extended | jq '.mode'
   curl http://localhost:5000/api/health | jq '.system_status'
   ```

### Recovery Steps
1. **Reconnect Hub**
   - Physically reconnect the USB hub
   - Wait 2-3 seconds for detection
   - System should auto-transition to LIVE mode

2. **Verify Recovery**
   ```bash
   # Monitor mode transition
   watch -n 0.5 'curl -s http://localhost:5000/api/extended | jq .mode'
   
   # Check telemetry
   curl http://localhost:5000/api/telemetry | jq '.system_status'
   ```

### Preventive Measures
- Schedule regular hub connection tests
- Monitor USB port status during operations
- Implement hub presence detection scripts

---

## Scenario 2: Missing Hub During Bench Mode (30s Delay)

### Situation
Extended period (>30 seconds) without hub connection in bench mode.

### Symptoms
- CRITICAL alert: "SIM mode active for 30+ seconds - extended bench mode"
- Dashboard shows extended SIM mode
- Potential data loss if in production

### Immediate Actions
1. **Emergency Hub Connection**
   - Immediately connect the hub
   - Document timing of connection
   - Note any error messages during reconnect

2. **System State Assessment**
   ```bash
   # Check system state
   curl http://localhost:5000/api/health | jq '.system_status'
   
   # Verify telemetry
   curl http://localhost:5000/api/telemetry | jq '.nodes'
   ```

3. **Validate Data Integrity**
   ```bash
   # Check for missing data periods
   curl http://localhost:5000/api/history | jq 'length'
   
   # Verify model windows
   curl http://localhost:5000/api/ai | jq '.cascade'
   ```

### Recovery Steps
1. **Full System Reset (if needed)**
   ```bash
   # Re-arm system
   curl -X POST http://localhost:5000/api/reset
   
   # Verify re-arm
   curl http://localhost:5000/api/health | jq '.ttf_seconds'
   ```

2. **Monitor System Recovery**
   - Watch for system warming up (60 seconds)
   - Verify model forecasts return
   - Check alert status

### Preventive Measures
- Set up hub monitoring alerts
- Implement automated hub reconnection scripts
- Schedule regular system health checks

---

## Scenario 3: Gateway Reconnect After Disconnect

### Situation
Gateway disconnects and reconnects (serial connection drops and re-establishes).

### Symptoms
- `/api/blackbox/status` shows gateway: false
- `/api/blackbox/chunks` returns error
- `/api/cloud/status` indicates no gateway
- Alert: gateway connectivity issues

### Immediate Actions
1. **Check Gateway Status**
   ```bash
   # Check gateway status
   curl http://localhost:5000/api/blackbox/status | jq '.gateway'
   
   # Check recent chunks
   curl 'http://localhost:5000/api/blackbox/chunks?held=1'
   ```

2. **Verify Serial Port**
   ```bash
   # Check serial port activity
   tail -f /var/log/syslog | grep -i serial
   
   # List active serial connections
   sudo lsof /dev/tty*
   ```

3. **Assess Data Loss**
   ```bash
   # Check for gaps in history
   curl http://localhost:5000/api/history | python3 -m json.tool
   
   # Verify model continuity
   curl http://localhost:5000/api/ai | jq '.cascade.disagreement'
   ```

### Recovery Steps
1. **Force Gateway Recovery**
   ```bash
   # Attempt gateway restart
   curl -X POST http://localhost:5000/api/blackbox/recover
   
   # Check recovery status
   curl http://localhost:5000/api/blackbox/status
   ```

2. **Validate Three-Plane Sync**
   ```bash
   # Check all planes
   curl http://localhost:5000/api/extended | jq '.mode'
   curl http://localhost:5000/api/cloud/status | jq '.gateway'
   curl http://localhost:5000/api/health | jq '.system_status'
   ```

3. **Route Convergence**
   - Monitor route A/B status convergence
   - Verify junction LED patterns
   - Check alert system stabilization

### Preventive Measures
- Implement serial port monitoring
- Set up automatic gateway restart scripts
- Document reconnection procedures

---

## Scenario 4: Outbox Depletion Warnings

### Situation
Outbox fills with chunks that cannot be released due to cloud sync failures.

### Symptoms
- `/api/cloud/status` shows high `held` count
- `/api/extended` shows outbox depth warnings
- Alert: "No releasable chunks - sync may be stuck"
- `/api/blackbox/status` ledger shows accumulation

### Immediate Actions
1. **Check Cloud Configuration**
   ```bash
   # Verify cloud settings
   grep -r "NS_CLOUD" /etc/environment
   
   # Check cloud client status
   curl http://localhost:5000/api/cloud/status | jq '.cloud'
   ```

2. **Assess Outbox State**
   ```bash
   # Check outbox depth
   curl http://localhost:5000/api/sync/status | jq '.pending_total'
   
   # View held chunks
   curl 'http://localhost:5000/api/cloud/trace?held=1' | jq '.chunks'
   ```

3. **Test Cloud Sync**
   ```bash
   # Attempt manual sync
   curl -X POST http://localhost:5000/api/sync
   
   # Check sync results
   curl http://localhost:5000/api/sync/status | jq '.last_success'
   ```

### Recovery Steps
1. **Force Cloud Sync**
   ```bash
   # Force immediate cloud sync
   curl -X POST http://localhost:5000/api/cloud/force_sync
   
   # Verify sync completion
   curl http://localhost:5000/api/cloud/status | jq '.ledger.releasable'
   ```

2. **Clear Outbox**
   ```bash
   # If sync continues to fail, clear problematic chunks
   # (requires database access)
   sudo -u postgres psql neural_strata -c "DELETE FROM outbox WHERE released = FALSE;"
   ```

3. **Implement Recovery
   - Restart sync daemon
   - Verify network connectivity to cloud endpoint
   - Check authentication credentials

### Preventive Measures
- Monitor outbox depth regularly
- Set up outbox threshold alerts
- Implement automatic sync retry mechanisms

---

## Scenario 5: Gateway Error Mode

### Situation
Gateway enters ERROR mode due to hardware or software issues.

### Symptoms
- `/api/blackbox/status` shows mode: "ERROR"
- `/api/extended` reports error details
- Critical alert: gateway error mode detected
- System may fall back to SIM mode

### Immediate Actions
1. **Check Error Details**
   ```bash
   # Get error information
   curl http://localhost:5000/api/blackbox/status | jq '.reason'
   
   # Check full error details
   curl http://localhost:5000/api/extended | jq '.blackbox'
   ```

2. **Hardware Diagnostics**
   ```bash
   # Check USB device status
   lsusb -t
   
   # Check device drivers
   dmesg | grep -i hub
   ```

3. **Gateway Logs**
   ```bash
   # Check gateway logs
   tail -f /var/log/blackbox.log
   
   # Check system logs
   journalctl -u neural-strata --since "5 minutes ago"
   ```

### Recovery Steps
1. **Hardware Check**
   - Verify USB hub is powered
   - Check cable connections
   - Test with different USB ports

2. **Software Restart**
   ```bash
   # Restart gateway service
   sudo systemctl restart blackbox-gateway
   
   # Check service status
   sudo systemctl status blackbox-gateway
   ```

3. **Fallback Procedures**
   ```bash
   # Force SIM mode if necessary
   curl -X POST http://localhost:5000/api/sim/trigger -H "Content-Type: application/json" -d '{"node": "node_1", "profile": "machinery_pass"}'
   
   # Verify system stability
   curl http://localhost:5000/api/health | jq '.system_status'
   ```

### Preventive Measures
- Monitor gateway health continuously
- Set up gateway error monitoring
- Maintain hardware redundancy

---

## General Emergency Procedures

### Immediate System Assessment

1. **Check System Health**
   ```bash
   # Overall system health
   curl http://localhost:5000/api/health
   
   # API status
   curl http://localhost:5000/api/telemetry | jq '.system_status'
   ```

2. **Verify API Connectivity**
   ```bash
   # Test all critical endpoints
   endpoints=("/api/telemetry" "/api/ai" "/api/health" "/api/extended")
   for endpoint in "${endpoints[@]}"; do
       echo "Testing $endpoint:"
       curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000$endpoint
   done
   ```

### Escalation Procedures

1. **Level 1 (Operator)**
   - Check logs
   - Restart services
   - Verify configuration

2. **Level 2 (Engineer)**
   - Analyze system state
   - Check database
   - Review alerts

3. **Level 3 (Senior Engineer)**
   - Check hardware
   - Review system architecture
   - Escalate to vendor if needed

### Documentation

- Record all actions taken
- Document recovery time
- Update incident logs
- Create post-incident review

---

## API Contract Violations

### Common Violations and Responses

1. **Missing Required Fields**
   - Response: Return 400 with detailed error message
   - Example: `/api/cloud/status` must include "gateway" field

2. **Invalid Data Types**
   - Response: Return 422 with field-specific validation errors
   - Example: "ttf_seconds" must be integer or "N/A"

3. **Contract Version Mismatch**
   - Response: Return 400 with supported versions
   - Example: "Contract telemetry-v2 not supported, use telemetry-v1"

4. **Missing Authentication**
   - Response: Return 401/403
   - Example: Admin endpoints require proper credentials

### Validation Scripts

```bash
#!/bin/bash
# Validate API contracts
curl -s http://localhost:5000/api/telemetry | jq empty  # Validate JSON

curl -s http://localhost:5000/api/ai | jq 'required(["model", "status"])'
```

---

## Blackbox Gateway Recovery

### Gateway Recovery Commands

1. **Check Gateway Status**
   ```bash
   curl http://localhost:5000/api/blackbox/status
   ```

2. **Force Gateway Restart**
   ```bash
   curl -X POST http://localhost:5000/api/blackbox/recover
   ```

3. **Validate Gateway Health**
   ```bash
   curl http://localhost:5000/api/extended | jq '.blackbox'
   ```

### Gateway Recovery Checklist

- [ ] Verify USB hub connection
- [ ] Check serial port status
- [ ] Validate gateway logs
- [ ] Test endpoint connectivity
- [ ] Monitor system state recovery
- [ ] Verify three-plane architecture sync

---

## Three-Plane Architecture Sync

### Validation Commands

1. **Check Live Plane**
   ```bash
   curl http://localhost:5000/api/extended | jq '.mode'
   ```

2. **Check Durable Plane**
   ```bash
   curl http://localhost:5000/api/cloud/status | jq '.cloud'
   ```

3. **Check Recovery Plane**
   ```bash
   curl http://localhost:5000/api/blackbox/status | jq '.blackbox'
   ```

### Sync Validation

- [ ] Live plane matches hardware source
- [ ] Durable plane has consistent cloud status
- [ ] Recovery plane has valid data
- [ ] All planes report consistent state
- [ ] Delete gate decisions are synchronized

---

## Post-Incident Review

### Checklist

1. **Incident Analysis**
   - Root cause identification
   - Timeline reconstruction
   - Impact assessment

2. **Response Evaluation**
   - Action effectiveness
   - Response time measurement
   - Resource utilization

3. **Improvements**
   - Preventive measures
   - Process updates
   - Training requirements

4. **Documentation Updates**
   - Update runbook
   - Document lessons learned
   - Update standard procedures

### Review Meeting

- **Participants**: Operator, Engineer, Senior Engineer
- **Duration**: 60-90 minutes
- **Output**: Updated procedures, preventive measures, action items

---

## Emergency Contacts

- **System Administrator**: admin@neuralstrata.local
- **Hardware Support**: hardware@neuralstrata.local
- **Cloud Operations**: cloud@neuralstrata.local
- **On-Call Engineer**: Rotate schedule

## Version Information

- **Document Version**: 1.0
- **Last Updated**: $(date +%Y-%m-%d)
- **Neural Strata Version**: Phase 12
- **API Contract Version**: v1

---

*This document is maintained by the Neural Strata operations team and should be updated with each Phase 12 release.*