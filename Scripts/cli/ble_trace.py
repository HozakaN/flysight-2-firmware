#!/usr/bin/env python3
"""
Record what the BLE stack reports to the firmware, and read it back over SWD.

The BLE controller can refuse a connection without telling the application
(see "Reconnecting with an identity address" in Docs/ble.md). To see that from
the FlySight side, this tool adds a temporary trace to app_ble.c:

  - a ring of the last 64 events in RAM: every advertising start (pairing mode
    or not, addresses in use), connection, disconnection, encryption change and
    GAP event;
  - two RAM switches that turn a fix off and on again without reflashing, so
    one boot and one bond can be compared with and without it:
      privacy  Device Privacy mode for bonded peers (ble_count_bonded_devices)
      window   request_pairing cleared in Adv_Update

The trace is test code. `instrument` edits the sources in place, `restore`
removes it again; it is never meant to be committed. It also sets
CFG_DEBUGGER_SUPPORTED to 1, which SWD access to a running firmware needs.

Usage:
    python ble_trace.py instrument          # then build and flash as usual
    python ble_trace.py read --elf build.elf
    python ble_trace.py mark --elf build.elf
    python ble_trace.py read --elf build.elf --since 12
    python ble_trace.py set privacy off --elf build.elf
    python ble_trace.py restore

The ELF must be the one that is running: the addresses of the trace come from
its symbol table. FLYSIGHT_ELF can be set instead of passing --elf.
"""

import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vbus import Swd, SwdError, connect  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_BLE = "STM32_WPAN/App/app_ble.c"
APP_CONF = "Core/Inc/app_conf.h"
MARKER = "TEMP-BLE-TRACE"

RECORDS = 64
RECORD_SIZE = 32
SWITCHES = {"privacy": "ble_trace_fix_privacy", "window": "ble_trace_fix_window"}

# ---------------------------------------------------------------------------
# Instrumentation: (file, text to find, text to put instead). Each text to find
# must occur exactly once, so a change in the firmware that breaks an anchor is
# reported instead of producing a half-instrumented build.
# ---------------------------------------------------------------------------

