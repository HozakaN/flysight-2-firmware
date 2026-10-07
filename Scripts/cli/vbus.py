#!/usr/bin/env python3
"""
Simulate plugging / unplugging the USB cable of a FlySight 2 over SWD (ST-Link).

The USB cable stays physically connected. The firmware decides that USB power
is present from one GPIO, PA2 (VBUS_DIV), an input with an interrupt on both
edges (see FlySight/vbus.c and FlySight/mode.c). This tool takes over that pin:

  unplug  drives PA2 low (pin switched to output)
          -> firmware sees VBUS_LOW, leaves USB mode, stops the USB peripheral,
             the host sees the device disappear.
  plug    gives PA2 back (pin switched to input)
          -> the real VBUS level returns, the EXTI fires on the rising edge,
             firmware enters USB mode and the host enumerates it again.

The firmware runs exactly the code path of a real unplug/plug (EXTI2 ->
FS_VBUS_Triggered -> mode state machine); it needs no test hook. The override
lives in the MCU's GPIO registers, so it survives this script exiting and is
cleared by any reset.

Requires STM32_Programmer_CLI (STM32CubeProgrammer, also bundled with
STM32CubeIDE). SWD access is HOTPLUG: the core is never halted.

Usage:
    python vbus.py status
    python vbus.py unplug
    python vbus.py plug
    python vbus.py cycle -n 20 --off 2 --on 3
    python vbus.py reset
"""

import argparse
import ctypes
import ctypes.util
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

# VBUS_DIV is PA2 (Core/Inc/main.h)
GPIOA = 0x48000000
GPIOA_MODER = GPIOA + 0x00
GPIOA_IDR = GPIOA + 0x10
GPIOA_ODR = GPIOA + 0x14
GPIOA_BSRR = GPIOA + 0x18
VBUS_PIN = 2
VBUS_BIT = 1 << VBUS_PIN
MODER_MASK = 0b11 << (2 * VBUS_PIN)   # PA0..PA3 live in byte 0 of MODER
MODER_OUTPUT = 0b01 << (2 * VBUS_PIN)

# Used to check from the MCU side that the firmware reacted
RCC_APB1ENR1 = 0x58000058
RCC_APB1ENR1_USBEN = 1 << 26
USB_BCDR = 0x40006858
USB_BCDR_DPPU = 1 << 15               # D+ pull-up: device is attached to the bus

SCB_CFSR = 0xE000ED28
SCB_HFSR = 0xE000ED2C
CFSR_BITS = [
    (0, "IACCVIOL"), (1, "DACCVIOL"), (3, "MUNSTKERR"), (4, "MSTKERR"),
    (8, "IBUSERR"), (9, "PRECISERR"), (10, "IMPRECISERR"), (11, "UNSTKERR"),
    (12, "STKERR"), (16, "UNDEFINSTR"), (17, "INVSTATE"), (18, "INVPC"),
    (19, "NOCP"), (24, "UNALIGNED"), (25, "DIVBYZERO"),
]
HFSR_FORCED = 1 << 30

# ST-LINK/V3 family, for reset-probe
ST_LINK_VID = 0x0483
ST_LINK_PIDS = [0x3754, 0x374F, 0x374E, 0x3753, 0x3752, 0x374D, 0x3744, 0x3748, 0x374B]

# Defaults from USB_Device/App/usbd_desc.c
DEFAULT_VID = 0x16D0
DEFAULT_PID = 0x0569

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DATA_RE = re.compile(r"^(0x[0-9A-Fa-f]+)\s*:\s*([0-9A-Fa-f]{2,8})\s*$", re.M)


SWD_HUNG_HINT = (
    "SWD did not answer. A firmware built with CFG_DEBUGGER_SUPPORTED=0 (the default) "
    "turns the SWD pins to analog and disables debug in low-power modes at boot "
    "(APPD_Init in Core/Src/app_debug.c), so the probe cannot reach it while it runs. "
    "Flash a build with CFG_DEBUGGER_SUPPORTED=1 (CubeMX: STM32_WPAN > Debugger). "
    "If the probe itself is stuck (libusb timeouts), run: vbus.py reset-probe"
)


class SwdError(RuntimeError):
    pass


