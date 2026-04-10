"""
Pytest configuration and shared fixtures for FlySight BLE tests.

Usage:
    pytest -v --port /dev/tty.usbserial-XXXX --ble-address XX:XX:XX:XX:XX:XX
"""

import sys
import os
import re

import pytest

# Add cli/ to path so we can import FlySightCLI
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cli"))

from flysight_serial import FlySightCLI


def pytest_addoption(parser):
    parser.addoption(
        "--port",
        action="store",
        required=True,
        help="UART serial port for CLI (e.g., /dev/tty.usbserial-XXXX)",
    )
    parser.addoption(
        "--ble-address",
        action="store",
        default=None,
        help="BLE address of FlySight device (e.g., XX:XX:XX:XX:XX:XX). If omitted, will scan.",
    )


@pytest.fixture(scope="session")
def cli(request):
    """FlySightCLI instance connected via UART."""
    port = request.config.getoption("--port")
    client = FlySightCLI(port=port)
    yield client
    client.close()


@pytest.fixture(scope="session")
def ble_address(request):
    """BLE address of the FlySight device."""
    addr = request.config.getoption("--ble-address")
    if addr is None:
        pytest.skip("No --ble-address provided and auto-scan not implemented yet")
    return addr


def parse_status(response: str) -> dict:
    """
    Parse the CLI 'status' command response into a dict.

    Expected format:
        Mode: SLEEP
        BLE: enabled (IDLE)

    Returns:
        {"mode": "SLEEP", "ble_enabled": True, "ble_state": "IDLE"}
    """
    result = {"mode": "UNKNOWN", "ble_enabled": False, "ble_state": "UNKNOWN"}

    for line in response.strip().split("\n"):
        line = line.strip().rstrip("\r")
        if line.startswith("Mode:"):
            result["mode"] = line.split(":", 1)[1].strip()
        elif line.startswith("BLE:"):
            result["ble_enabled"] = "enabled" in line
            match = re.search(r"\((\w+)\)", line)
            if match:
                result["ble_state"] = match.group(1)

    return result


@pytest.fixture
def ensure_sleep(cli):
    """Ensure device is in SLEEP mode before and after test."""
    status = parse_status(cli.send_command("status"))
    if status["mode"] != "SLEEP":
        cli.send_command("mode sleep")
    yield
    status = parse_status(cli.send_command("status"))
    if status["mode"] != "SLEEP":
        cli.send_command("mode sleep")