PATCHES = [
    (APP_CONF,
     "#define CFG_DEBUGGER_SUPPORTED    0\n",
     "#define CFG_DEBUGGER_SUPPORTED    1 /* TEMP-BLE-TRACE */\n"),

    (APP_BLE,
     "static BleApplicationContext_t BleApplicationContext;\n",
     """static BleApplicationContext_t BleApplicationContext;

/* TEMP-BLE-TRACE begin ------------------------------------------------------- */
typedef struct { uint32_t tick; uint8_t kind, a, b, c; uint8_t data[24]; } ble_trace_rec_t;
volatile ble_trace_rec_t ble_trace_log[64];
volatile uint32_t ble_trace_n;
volatile uint32_t ble_trace_gatt_n;
volatile uint8_t ble_trace_fix_privacy = 1;  /* written over SWD: 0 = no Device Privacy mode */
volatile uint8_t ble_trace_fix_window = 1;   /* written over SWD: 0 = request_pairing kept in Adv_Update */
volatile uint8_t ble_trace_bonded, ble_trace_rl0_type, ble_trace_rl0_addr[6], ble_trace_pm_status;
static void ble_trace(uint8_t kind, uint8_t a, uint8_t b, uint8_t c, const uint8_t *data, uint8_t len)
{
  volatile ble_trace_rec_t *r = &ble_trace_log[ble_trace_n & 63];
  r->tick = HAL_GetTick(); r->kind = kind; r->a = a; r->b = b; r->c = c;
  for (uint8_t i = 0; i < 24; i++) r->data[i] = (data && i < len) ? data[i] : 0;
  ble_trace_n++;
}
/* TEMP-BLE-TRACE end --------------------------------------------------------- */
"""),

    (APP_BLE,
     "  p_event_pckt = (hci_event_pckt*) ((hci_uart_pckt *) p_Pckt)->data;\n\n  switch (p_event_pckt->evt)",
     """  p_event_pckt = (hci_event_pckt*) ((hci_uart_pckt *) p_Pckt)->data;

  /* TEMP-BLE-TRACE begin */
  if (p_event_pckt->evt == HCI_DISCONNECTION_COMPLETE_EVT_CODE)
  {
    hci_disconnection_complete_event_rp0 *d = (hci_disconnection_complete_event_rp0 *) p_event_pckt->data;
    ble_trace(3, d->Reason, d->Status, 0, (uint8_t *) &d->Connection_Handle, 2);
  }
  else if (p_event_pckt->evt == HCI_ENCRYPTION_CHANGE_EVT_CODE)
  {
    hci_encryption_change_event_rp0 *e = (hci_encryption_change_event_rp0 *) p_event_pckt->data;
    ble_trace(5, e->Status, e->Encryption_Enabled, 0, 0, 0);
  }
  else if (p_event_pckt->evt == HCI_LE_META_EVT_CODE)
  {
    evt_le_meta_event *m = (evt_le_meta_event *) p_event_pckt->data;
    if (m->subevent == HCI_LE_ENHANCED_CONNECTION_COMPLETE_SUBEVT_CODE) ble_trace(2, 0, 0, 0, m->data, 23);
    else if (m->subevent != HCI_LE_ADVERTISING_REPORT_SUBEVT_CODE) ble_trace(7, m->subevent, 0, 0, m->data, 8);
  }
  else if (p_event_pckt->evt == HCI_VENDOR_SPECIFIC_DEBUG_EVT_CODE)
  {
    evt_blecore_aci *v = (evt_blecore_aci *) p_event_pckt->data;
    if ((v->ecode & 0xFF00) == 0x0C00) ble_trace_gatt_n++;
    else if (v->ecode != 0x0004) ble_trace(6, v->ecode & 0xFF, v->ecode >> 8, 0, v->data, 8);
  }
  else
  {
    ble_trace(8, p_event_pckt->evt, 0, 0, p_event_pckt->data, 8);
  }
  /* TEMP-BLE-TRACE end */

  switch (p_event_pckt->evt)"""),

    (APP_BLE,
     "  // 4) Let bonded peers connect with their identity address too.",
     """  /* TEMP-BLE-TRACE begin */
  ble_trace_bonded = total;
  if (total) { ble_trace_rl0_type = rl_entries[0].Peer_Identity_Address_Type; memcpy((void *) ble_trace_rl0_addr, rl_entries[0].Peer_Identity_Address, 6); }
  ble_trace_pm_status = 0xEE;
  /* TEMP-BLE-TRACE end */

  // 4) Let bonded peers connect with their identity address too."""),

    (APP_BLE,
     "  for (uint8_t k = 0; k < total; k++)\n  {\n    ret = hci_le_set_privacy_mode(",
     "  for (uint8_t k = 0; ble_trace_fix_privacy && k < total; k++) /* TEMP-BLE-TRACE switch */\n"
     "  {\n    ret = hci_le_set_privacy_mode("),

    (APP_BLE,
     "                                  BLE_PRIVACY_MODE_DEVICE);\n",
     "                                  BLE_PRIVACY_MODE_DEVICE);\n"
     "    if (k == 0) ble_trace_pm_status = ret; /* TEMP-BLE-TRACE */\n"),

    (APP_BLE,
     """  if (NewStatus == APP_BLE_FAST_ADV)
  {
    HW_TS_Start(Advertising_mgr_timer_Id, FAST_ADV_TIMEOUT);
  }

  return;
}""",
     """  if (NewStatus == APP_BLE_FAST_ADV)
  {
    HW_TS_Start(Advertising_mgr_timer_Id, FAST_ADV_TIMEOUT);
  }

  /* TEMP-BLE-TRACE begin: which addresses are in use for this advertising run */
  {
    uint8_t d[24] = {0}, len = 0;
    aci_hal_read_config_data(CONFIG_DATA_RANDOM_ADDRESS_OFFSET, &len, &d[0]);
    d[12] = 0xEE;
    if (ble_trace_bonded) d[12] = hci_le_read_local_resolvable_address(ble_trace_rl0_type, (const uint8_t *) ble_trace_rl0_addr, &d[6]);
    d[13] = ret; d[14] = ble_trace_fix_privacy; d[15] = ble_trace_pm_status;
    d[16] = ble_trace_rl0_type; memcpy(&d[17], (void *) ble_trace_rl0_addr, 6);
    ble_trace(1, request_pairing, ble_trace_bonded, (uint8_t) NewStatus, d, 24);
  }
  /* TEMP-BLE-TRACE end */

  return;
}"""),

    (APP_BLE,
     "  request_pairing = 0;\n\n  FS_Adv_Request(APP_BLE_LP_ADV);\n}",
     "  if (ble_trace_fix_window) request_pairing = 0; /* TEMP-BLE-TRACE switch */\n\n"
     "  FS_Adv_Request(APP_BLE_LP_ADV);\n}"),
]