def find_programmer(explicit=None):
    """Locate STM32_Programmer_CLI."""
    candidates = [explicit, os.environ.get("STM32_PROGRAMMER_CLI"),
                  shutil.which("STM32_Programmer_CLI")]
    for pattern in [
        "/Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/"
        "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.*/tools/bin/STM32_Programmer_CLI",
        "/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/"
        "STM32CubeProgrammer.app/Contents/MacOS/bin/STM32_Programmer_CLI",
        os.path.expanduser("~/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI"),
        "/opt/st/stm32cubeide_*/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.*"
        "/tools/bin/STM32_Programmer_CLI",
        "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe",
    ]:
        candidates.extend(sorted(glob.glob(pattern), reverse=True))

    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise SwdError("STM32_Programmer_CLI not found. Install STM32CubeProgrammer or "
                   "STM32CubeIDE, or pass --programmer / set STM32_PROGRAMMER_CLI.")


class Swd:
    """Memory access to the target through STM32_Programmer_CLI."""

    def __init__(self, programmer, sn=None, timeout=10, runner=subprocess.run):
        self.programmer = programmer
        self.sn = sn
        self.timeout = timeout
        self._runner = runner

    def _run(self, *cmds):
        args = [self.programmer, "-c", "port=SWD", "mode=HOTPLUG", "ap=0"]
        if self.sn:
            args.append(f"sn={self.sn}")
        args.extend(str(c) for c in cmds)
        try:
            proc = self._runner(args, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise SwdError(SWD_HUNG_HINT) from None
        return ANSI_RE.sub("", proc.stdout + proc.stderr)

    @staticmethod
    def _error_from(out, default):
        for line in out.splitlines():
            if line.startswith("Error") or "No STM32 target" in line or "No debug probe" in line:
                return line.strip()
        return default

    def read(self, *specs):
        """Read (address, width) pairs, width 8 or 32, in a single SWD session."""
        cmds = []
        for addr, width in specs:
            cmds += [f"-r{width}", f"0x{addr:08X}", "1" if width == 8 else "4"]
        out = self._run(*cmds)
        values = [int(m.group(2), 16) for m in DATA_RE.finditer(out)]
        if len(values) != len(specs):
            raise SwdError(self._error_from(out, "SWD read failed (is the ST-Link connected?)"))
        return values

    def write8(self, addr, value):
        out = self._run("-w8", f"0x{addr:08X}", f"0x{value:02X}")
        if "successfully" not in out:
            raise SwdError(self._error_from(out, f"SWD write to 0x{addr:08X} failed"))

    def write32(self, addr, value, check=True):
        out = self._run("-w32", f"0x{addr:08X}", f"0x{value:08X}")
        if check and "complete" not in out:
            raise SwdError(self._error_from(out, f"SWD write to 0x{addr:08X} failed"))

    def reset(self):
        out = self._run("-rst")
        if "Software reset is performed" not in out:
            raise SwdError(self._error_from(out, "SWD reset failed"))


@dataclass
class State:
    moder: int
    idr: int
    odr: int
    apb1enr1: int
    bcdr: int
    cfsr: int
    hfsr: int

    @property
    def pin_high(self):
        """What the firmware reads on VBUS_DIV."""
        return bool(self.idr & VBUS_BIT)

    @property
    def forced(self):
        """PA2 is driven by the MCU instead of following VBUS."""
        return (self.moder & MODER_MASK) == MODER_OUTPUT

    @property
    def usb_active(self):
        """Firmware has the USB peripheral clocked and attached to the bus."""
        return bool(self.apb1enr1 & RCC_APB1ENR1_USBEN) and bool(self.bcdr & USB_BCDR_DPPU)

    @property
    def faulted(self):
        return bool(self.cfsr or self.hfsr)

    def fault_text(self):
        names = [name for bit, name in CFSR_BITS if self.cfsr & (1 << bit)]
        if self.hfsr & HFSR_FORCED:
            names.insert(0, "HardFault(forced)")
        return " ".join(names) or f"HFSR=0x{self.hfsr:08X}"


def read_state(swd):
    return State(*swd.read(
        (GPIOA_MODER, 32), (GPIOA_IDR, 32), (GPIOA_ODR, 32),
        (RCC_APB1ENR1, 32), (USB_BCDR, 32), (SCB_CFSR, 32), (SCB_HFSR, 32)))


def clear_faults(swd):
    """Clear CFSR/HFSR (sticky across system resets). Returns True if they read back as zero."""
    # Both are write-1-to-clear, so the programmer's read-back verify always
    # "fails"; ignore its status and check the registers ourselves.
    swd.write32(SCB_CFSR, 0xFFFFFFFF, check=False)
    swd.write32(SCB_HFSR, 0xFFFFFFFF, check=False)
    return not any(swd.read((SCB_CFSR, 32), (SCB_HFSR, 32)))


def force_unplugged(swd):
    """Make the firmware see VBUS low."""
    moder, odr = swd.read((GPIOA_MODER, 8), (GPIOA_ODR, 32))
    if odr & VBUS_BIT:
        # BSRR is write-only, so the programmer reports a bogus verify error
        swd.write32(GPIOA_BSRR, VBUS_BIT << 16, check=False)
    swd.write8(GPIOA_MODER, (moder & ~MODER_MASK) | MODER_OUTPUT)

    moder, idr = swd.read((GPIOA_MODER, 32), (GPIOA_IDR, 32))
    if (moder & MODER_MASK) != MODER_OUTPUT or idr & VBUS_BIT:
        raise SwdError("could not drive PA2 low (did the firmware reconfigure the pin?)")


def release_vbus(swd):
    """Give PA2 back to the real VBUS."""
    moder, = swd.read((GPIOA_MODER, 8))
    swd.write8(GPIOA_MODER, moder & ~MODER_MASK)

    moder, = swd.read((GPIOA_MODER, 32))
    if (moder & MODER_MASK) == MODER_OUTPUT:
        raise SwdError("could not release PA2")


def reset_probe(programmer):
    """USB-reset the ST-Link itself (not the target). It can get stuck when an SWD access is killed."""
    libs = glob.glob(os.path.join(os.path.dirname(programmer), "libusb-1.0*"))
    libs.append(ctypes.util.find_library("usb-1.0"))
    lib = None
    for path in filter(None, libs):
        try:
            lib = ctypes.CDLL(path)
            break
        except OSError:
            continue
    if lib is None:
        raise SwdError("libusb not found; unplug and replug the ST-Link instead")

    lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
    lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
    lib.libusb_reset_device.argtypes = [ctypes.c_void_p]
    lib.libusb_close.argtypes = [ctypes.c_void_p]

    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        raise SwdError("libusb_init failed")
    try:
        for pid in ST_LINK_PIDS:
            handle = lib.libusb_open_device_with_vid_pid(ctx, ST_LINK_VID, pid)
            if not handle:
                continue
            rc = lib.libusb_reset_device(handle)
            lib.libusb_close(handle)
            # NOT_FOUND (-5): the probe already re-enumerated under a new address
            return rc in (0, -5)
    finally:
        lib.libusb_exit(ctx)
    return False


def host_has_device(vid, pid):
    """True/False if the host sees the FlySight on USB, None if unsupported here."""
    if sys.platform == "darwin":
        out = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"],
                             capture_output=True, text=True).stdout
        return ioreg_has_device(out, vid, pid)
    if sys.platform.startswith("linux"):
        want = (f"{vid:04x}", f"{pid:04x}")
        for dev in glob.glob("/sys/bus/usb/devices/*"):
            try:
                with open(os.path.join(dev, "idVendor")) as f:
                    v = f.read().strip()
                with open(os.path.join(dev, "idProduct")) as f:
                    p = f.read().strip()
            except OSError:
                continue
            if (v, p) == want:
                return True
        return False
    return None


