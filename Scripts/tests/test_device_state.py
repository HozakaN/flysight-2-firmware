"""Tests for Device State BLE service."""

import asyncio

import pytest

from ble_client import FlySightBLE
from conftest import parse_status


pytestmark = pytest.mark.asyncio

MODE_NAMES = {0: "SLEEP", 1: "ACTIVE", 2: "CONFIG", 3: "USB", 4: "PAIRING", 5: "START"}


@pytest.fixture
async def paired_ble(ble_address, ensure_sleep):
    """Connected and paired BLE client."""
    ble = FlySightBLE()
    await ble.connect(ble_address)
    yield ble
    await ble.disconnect()


async def test_read_mode_matches_cli(paired_ble, cli):
    """BLE device mode should match CLI status."""
    ble_mode = await paired_ble.read_device_mode()
    cli_status = parse_status(cli.send_command("status"))

    ble_mode_name = MODE_NAMES.get(ble_mode, "UNKNOWN")
    assert ble_mode_name == cli_status["mode"], (
        f"BLE mode {ble_mode_name} != CLI mode {cli_status['mode']}"
    )


async def test_mode_change_indication(paired_ble, cli):
    """Mode change indications should reflect CLI mode changes."""
    modes_received = []

    await paired_ble.subscribe_mode(lambda m: modes_received.append(m))

    # Trigger mode change: SLEEP → ACTIVE → SLEEP
    cli.send_command("mode active")
    await asyncio.sleep(2)
    cli.send_command("mode sleep")
    await asyncio.sleep(2)

    await paired_ble.unsubscribe_mode()

    # Should have seen ACTIVE (1) and SLEEP (0)
    assert 1 in modes_received, f"ACTIVE mode not seen in indications: {modes_received}"
    assert 0 in modes_received, f"SLEEP mode not seen in indications: {modes_received}"