class TraceError(RuntimeError):
    pass


def patch_sources(root, reverse=False):
    """Apply (or remove) the trace in the sources under root. Returns the files changed."""
    contents = {}
    for path, _, _ in PATCHES:
        if path not in contents:
            with open(os.path.join(root, path), newline="") as f:
                contents[path] = f.read()

    present = any(MARKER in text for text in contents.values())
    if reverse and not present:
        raise TraceError("the sources are not instrumented, nothing to restore")
    if not reverse and present:
        raise TraceError("the sources are already instrumented (run `restore` first)")

    for path, plain, traced in PATCHES:
        if "\r\n" in contents[path]:
            # A checkout made on Windows (core.autocrlf): the trace takes the line ends of the file
            plain, traced = plain.replace("\n", "\r\n"), traced.replace("\n", "\r\n")
        old, new = (traced, plain) if reverse else (plain, traced)
        found = contents[path].count(old)
        if found != 1:
            raise TraceError(f"{path}: expected exactly one occurrence of this text, found {found}:\n"
                             f"{old[:200]}\n(the firmware changed here; update PATCHES in ble_trace.py)")
        contents[path] = contents[path].replace(old, new)

    for path, text in contents.items():
        with open(os.path.join(root, path), "w", newline="") as f:
            f.write(text)
    return sorted(contents)


# ---------------------------------------------------------------------------
# ELF symbols (ELF32, little endian: what the Cortex-M4 toolchain produces)
# ---------------------------------------------------------------------------

def elf_symbols(path, prefix="ble_trace_"):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise TraceError(f"{path}: not a 32-bit little-endian ELF file")
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]

    symbols = {}
    for _, sh_type, _, _, offset, size, link, _, _, entsize in sections:
        if sh_type != 2 or not entsize:      # SHT_SYMTAB
            continue
        strtab = sections[link][4]
        for pos in range(offset, offset + size, entsize):
            name_off, value = struct.unpack_from("<II", data, pos)
            end = data.index(b"\0", strtab + name_off)
            name = data[strtab + name_off:end].decode("ascii", "replace")
            if name.startswith(prefix):
                symbols[name] = value
    if not symbols:
        raise TraceError(f"{path}: no {prefix}* symbol; is this an instrumented build?")
    return symbols


# ---------------------------------------------------------------------------
# Reading and decoding
# ---------------------------------------------------------------------------

BLOCK_RE = re.compile(r"^\s*0x[0-9A-Fa-f]+\s*:\s*((?:[0-9A-Fa-f]{2}\s*)+)$", re.M)


class Target(Swd):
    def read_bytes(self, addr, count):
        out = self._run("-r8", f"0x{addr:08X}", str(count))
        values = [int(x, 16) for m in BLOCK_RE.finditer(out) for x in m.group(1).split()]
        if len(values) < count:
            raise SwdError(self._error_from(out, "SWD read failed (is the ST-Link connected?)"))
        return bytes(values[:count])


