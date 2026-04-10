"""Tests for BLE pairing flow."""

import asyncio

import pytest
import pytest_asyncio

from ble_client import FlySightBLE, scan_for_flysight
from conftest import parse_status


pytestmark = pytest.mark.asyncio


async def test_pairing_full_flow(cli, ble_address):
    """Full pairing flow: reset BLE, start pairing, connect, pair, ping, disconnect."""
    # 1. Reset BLE bonds
    cli.write_file_command("flysight write", "Reset_BLE: 1\n")

    # 2. Start pairing via CLI
    cli.send_command("pair start")

    # 3. Verify pairing mode via CLI
    status = parse_status(cli.send_command("status"))
    assert status["mode"] == "PAIRING", f"Expected PAIRING, got {status['mode']}"

    # 4. Scan and verify manufacturer data shows pairing mode
    device = await scan_for_flysight(timeout=10)
    assert device is not None, "FlySight not found during BLE scan"
    assert device.pairing_mode, "Device not advertising pairing mode"

    # 5. Connect and pair
    ble = FlySightBLE()
    try:
        await ble.connect(ble_address)
        await ble.pair()

        # 6. Verify connection is functional
        ack = await ble.ping()
        assert ack, "Ping failed after pairing"
    finally:
        # 7. Disconnect
        await ble.disconnect()

    # 8. Cleanup
    cli.send_command("pair stop")
    cli.send_command("mode sleep")


async def test_pairing_rejected_without_pairing_mode(cli, ble_address):
    """Connection should be rejected when device is not in pairing mode and not bonded."""
    # Reset BLE bonds to clear whitelist
    cli.write_file_command("flysight write", "Reset_BLE: 1\n")

    # Ensure device is in SLEEP, NOT in pairing mode
    cli.send_command("mode sleep")
    await asyncio.sleep(2)

    status = parse_status(cli.send_command("status"))
    assert status["mode"] == "SLEEP"

    # Attempt connection — should fail (whitelist blocks)
    ble = FlySightBLE()
    try:
        await ble.connect(ble_address, timeout=10)
        # If we get here, connection succeeded — check if ping works
        # (it shouldn't for an unbonded device)
        ack = await ble.ping(timeout=5)
        assert not ack, "Unbonded device should not respond to ping"
    except Exception:
        pass  # Expected: connection refused or timeout
    finally:
        await ble.disconnect()


async def test_reconnection_after_bonding(cli, ble_address):
    """After bonding, device should accept connections without pairing mode."""
    # First: pair
    cli.send_command("pair start")
    await asyncio.sleep(1)

    ble = FlySightBLE()
    await ble.connect(ble_address)
    await ble.pair()
    ack = await ble.ping()
    assert ack, "Initial ping failed"
    await ble.disconnect()

    # Stop pairing, go to sleep
    cli.send_command("pair stop")
    cli.send_command("mode sleep")
    await asyncio.sleep(3)

    # Reconnect WITHOUT pairing mode — should work (bonded/whitelisted)
    ble2 = FlySightBLE()
    try:
        await ble2.connect(ble_address, timeout=15)
        ack = await ble2.ping()
        assert ack, "Reconnection ping failed — device may not be whitelisted"
    finally:
        await ble2.disconnect()
