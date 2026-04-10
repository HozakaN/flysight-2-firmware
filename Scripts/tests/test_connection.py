"""Tests for BLE connection without pairing (device already bonded)."""

import asyncio

import pytest

from ble_client import FlySightBLE
from conftest import parse_status


pytestmark = pytest.mark.asyncio


async def test_connect_bonded_device(cli, ble_address, ensure_sleep):
    """Direct connection to an already-bonded device should succeed."""
    status = parse_status(cli.send_command("status"))
    assert status["mode"] == "SLEEP"

    ble = FlySightBLE()
    try:
        await ble.connect(ble_address)
        ack = await ble.ping()
        assert ack, "Ping failed on bonded connection"
    finally:
        await ble.disconnect()


async def test_multiple_reconnections(cli, ble_address, ensure_sleep):
    """Connect/disconnect 5 times to verify stability."""
    for i in range(5):
        ble = FlySightBLE()
        try:
            await ble.connect(ble_address)
            ack = await ble.ping()
            assert ack, f"Ping failed on iteration {i}"
        finally:
            await ble.disconnect()
        await asyncio.sleep(2)  # Let device re-advertise


async def test_connection_refused_unbonded(cli, ble_address):
    """After BLE reset, unbonded device should refuse connections outside pairing mode."""
    # Reset BLE bonds
    cli.write_file_command("flysight write", "Reset_BLE: 1\n")
    cli.send_command("mode sleep")
    await asyncio.sleep(2)

    ble = FlySightBLE()
    connected = False
    try:
        await ble.connect(ble_address, timeout=10)
        connected = True
    except Exception:
        pass  # Expected

    if connected:
        # Even if connected, ping should fail (no encryption keys)
        ack = await ble.ping(timeout=5)
        await ble.disconnect()
        assert not ack, "Unbonded device should not respond to ping"

    # Cleanup: re-pair for subsequent tests
    cli.send_command("pair start")
    await asyncio.sleep(1)
    ble2 = FlySightBLE()
    try:
        await ble2.connect(ble_address)
        await ble2.pair()
    finally:
        await ble2.disconnect()
    cli.send_command("pair stop")
    cli.send_command("mode sleep")
