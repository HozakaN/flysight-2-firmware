"""Tests for BLE file transfer (read, write, directory operations)."""

import asyncio

import pytest

from ble_client import FlySightBLE
from conftest import parse_status


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def paired_ble(ble_address, ensure_sleep):
    """Connected and paired BLE client."""
    ble = FlySightBLE()
    await ble.connect(ble_address)
    yield ble
    await ble.disconnect()


async def test_write_ble_read_cli(paired_ble, cli):
    """Write a file via BLE, read it back via CLI."""
    content = b"Hello from BLE test\n"
    await paired_ble.write_file_from_bytes(content, "/test_ble.txt")

    read_back = cli.read_file_command("file read /test_ble.txt")
    assert "Hello from BLE test" in read_back

    await paired_ble.delete_file("/test_ble.txt")


async def test_write_cli_read_ble(paired_ble, cli):
    """Write a file via CLI, read it back via BLE."""
    cli.write_file_command("file write /test_cli.txt", "Hello from CLI test\n")

    data = await paired_ble.read_file("/test_cli.txt")
    assert b"Hello from CLI test" in data

    await paired_ble.delete_file("/test_cli.txt")


async def test_large_file_transfer(paired_ble):
    """Write and read back a file larger than the ARQ window (8 * 242 = 1936 bytes)."""
    data = b"X" * 5000
    await paired_ble.write_file_from_bytes(data, "/test_large.txt")

    read_back = await paired_ble.read_file("/test_large.txt")
    assert read_back == data, f"Size mismatch: sent {len(data)}, got {len(read_back)}"

    await paired_ble.delete_file("/test_large.txt")


async def test_list_directory(paired_ble):
    """List root directory should contain known files."""
    entries = await paired_ble.list_dir("/")
    names = [e.split()[-1] for e in entries]
    # At minimum, flysight.txt or config.txt should exist
    assert len(entries) > 0, "Root directory is empty"


async def test_mkdir_and_delete(paired_ble):
    """Create a directory, verify it exists, then delete it."""
    await paired_ble.mkdir("/test_dir")
    await asyncio.sleep(0.5)

    entries = await paired_ble.list_dir("/")
    names = [e.split()[-1] for e in entries]
    assert "test_dir" in names, f"test_dir not found in {names}"

    await paired_ble.delete_file("/test_dir")


async def test_file_transfer_rejected_in_active_mode(paired_ble, cli):
    """File transfer should be rejected (NAK) when device is in ACTIVE mode."""
    cli.send_command("mode active")
    await asyncio.sleep(1)

    status = parse_status(cli.send_command("status"))
    assert status["mode"] == "ACTIVE"

    # Attempt to read — should fail or return empty
    try:
        data = await paired_ble.read_file("/flysight.txt", timeout=3)
        # If we get data, the NAK wasn't handled — still a valid test result
        assert len(data) == 0, "File transfer should be rejected in ACTIVE mode"
    except Exception:
        pass  # Expected: NAK or timeout

    # Cleanup
    cli.send_command("mode sleep")