def ioreg_has_device(text, vid, pid):
    for block in re.split(r"(?m)^[\s|]*\+-o ", text):
        if re.search(rf'"idVendor" = {vid}\b', block) and re.search(rf'"idProduct" = {pid}\b', block):
            return True
    return False


def wait_for(predicate, timeout, interval=0.05):
    """Seconds until predicate() is true, or None on timeout."""
    start = time.monotonic()
    while True:
        if predicate():
            return time.monotonic() - start
        if time.monotonic() - start >= timeout:
            return None
        time.sleep(interval)


class Bench:
    """What the tests look at: the MCU's reaction, and optionally the host's."""

    def __init__(self, swd, vid, pid, check_host):
        self.swd = swd
        self.vid = vid
        self.pid = pid
        self.check_host = check_host

    def wait_firmware(self, active, timeout):
        return wait_for(lambda: read_state(self.swd).usb_active == active, timeout)

    def wait_host(self, present, timeout):
        return wait_for(lambda: host_has_device(self.vid, self.pid) == present, timeout)


def fmt_time(t):
    return "TIMEOUT" if t is None else f"{t:.2f}s"


def explain_failure(swd):
    """Say why the firmware may not have reacted."""
    st = read_state(swd)
    if st.faulted:
        print(f"  firmware has faulted since the last clear: {st.fault_text()} "
              f"(CFSR=0x{st.cfsr:08X} HFSR=0x{st.hfsr:08X}); a crashed firmware cannot react")


