"""Tests for BLE ping / keep-alive mechanism."""

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


async def test_ping_ack(paired_ble):
    """Basic ping should return ACK."""
    ack = await paired_ble.ping()
    assert ack, "Ping ACK not received"


async def test_ping_keeps_alive(paired_ble):
    """Ping at 25s should prevent the 30s timeout disconnect."""
    await asyncio.sleep(25)
    ack = await paired_ble.ping()
    assert ack, "Ping failed near timeout boundary"
    assert paired_ble.is_connected, "Connection dropped despite ping"


async def test_timeout_without_ping(paired_ble):
    """Without ping, connection should drop after ~30s timeout."""
    await asyncio.sleep(35)
    # Connection should have been terminated by the device
    assert not paired_ble.is_connected, "Connection should have timed out"