GAP_EVENTS = {
    0x0006: "FW_ERROR",
    0x0400: "LIMITED_DISCOVERABLE_TIMEOUT", 0x0401: "PAIRING_COMPLETE", 0x0402: "PASS_KEY_REQ",
    0x0403: "AUTHORIZATION_REQ", 0x0404: "PERIPHERAL_SECURITY_INITIATED", 0x0405: "BOND_LOST",
    0x0407: "PROC_COMPLETE", 0x0408: "ADDR_NOT_RESOLVED", 0x0409: "NUMERIC_COMPARISON",
    0x040A: "KEYPRESS", 0x0800: "L2CAP_CONN_UPDATE_RESP", 0x0801: "L2CAP_PROC_TIMEOUT",
    0x0802: "L2CAP_CONN_UPDATE_REQ",
}
DISCONNECT_REASONS = {
    0x05: "authentication failure", 0x06: "key missing", 0x08: "connection timeout",
    0x13: "remote user terminated", 0x16: "terminated by local host", 0x22: "LL response timeout",
    0x3D: "MIC failure", 0x3E: "failed to be established",
}
FW_ERRORS = {
    0x01: "L2CAP recombination failure", 0x02: "GATT unexpected peer message", 0x03: "NVM level warning",
    0x04: "COC RX data length too large", 0x05: "ECOC already assigned DCID",
}
PEER_TYPES = {0: "public", 1: "random", 2: "public identity (resolved)", 3: "random identity (resolved)"}
QUIET_LE_SUBEVENTS = (0x03, 0x07, 0x0C)     # connection update, data length change, PHY update


def addr(b):
    return ":".join(f"{x:02X}" for x in reversed(b))


def decode(index, rec):
    """One trace record as a line of text, or None for an event not worth showing."""
    kind, a, b = rec[4], rec[5], rec[6]
    d = rec[8:32]
    if kind == 1:
        mode = "PAIRING mode" if a else "idle"
        rpa = addr(d[6:12]) if d[12] == 0 else ("-" if d[12] == 0xEE else f"(read error 0x{d[12]:02x})")
        if not b:
            privacy = "-"
        else:
            privacy = {0xEE: "not set", 0x00: "device privacy"}.get(d[15], f"set failed 0x{d[15]:02x}")
        return (f"{index:4d} ADV START    {mode:12s} bonded={b} adv_cmd=0x{d[13]:02x} | static addr {addr(d[0:6])}"
                f" | controller RPA for peer0 {rpa} | peer0 privacy: {privacy}")
    if kind == 2:
        peer_rpa = addr(d[17:23]) if any(d[17:23]) else "none"
        ours = f"{addr(d[11:17])} (controller RPA)" if any(d[11:17]) else "not a controller RPA"
        return (f"{index:4d} CONNECTED    status={d[0]} peer={addr(d[5:11])} ({PEER_TYPES.get(d[4], d[4])})"
                f" peerRPA={peer_rpa} | our address in this link: {ours}")
    if kind == 3:
        return f"{index:4d} DISCONNECTED reason=0x{a:02x} ({DISCONNECT_REASONS.get(a, '?')})"
    if kind == 5:
        return f"{index:4d} ENCRYPTION   status=0x{a:02x} enabled={b}"
    if kind == 6:
        code = a | b << 8
        extra = f" status={d[2]} reason=0x{d[3]:02x}" if code == 0x0401 else ""
        if code == 0x0006:
            # Type, length, then up to six of the bytes that come with it
            extra = (f" type=0x{d[0]:02x} ({FW_ERRORS.get(d[0], '?')})"
                     f" data={bytes(d[2:2 + min(d[1], 6)]).hex() or '-'}")
        return f"{index:4d} GAP/L2CAP    0x{code:04x} {GAP_EVENTS.get(code, '')}{extra}"
    if kind == 7:
        return None if a in QUIET_LE_SUBEVENTS else f"{index:4d} LE META      subevent=0x{a:02x}"
    if kind == 8:
        return f"{index:4d} HCI EVENT    0x{a:02x}"
    return f"{index:4d} ?            kind={kind}"


