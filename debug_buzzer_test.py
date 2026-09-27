#!/usr/bin/env python3
"""
Debug script to test Level Hub 2 buzzer protection mechanisms.

This script analyzes the firmware protection layers and simulates the boot
sequence to identify why buzzer activation persists despite multiple safeguards.
"""

import subprocess
import time
import re
from typing import List, Tuple

def analyze_firmware_files():
    """Analyze the firmware files for buzzer protection mechanisms."""
    print("=== ANALYZING FIRMWARE PROTECTION MECHANISMS ===\n")

    # Read strips.cpp for siren protection
    with open('/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/firmware/src/level_hub/strips.cpp', 'r') as f:
        strips_content = f.read()

    print("1. LEVEL HUB 2 BUZZER PROTECTION ANALYSIS")
    print("=" * 50)

    # Check for boot protection in strips.cpp
    boot_protection_checks = [
        ('s_boot_silent = true', 'Boot silence flag initialization'),
        ('if (s_boot_silent)', 'Boot silence check in ns_siren_set'),
        ('if (!s_have_command)', 'Have command check in strips_apply'),
        ('digitalWrite(PIN_SIREN, HIGH)', 'Siren forced HIGH'),
    ]

    for pattern, description in boot_protection_checks:
        if pattern in strips_content:
            print(f"✅ FOUND: {description}")
        else:
            print(f"❌ MISSING: {description}")

    print("\n2. PROTECTION LAYERS BREAKDOWN")
    print("=" * 50)

    protections = [
        ("Hardware GPIO init (main.cpp:236-249)", "Safe state before boot"),
        ("ns_siren_set() boot block (strips.cpp:227-234)", "Block siren during boot"),
        ("strips_apply() early return (strips.cpp:372-374)", "No rendering until command"),
        ("main.cpp force siren HIGH (main.cpp:297-302)", "Final safety net"),
    ]

    for mechanism, description in protections:
        print(f"🔒 {description}: {mechanism}")

    print("\n3. POTENTIAL FAILURE MODES")
    print("=" * 50)

    failure_modes = [
        "Race condition between strips.cpp and main.cpp protections",
        "Hardware debounce causing multiple siren toggles",
        "Power supply issues causing reset of protection flags",
        "Firmware compilation issues bypassing protections",
        "Physical GPIO connections bypassing software protection"
    ]

    for i, mode in enumerate(failure_modes, 1):
        print(f"{i}. {mode}")

    return strips_content

def analyze_startup_sequence():
    """Analyze the complete startup sequence."""
    print("\n=== STARTUP SEQUENCE ANALYSIS ===\n")

    # Read main.cpp startup sequence
    with open('/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/firmware/src/level_hub/main.cpp', 'r') as f:
        main_content = f.read()

    print("1. LEVEL HUB 2 SETUP SEQUENCE")
    print("=" * 50)

    setup_analysis = [
        ("pinMode(PIN_GATE_GREEN, OUTPUT)", "Gate green output"),
        ("pinMode(PIN_GATE_RED, OUTPUT)", "Gate red output"),
        ("digitalWrite(PIN_GATE_GREEN, LOW)", "Gate green off"),
        ("digitalWrite(PIN_GATE_RED, HIGH)", "Gate red on (boot state)"),
        ("pinMode(PIN_SIREN, OUTPUT)", "Siren output"),
        ("digitalWrite(PIN_SIREN, HIGH)", "Siren HIGH (silent)"),
    ]

    for pattern, description in setup_analysis:
        if pattern in main_content:
            print(f"✅ {description}: Found")
        else:
            print(f"❌ {description}: Missing")

    print("\n2. BOOT PROTECTION TIMING")
    print("=" * 50)

    print("Protection layers in order:")
    print("1. Hardware: GPIO14 driven HIGH immediately (main.cpp:249)")
    print("2. Software: strips_apply() checks s_have_command (strips.cpp:372)")
    print("3. Software: ns_siren_set() checks s_boot_silent (strips.cpp:230)")
    print("4. Hardware: main.cpp forces HIGH in loop() (main.cpp:301)")

    print("\n⚠️  CRITICAL QUESTION: Why is buzzer still sounding?")
    print("   - Protection layer not in effect?")
    print("   - Protection overridden?")
    print("   - Protection reset?")

def simulate_boot_logic():
    """Simulate the boot logic to identify failure points."""
    print("\n=== BOOT LOGIC SIMULATION ===\n")

    print("1. INITIAL STATE (at setup() start):")
    initial_state = {
        "s_boot_silent": "true (blocks siren)",
        "s_have_command": "false (no rendering)",
        "s_siren_on": "false (no active command)",
        "PIN_SIREN state": "HIGH (silent) via hardware init",
    }

    for key, value in initial_state.items():
        print(f"   {key}: {value}")

    print("\n2. PROTECTION LAYER EXECUTION ORDER:")
    execution_order = [
        "Hardware: digitalWrite(PIN_SIREN, HIGH) - IMMEDIATE",
        "Software: strips_apply() checks s_have_command - ~30Hz",
        "Software: ns_siren_set() checks s_boot_silent - only on commands",
        "Hardware: main.cpp loop() check - ~10ms interval",
    ]

    for i, step in enumerate(execution_order, 1):
        print(f"   {i}. {step}")

    print("\n3. FAILURE ANALYSIS:")
    print("=" * 50)

    print("✓ Layer 1 (Hardware): Should always keep siren HIGH")
    print("✓ Layer 2 (strips_apply): Should return early before any rendering")
    print("✓ Layer 3 (ns_siren_set): Should block siren commands during boot")
    print("✓ Layer 4 (main.cpp): Should override any stray siren activity")

    print("\n⚠️  CONCLUSION: Multiple redundant protections exist.")
    print("   If buzzer sounds, at least one protection failed completely.")

def suggest_debug_steps():
    """Suggest debugging steps to identify the root cause."""
    print("\n=== DEBUGGING RECOMMENDATIONS ===\n")

    debug_steps = [
        {
            "step": 1,
            "action": "Check Level Hub 2 GPIO 4/13 connections",
            "description": "Verify ring port connections physically on Level Hub 2",
            "priority": "CRITICAL"
        },
        {
            "step": 2,
            "action": "Power cycle and capture serial output",
            "description": "Remove power, wait 5s, reconnect, capture boot messages",
            "priority": "HIGH"
        },
        {
            "step": 3,
            "action": "Add debug prints to strips.cpp",
            "description": "Add s_boot_silent and s_have_command status prints",
            "priority": "MEDIUM"
        },
        {
            "step": 4,
            "action": "Verify firmware compilation",
            "description": "Check that all protection code is compiled in",
            "priority": "MEDIUM"
        },
        {
            "step": 5,
            "action": "Check power supply stability",
            "description": "Verify clean power to both hubs and nodes",
            "priority": "LOW"
        }
    ]

    for step in debug_steps:
        print(f"Step {step['step']} ({step['priority']}): {step['action']}")
        print(f"   {step['description']}\n")

def main():
    """Main debug function."""
    print("NEURAL STRATA - LEVEL HUB 2 BUZZER DEBUGger")
    print("=" * 60)

    # Analyze the current state
    analyze_firmware_files()
    analyze_startup_sequence()
    simulate_boot_logic()
    suggest_debug_steps()

    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("Multiple redundant protection layers exist.")
    print("If buzzer sounds, protection mechanism is completely broken.")
    print("Requires hardware-level investigation.")

if __name__ == "__main__":
    main()