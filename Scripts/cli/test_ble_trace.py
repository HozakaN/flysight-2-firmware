"""
Tests for ble_trace.py that need no hardware.

Usage:
    python -m unittest test_ble_trace
"""

import os
import shutil
import struct
import tempfile
import unittest

import ble_trace
from ble_trace import (APP_BLE, APP_CONF, MARKER, RECORD_SIZE, RECORDS, REPO, Target, TraceError,
                       decode, elf_symbols, patch_sources, timeline)


def record(kind, a=0, b=0, c=0, data=b""):
    return struct.pack("<IBBBB", 1234, kind, a, b, c) + bytes(data).ljust(24, b"\0")


class PatchTests(unittest.TestCase):
    """Run against a copy of the real sources, so an anchor broken by a firmware change is caught."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        for path in (APP_BLE, APP_CONF):
            os.makedirs(os.path.dirname(os.path.join(self.root, path)), exist_ok=True)
            shutil.copy(os.path.join(REPO, path), os.path.join(self.root, path))

    def content(self, path):
        with open(os.path.join(self.root, path), "rb") as f:
            return f.read()

    def test_instrument_then_restore_gives_back_the_same_bytes(self):
        before = {path: self.content(path) for path in (APP_BLE, APP_CONF)}
        self.assertEqual(patch_sources(self.root), sorted([APP_BLE, APP_CONF]))
        for path in (APP_BLE, APP_CONF):
            self.assertIn(MARKER.encode(), self.content(path))
        patch_sources(self.root, reverse=True)
        self.assertEqual({path: self.content(path) for path in (APP_BLE, APP_CONF)}, before)

    def test_trace_blocks_are_closed_and_both_switches_exist(self):
        patch_sources(self.root)
        text = self.content(APP_BLE).decode()
        self.assertEqual(text.count(MARKER + " begin"), text.count(MARKER + " end"))
        for name in ble_trace.SWITCHES.values():
            self.assertIn(name, text)

    def test_the_committed_sources_are_not_instrumented(self):
        for path in (APP_BLE, APP_CONF):
            self.assertNotIn(MARKER.encode(), self.content(path))

    def test_instrumenting_twice_is_refused(self):
        patch_sources(self.root)
        with self.assertRaises(TraceError):
            patch_sources(self.root)

    def test_restoring_plain_sources_is_refused(self):
        with self.assertRaises(TraceError):
            patch_sources(self.root, reverse=True)

    def test_a_missing_anchor_is_reported_and_nothing_is_written(self):
        path = os.path.join(self.root, APP_BLE)
        with open(path, newline="") as f:
            text = f.read()
        with open(path, "w", newline="") as f:
            f.write(text.replace("  request_pairing = 0;\n\n  FS_Adv_Request(APP_BLE_LP_ADV);", "  /* gone */"))
        broken = {p: self.content(p) for p in (APP_BLE, APP_CONF)}
        with self.assertRaises(TraceError) as ctx:
            patch_sources(self.root)
        self.assertIn("found 0", str(ctx.exception))
        self.assertEqual({p: self.content(p) for p in (APP_BLE, APP_CONF)}, broken)


class DecodeTests(unittest.TestCase):
    def test_advertising_start_in_pairing_mode(self):
        data = bytearray(24)
        data[0:6] = bytes.fromhex("0504030201C0")       # static address, little endian
        data[6:12] = bytes.fromhex("720000000054")      # controller RPA for the first bonded peer
        data[12], data[13], data[15] = 0x00, 0x00, 0x00
        line = decode(12, record(1, a=1, b=2, c=1, data=data))
        self.assertIn("PAIRING mode", line)
        self.assertIn("bonded=2", line)
        self.assertIn("static addr C0:01:02:03:04:05", line)
        self.assertIn("54:00:00:00:00:72", line)
        self.assertIn("peer0 privacy: device privacy", line)

    def test_advertising_start_without_the_privacy_fix(self):
        data = bytearray(24)
        data[12], data[15] = 0x00, 0xEE
        self.assertIn("peer0 privacy: not set", decode(0, record(1, a=0, b=1, data=data)))

    def test_advertising_start_with_no_bond(self):
        data = bytearray(24)
        data[12], data[15] = 0xEE, 0xEE
        line = decode(0, record(1, a=0, b=0, data=data))
        self.assertIn("idle", line)
        self.assertIn("controller RPA for peer0 - | peer0 privacy: -", line)

    def test_connection_from_an_identity_address(self):
        data = bytearray(24)
        data[4] = 0                                      # public address
        data[5:11] = bytes.fromhex("554433221100")
        line = decode(2, record(2, data=data))
        self.assertIn("peer=00:11:22:33:44:55 (public)", line)
        self.assertIn("peerRPA=none", line)

    def test_connection_from_a_resolved_private_address(self):
        data = bytearray(24)
        data[4] = 2
        data[5:11] = bytes.fromhex("CCBBAA221100")
        data[11:17] = bytes.fromhex("C40000000066")
        data[17:23] = bytes.fromhex("530000000045")
        line = decode(29, record(2, data=data))
        self.assertIn("peer=00:11:22:AA:BB:CC (public identity (resolved))", line)
        self.assertIn("peerRPA=45:00:00:00:00:53", line)
        self.assertIn("66:00:00:00:00:C4 (controller RPA)", line)

    def test_disconnection_encryption_and_gap_events(self):
        self.assertIn("reason=0x3d (MIC failure)", decode(1, record(3, a=0x3D)))
        self.assertIn("status=0x00 enabled=1", decode(1, record(5, a=0, b=1)))
        self.assertIn("LIMITED_DISCOVERABLE_TIMEOUT", decode(1, record(6, a=0x00, b=0x04)))
        self.assertIn("PAIRING_COMPLETE status=0 reason=0x00", decode(1, record(6, a=0x01, b=0x04)))

    def test_routine_link_events_are_not_shown(self):
        for subevent in (0x03, 0x07, 0x0C):
            self.assertIsNone(decode(1, record(7, a=subevent)))
        self.assertIn("subevent=0x0a", decode(1, record(7, a=0x0A)))


class TimelineTests(unittest.TestCase):
    def blob(self, count):
        ring = [bytes(RECORD_SIZE)] * RECORDS
        for i in range(max(0, count - RECORDS), count):
            ring[i % RECORDS] = record(5, a=i & 0xFF, b=1)
        return b"".join(ring)

    def test_since_selects_the_records_after_a_mark(self):
        lines = timeline(10, self.blob(10), since=7)
        self.assertEqual([line.split()[0] for line in lines], ["7", "8", "9"])

    def test_only_the_last_ring_of_records_is_available(self):
        lines = timeline(200, self.blob(200), since=0)
        self.assertEqual(len(lines), RECORDS)
        self.assertEqual(lines[0].split()[0], "136")
        self.assertIn("status=0x88", lines[0])
        self.assertEqual(lines[-1].split()[0], "199")


def tiny_elf(symbols):
    """A minimal ELF32 holding only a symbol table."""
    strtab = b"\0"
    entries = [struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)]
    for name, value in symbols.items():
        entries.append(struct.pack("<IIIBBH", len(strtab), value, 4, 0x11, 0, 1))
        strtab += name.encode() + b"\0"
    symtab = b"".join(entries)
    symtab_off, strtab_off = 52, 52 + len(symtab)
    shoff = strtab_off + len(strtab)
    header = (b"\x7fELF\x01\x01\x01" + bytes(9)
              + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 3, 0))
    sections = (bytes(40)
                + struct.pack("<IIIIIIIIII", 0, 2, 0, 0, symtab_off, len(symtab), 2, 0, 4, 16)
                + struct.pack("<IIIIIIIIII", 0, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1, 0))
    return header + symtab + strtab + sections


class ElfTests(unittest.TestCase):
    def write(self, data):
        f = tempfile.NamedTemporaryFile(suffix=".elf", delete=False)
        self.addCleanup(os.unlink, f.name)
        f.write(data)
        f.close()
        return f.name

    def test_only_the_trace_symbols_are_returned(self):
        path = self.write(tiny_elf({"ble_trace_n": 0x20001234, "main": 0x08000100, "ble_trace_log": 0x20002000}))
        self.assertEqual(elf_symbols(path), {"ble_trace_n": 0x20001234, "ble_trace_log": 0x20002000})

    def test_a_build_without_the_trace_is_refused(self):
        with self.assertRaises(TraceError) as ctx:
            elf_symbols(self.write(tiny_elf({"main": 0x08000100})))
        self.assertIn("instrumented", str(ctx.exception))

    def test_a_file_that_is_not_an_elf_is_refused(self):
        with self.assertRaises(TraceError):
            elf_symbols(self.write(b"not an elf file at all" * 4))


class TargetTests(unittest.TestCase):
    def test_a_block_read_is_reassembled_from_the_programmer_output(self):
        out = ("Reading 8-bit memory content\n  Size          : 20 Bytes\n\n"
               "0x20001000 : 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F\n"
               "0x20001010 : 10 11 12 13\n")
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return type("Proc", (), {"stdout": out, "stderr": ""})()

        target = Target("STM32_Programmer_CLI", runner=runner)
        self.assertEqual(target.read_bytes(0x20001000, 20), bytes(range(20)))
        self.assertEqual(calls[0][-3:], ["-r8", "0x20001000", "20"])

    def test_a_short_read_is_an_error(self):
        def runner(args, **kwargs):
            return type("Proc", (), {"stdout": "Error: No STM32 target found!\n", "stderr": ""})()

        with self.assertRaises(ble_trace.SwdError):
            Target("STM32_Programmer_CLI", runner=runner).read_bytes(0x20001000, 4)


if __name__ == "__main__":
    unittest.main()