def timeline(count, blob, since=0):
    """Decoded lines for records since..count-1; only the last 64 are still in the ring."""
    first = max(since, count - RECORDS, 0)
    lines = []
    for i in range(first, count):
        slot = (i % RECORDS) * RECORD_SIZE
        line = decode(i, blob[slot:slot + RECORD_SIZE])
        if line:
            lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def open_target(args):
    elf = args.elf or os.environ.get("FLYSIGHT_ELF")
    if not elf:
        raise TraceError("pass --elf (or set FLYSIGHT_ELF) with the ELF of the firmware that is running")
    return connect(args, Target), elf_symbols(elf)


def cmd_instrument(args):
    for path in patch_sources(args.root):
        print(f"instrumented {path}")
    print("build and flash, then: ble_trace.py read --elf <the ELF you flashed>. Do not commit this.")
    return 0


def cmd_restore(args):
    for path in patch_sources(args.root, reverse=True):
        print(f"restored {path}")
    return 0


def cmd_mark(args):
    target, sym = open_target(args)
    print(int.from_bytes(target.read_bytes(sym["ble_trace_n"], 4), "little"))
    return 0


def cmd_read(args):
    target, sym = open_target(args)
    count = int.from_bytes(target.read_bytes(sym["ble_trace_n"], 4), "little")
    gatt = int.from_bytes(target.read_bytes(sym["ble_trace_gatt_n"], 4), "little")
    switches = ", ".join(f"{name} fix {'ON' if target.read_bytes(sym[var], 1)[0] else 'OFF'}"
                         for name, var in SWITCHES.items())
    blob = target.read_bytes(sym["ble_trace_log"], RECORDS * RECORD_SIZE)
    print(f"[{count} records, {switches}, {gatt} GATT events]")
    for line in timeline(count, blob, args.since):
        print(line)
    return 0


def cmd_set(args):
    target, sym = open_target(args)
    var = SWITCHES[args.switch]
    target.write8(sym[var], 1 if args.state == "on" else 0)
    state = "ON" if target.read_bytes(sym[var], 1)[0] else "OFF"
    print(f"{args.switch} fix: {state} (applies from the next advertising start; a reset turns it back on)")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Temporary BLE trace for the FlySight 2, read over SWD.")
    parser.add_argument("--programmer", help="path to STM32_Programmer_CLI")
    parser.add_argument("--openocd", help="path to OpenOCD, to use it instead of STM32_Programmer_CLI")
    parser.add_argument("--sn", help="ST-Link serial number, if several are connected")
    parser.add_argument("--swd-timeout", type=float, default=10, help="seconds before giving up on SWD")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("instrument", help="add the trace to the sources (temporary, do not commit)")
    p.add_argument("--root", default=REPO, help="firmware tree to edit (default: this repository)")
    p.set_defaults(func=cmd_instrument)

    p = sub.add_parser("restore", help="remove the trace from the sources")
    p.add_argument("--root", default=REPO, help="firmware tree to edit (default: this repository)")
    p.set_defaults(func=cmd_restore)

    for name, func, text in (("read", cmd_read, "print the recorded events"),
                             ("mark", cmd_mark, "print the number of events recorded so far"),
                             ("set", cmd_set, "turn a fix off or on in RAM")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--elf", help="ELF of the running firmware (or FLYSIGHT_ELF)")
        if name == "read":
            p.add_argument("--since", type=int, default=0, help="first record to print (see `mark`)")
        if name == "set":
            p.add_argument("switch", choices=sorted(SWITCHES))
            p.add_argument("state", choices=["on", "off"])
        p.set_defaults(func=func)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TraceError, SwdError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