def make_bench(swd, args):
    check_host = not args.no_host
    if check_host and host_has_device(args.vid, args.pid) is None:
        print("note: host-side USB check not supported on this platform", file=sys.stderr)
        check_host = False
    return Bench(swd, args.vid, args.pid, check_host)


def cmd_status(swd, args):
    st = read_state(swd)
    print(f"VBUS_DIV (PA2)   : {'HIGH' if st.pin_high else 'LOW'}"
          f"  ({'FORCED by this tool' if st.forced else 'following the real VBUS'})")
    print(f"firmware USB     : {'active (clock on, D+ pull-up on)' if st.usb_active else 'off'}")
    host = host_has_device(args.vid, args.pid)
    if host is not None:
        print(f"host sees USB    : {'yes' if host else 'no'}  ({args.vid:04x}:{args.pid:04x})")
    if st.faulted:
        print(f"fault flags      : {st.fault_text()}  "
              f"(CFSR=0x{st.cfsr:08X} HFSR=0x{st.hfsr:08X}; sticky, see --clear-faults)")
    else:
        print("fault flags      : none")
    if args.clear_faults:
        print("fault flags cleared" if clear_faults(swd) else "could not clear the fault flags")
    return 0


def cmd_unplug(swd, args):
    bench = make_bench(swd, args)
    force_unplugged(swd)
    print("PA2 forced low: firmware sees VBUS_LOW")
    if args.no_wait:
        return 0

    ok = True
    t = bench.wait_firmware(False, args.timeout)
    print(f"  firmware left USB mode : {fmt_time(t)}")
    ok &= t is not None
    if bench.check_host:
        t = bench.wait_host(False, args.timeout)
        print(f"  host lost the device   : {fmt_time(t)}")
        ok &= t is not None
    if not ok:
        explain_failure(swd)
    return 0 if ok else 1


def cmd_plug(swd, args):
    bench = make_bench(swd, args)
    release_vbus(swd)
    print("PA2 released: firmware sees the real VBUS")
    if args.no_wait:
        return 0

    ok = True
    t = bench.wait_firmware(True, args.timeout)
    print(f"  firmware entered USB mode : {fmt_time(t)}")
    ok &= t is not None
    if bench.check_host:
        t = bench.wait_host(True, args.timeout)
        print(f"  host enumerated device    : {fmt_time(t)}")
        ok &= t is not None
    if not ok:
        explain_failure(swd)
    return 0 if ok else 1


