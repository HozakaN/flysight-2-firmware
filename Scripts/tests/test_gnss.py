"""Tests for GNSS data streaming over BLE."""

import asyncio

import pytest

from ble_client import FlySightBLE
from conftest import parse_status


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def paired_ble_active(ble_address, cli):
    """Connected BLE client with device in ACTIVE mode."""
    cli.send_command("mode active")
    await asyncio.sleep(2)

    ble = FlySightBLE()
    await ble.connect(ble_address)
    yield ble

    await ble.disconnect()
    cli.send_command("mode sleep")


async def test_gnss_default_mask(paired_ble_active):
    """GNSS data should stream with default mask 0xB0 (iTOW + Position + Velocity)."""
    packets = []

    await paired_ble_active.subscribe_gnss(lambda data: packets.append(data))
    await asyncio.sleep(10)  # Wait for GNSS fixes
    await paired_ble_active.unsubscribe_gnss()

    assert len(packets) > 0, "No GNSS packets received"

    for pkt in packets:
        mask = pkt[0]
        # Default mask should have iTOW (0x80), Position (0x20), Velocity (0x10)
        assert mask & 0xB0 != 0, f"Unexpected mask: 0x{mask:02X}"


async def test_gnss_custom_mask(paired_ble_active):
    """Setting GNSS mask to iTOW + numSV (0x84) should produce 6-byte packets."""
    await paired_ble_active.set_gnss_mask(0x84)
    await asyncio.sleep(0.5)

    mask = await paired_ble_active.get_gnss_mask()
    assert mask == 0x84, f"Mask not set correctly: 0x{mask:02X}"

    packets = []
    await paired_ble_active.subscribe_gnss(lambda data: packets.append(data))
    await asyncio.sleep(10)
    await paired_ble_active.unsubscribe_gnss()

    assert len(packets) > 0, "No GNSS packets received with custom mask"

    for pkt in packets:
        # mask(1) + iTOW(4) + numSV(1) = 6 bytes
        assert len(pkt) == 6, f"Expected 6 bytes, got {len(pkt)}"

    # Restore default mask
    await paired_ble_active.set_gnss_mask(0xB0)


async def test_no_gnss_in_sleep(ble_address, cli, ensure_sleep):
    """No GNSS data should stream when device is in SLEEP mode."""
    ble = FlySightBLE()
    await ble.connect(ble_address)

    packets = []
    await ble.subscribe_gnss(lambda data: packets.append(data))
    await asyncio.sleep(5)
    await ble.unsubscribe_gnss()
    await ble.disconnect()

    assert len(packets) == 0, f"Received {len(packets)} GNSS packets in SLEEP mode"
