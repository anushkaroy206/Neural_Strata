# Level Hub 2 Firmware (SIMPLIFIED)

## Overview
Simplified firmware for Level Hub 2 focusing on essential functionality and reliability. This firmware implements basic command handling, strip rendering, and ring communication with multiple redundant protection layers against continuous buzzer activation.

## Key Features

### Essential Protection Layers
1. **Hardware GPIO Protection**: Siren pin forced HIGH immediately during boot
2. **Software Boot Block**: Multiple checks prevent siren activation during startup
3. **Strip Rendering Guard**: Stays silent until first server command
4. **Final Hardware Override**: Continuous high-level siren suppression

### Simplified Command Handling
- LED commands for junction control
- Pattern commands for both junctions
- Siren commands with Level validation
- Advisory commands for notification handling

### Basic Ring Communication
- Uplink management via direct or cross-link paths
- Fault detection and reporting
- Heartbeat protocol for link monitoring
- Segment restoration handling

## Files

### Core Files
- `main.cpp` - Main firmware entry point with simplified command handling
- `strips.h` - Simplified interface for strip operations
- `strips.cpp` - Simplified strip rendering with protection logic
- `ring_port.cpp` - Ring communication implementation

### Hardware Configuration
- `pins.h` - GPIO pin definitions
- `config.h` - Configuration parameters

## Build & Flash Instructions

### Compilation
```bash
# Level 1 drives J1 and J2. Level 2 drives J3 and J4.
pio run -e level_hub_l1
pio run -e level_hub_l2
```

### Flashing
```bash
pio run -e level_hub_l1 -t upload
pio run -e level_hub_l2 -t upload

# Verify after flash
pio device monitor -b 115200
```

## Expected Behavior After Flash

### Boot Sequence
1. **Immediate Protection**: Siren forced HIGH (silent)
2. **Ready State**: "Ready - waiting for commands"
3. **Strips Offline**: No rendering until first command

### After First Command
1. **Siren Unlocked**: Siren now responds to commands
2. **Strips Active**: Pattern rendering begins
3. **Ring Connected**: Level Hub 2 to Level Hub 1 link established

### Console Output Example
```
[level 2 hub] Basic Level Hub Started
[level 2 hub] ring up · A=UART2(17/16) B=UART1(4/13) via=lvl2
[level 2 hub] Ready - waiting for commands
[level 2 hub] Commands: 1
[level 2 hub] ring A up (rx 1 tx 1) · B up (rx 0 tx 0) · fwd 0 ignored 0 overflow 0 reroutes 0 · faults 0/0
```

### After First Command
```
[level 2 hub] First command received
[level 2 hub] Strips now rendering
[level 2 hub] Pattern flow_green applied to both junctions
[level 2 hub] ring A up (rx 1 tx 2) · B up (rx 0 tx 0) · fwd 0 ignored 0 overflow 0 reroutes 0 · faults 0/0
```

## Troubleshooting

### Buzzer Still Active After Flash
1. **Verify Pin Connections**: GPIO 4/13 should be properly wired
2. **Check Serial Output**: Look for boot messages
3. **Power Cycle**: Remove power for 5s, reconnect
4. **Console Monitor**: Use `pio device monitor -b 115200`

### No Ring Communication
1. **Check Wiring**: Ensure Level Hub 2 to Level Hub 1 connections
2. **Verify Build**: Ensure `-DLEVEL=2` flag is used
3. **Console Output**: Look for "ring up" message

### Strips Not Rendering
1. **Wait for Command**: Strips stay dark until first server command
2. **Verify Server**: Ensure Level Hub 2 is receiving commands
3. **Check Patterns**: Apply a pattern via server command

## Testing

### Manual Testing Steps
1. Flash firmware to Level Hub 2
2. Power on the system
3. Monitor console output
4. Send first command via server/API
5. Verify strips render
6. Test siren commands
7. Check ring communication status

### Debug Script
Run the debug script to analyze current state:
```bash
python debug_buzzer_test.py
```

## Protection Mechanism Analysis

The firmware implements multiple redundant protection layers:

1. **Hardware**: `digitalWrite(PIN_SIREN, HIGH)` in setup()
2. **Software**: `ns_siren_simple_set()` checks `s_boot_silent`
3. **Software**: `strips_apply()` checks `s_have_command`
4. **Hardware**: `main.cpp` forces HIGH in loop()

If buzzer sounds despite these protections, the issue is hardware-level and requires investigation of:
- GPIO pin connections
- Power supply stability
- Physical switch/wiring issues
- External interference

## License
This firmware is part of Neural Strata project.
Distributed under project license.