def cmd_cycle(swd, args):
    bench = make_bench(swd, args)
    if not clear_faults(swd):
        raise SwdError("could not clear the fault flags")
    release_vbus(swd)

    if bench.wait_firmware(True, args.timeout) is None:
        st = read_state(swd)
        why = f" ({st.fault_text()})" if st.faulted else ""
        print(f"FAIL: firmware is not in USB mode before the test{why}")
        return 1
    if bench.check_host and bench.wait_host(True, args.timeout) is None:
        print(f"note: host does not see {args.vid:04x}:{args.pid:04x}; "
              "cycling with firmware-side checks only (use --vid/--pid or --no-host)")
        bench.check_host = False

    failures = 0
    try:
        for i in range(1, args.count + 1):
            force_unplugged(swd)
            fw_off = bench.wait_firmware(False, args.timeout)
            host_off = bench.wait_host(False, args.timeout) if bench.check_host else 0
            time.sleep(args.off)

            release_vbus(swd)
            fw_on = bench.wait_firmware(True, args.timeout)
            host_on = bench.wait_host(True, args.timeout) if bench.check_host else 0
            time.sleep(args.on)

            st = read_state(swd)
            good = None not in (fw_off, host_off, fw_on, host_on) and not st.faulted
            line = (f"#{i:<3} unplug: fw {fmt_time(fw_off)}"
                    + (f" host {fmt_time(host_off)}" if bench.check_host else "")
                    + f" | plug: fw {fmt_time(fw_on)}"
                    + (f" host {fmt_time(host_on)}" if bench.check_host else ""))
            if st.faulted:
                line += f" | FAULT {st.fault_text()}"
            print(f"{line} | {'ok' if good else 'FAIL'}")

            if not good:
                failures += 1
                if not args.keep_going:
                    break
    finally:
        release_vbus(swd)

    print(f"{failures} failure(s)" if failures else f"{args.count} cycle(s) ok")
    return 1 if failures else 0


def cmd_reset(swd, args):
    swd.reset()
    print("target reset (PA2 override, if any, is gone)")
    time.sleep(args.settle)
    cmd_status(swd, argparse.Namespace(vid=args.vid, pid=args.pid, clear_faults=False))
    return 0


def cmd_reset_probe(swd, args):
    if not reset_probe(swd.programmer):
        raise SwdError("no ST-Link found on USB")
    time.sleep(2)
    print("ST-Link reset")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Simulate plugging/unplugging the FlySight's USB cable over SWD")
    parser.add_argument("--programmer", help="path to STM32_Programmer_CLI")
    parser.add_argument("--sn", help="ST-Link serial number (when several probes are connected)")
    parser.add_argument("--swd-timeout", type=float, default=10,
                        help="seconds before an SWD access is considered hung (default 10)")
    parser.add_argument("--vid", type=lambda s: int(s, 16), default=DEFAULT_VID,
                        help=f"USB vendor id, hex (default {DEFAULT_VID:04x})")
    parser.add_argument("--pid", type=lambda s: int(s, 16), default=DEFAULT_PID,
                        help=f"USB product id, hex (default {DEFAULT_PID:04x})")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_wait(p):
        p.add_argument("--timeout", type=float, default=10,
                       help="seconds to wait for each reaction (default 10)")
        p.add_argument("--no-host", action="store_true", help="skip the host-side USB check")

    p = sub.add_parser("status", help="show what the firmware and the host currently see")
    p.add_argument("--clear-faults", action="store_true", help="clear CFSR/HFSR afterwards")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("unplug", help="make the firmware think USB was unplugged")
    with_wait(p)
    p.add_argument("--no-wait", action="store_true", help="return right after forcing the pin")
    p.set_defaults(func=cmd_unplug)

    p = sub.add_parser("plug", help="make the firmware think USB was plugged back in")
    with_wait(p)
    p.add_argument("--no-wait", action="store_true", help="return right after releasing the pin")
    p.set_defaults(func=cmd_plug)

    p = sub.add_parser("cycle", help="unplug/plug repeatedly and check the reaction each time")
    with_wait(p)
    p.add_argument("-n", "--count", type=int, default=5, help="number of cycles (default 5)")
    p.add_argument("--off", type=float, default=1.0, help="seconds unplugged (default 1)")
    p.add_argument("--on", type=float, default=1.0, help="seconds plugged between cycles (default 1)")
    p.add_argument("--keep-going", action="store_true", help="do not stop at the first failure")
    p.set_defaults(func=cmd_cycle)

    p = sub.add_parser("reset-probe", help="USB-reset a stuck ST-Link (not the target)")
    p.set_defaults(func=cmd_reset_probe)

    p = sub.add_parser("reset", help="reset the MCU through SWD")
    p.add_argument("--settle", type=float, default=3.0, help="seconds to wait before status (default 3)")
    p.set_defaults(func=cmd_reset)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        swd = Swd(find_programmer(args.programmer), sn=args.sn, timeout=args.swd_timeout)
        return args.func(swd, args)
    except SwdError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
