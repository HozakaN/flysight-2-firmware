"""Tests for Battery Level BLE service."""

import asyncio

import pytest

from ble_client import FlySightBLE


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def paired_ble(ble_address, ensure_sleep):
    """Connected and paired BLE client."""
    ble = FlySightBLE()
    await ble.connect(ble_address)
    yield ble
    await ble.disconnect()


async def test_battery_level_read(paired_ble):
    """Battery level should be between 0 and 100."""
    level = await paired_ble.read_battery()
    assert 0 <= level <= 100, f"Battery level out of range: {level}"


async def test_battery_notifications(paired_ble):
    """Battery notifications should report valid levels."""
    levels = []

    await paired_ble.subscribe_battery(lambda l: levels.append(l))
    await asyncio.sleep(5)
    await paired_ble.unsubscribe_battery()

    # Battery notifications may not fire if level hasn't changed
    # so we only check validity if we got any
    for level in levels:
        assert 0 <= level <= 100, f"Battery level out of range: {level}"
