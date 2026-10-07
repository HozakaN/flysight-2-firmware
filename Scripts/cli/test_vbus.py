"""
Tests for vbus.py that need no hardware.

Usage:
    python -m unittest test_vbus
"""

import types
import unittest

import vbus
from vbus import (GPIOA_BSRR, GPIOA_IDR, GPIOA_MODER, GPIOA_ODR, MODER_MASK,
                  MODER_OUTPUT, VBUS_BIT, State, Swd, SwdError)


class FakeGpioA:
    """Just enough of GPIOA for PA2: input follows VBUS, output follows ODR."""

    def __init__(self, vbus_present=True, moder=0x6ABFFBCF, odr=0x8000):
        self.vbus_present = vbus_present
        self.moder = moder
        self.odr = odr
        self.stuck_high = False
        self.writes = []

    def idr(self):
        out = (self.moder & MODER_MASK) == MODER_OUTPUT
        high = (self.odr & VBUS_BIT) != 0 if out else self.vbus_present
        if out and self.stuck_high:
            high = True
        return (0x8020 & ~VBUS_BIT) | (VBUS_BIT if high else 0)

    def read(self, *specs):
        values = []
        for addr, width in specs:
            word = {GPIOA_MODER: self.moder, GPIOA_IDR: self.idr(), GPIOA_ODR: self.odr}[addr]
            values.append(word & 0xFF if width == 8 else word)
        return values

    def write8(self, addr, value):
        assert addr == GPIOA_MODER
        self.writes.append(("w8", addr, value))
        self.moder = (self.moder & ~0xFF) | value

    def write32(self, addr, value, check=True):
        assert addr == GPIOA_BSRR
        self.writes.append(("w32", addr, value))
        self.odr = (self.odr | (value & 0xFFFF)) & ~(value >> 16)


class ForceTests(unittest.TestCase):
    def test_unplug_drives_pa2_low_and_keeps_other_pins(self):
        gpio = FakeGpioA()
        vbus.force_unplugged(gpio)
        self.assertEqual(gpio.moder & MODER_MASK, MODER_OUTPUT)
        self.assertEqual(gpio.moder & ~MODER_MASK, 0x6ABFFBCF & ~MODER_MASK)
        self.assertFalse(gpio.idr() & VBUS_BIT)

    def test_release_restores_input(self):
        gpio = FakeGpioA()
        vbus.force_unplugged(gpio)
        vbus.release_vbus(gpio)
        self.assertEqual(gpio.moder, 0x6ABFFBCF)
        self.assertTrue(gpio.idr() & VBUS_BIT)

    def test_release_follows_real_vbus(self):
        gpio = FakeGpioA(vbus_present=False)
        vbus.force_unplugged(gpio)
        vbus.release_vbus(gpio)
        self.assertFalse(gpio.idr() & VBUS_BIT)

    def test_moder_is_written_one_byte_only(self):
        gpio = FakeGpioA()
        vbus.force_unplugged(gpio)
        vbus.release_vbus(gpio)
        self.assertTrue(gpio.writes)
        self.assertTrue(all(kind in ("w8", "w32") for kind, *_ in gpio.writes))
        self.assertFalse([w for w in gpio.writes if w[0] == "w32" and w[1] == GPIOA_MODER])

    def test_odr_bit_is_cleared_through_bsrr(self):
        gpio = FakeGpioA(odr=0x8000 | VBUS_BIT)
        vbus.force_unplugged(gpio)
        self.assertIn(("w32", GPIOA_BSRR, VBUS_BIT << 16), gpio.writes)
        self.assertFalse(gpio.odr & VBUS_BIT)

    def test_odr_clear_is_skipped_when_already_low(self):
        gpio = FakeGpioA()
        vbus.force_unplugged(gpio)
        self.assertFalse([w for w in gpio.writes if w[1] == GPIOA_BSRR])

    def test_unplug_fails_if_pin_cannot_go_low(self):
        gpio = FakeGpioA()
        gpio.stuck_high = True
        with self.assertRaises(SwdError):
            vbus.force_unplugged(gpio)


class StateTests(unittest.TestCase):
    def state(self, **kw):
        base = dict(moder=0x6ABFFBCF, idr=0x8024, odr=0x8000, apb1enr1=0x05800400,
                    bcdr=0, cfsr=0, hfsr=0)
        base.update(kw)
        return State(**base)

    def test_pin_level(self):
        self.assertTrue(self.state(idr=0x24).pin_high)
        self.assertFalse(self.state(idr=0x20).pin_high)

    def test_forced(self):
        self.assertFalse(self.state().forced)
        self.assertTrue(self.state(moder=0x6ABFFBDF).forced)

    def test_usb_active_needs_clock_and_pullup(self):
        usben = 1 << 26
        self.assertTrue(self.state(apb1enr1=usben, bcdr=1 << 15).usb_active)
        self.assertFalse(self.state(apb1enr1=0, bcdr=1 << 15).usb_active)
        self.assertFalse(self.state(apb1enr1=usben, bcdr=0).usb_active)

    def test_fault_text(self):
        st = self.state(cfsr=1 << 17, hfsr=1 << 30)
        self.assertTrue(st.faulted)
        self.assertEqual(st.fault_text(), "HardFault(forced) INVSTATE")
        self.assertFalse(self.state().faulted)


class SwdTests(unittest.TestCase):
    def swd(self, stdout, stderr=""):
        def runner(args, **kw):
            self.args = args
            return types.SimpleNamespace(stdout=stdout, stderr=stderr)
        return Swd("/x/STM32_Programmer_CLI", sn="ABC", runner=runner)

    def test_read_parses_values_and_strips_colors(self):
        out = ("\x1b[39;49m\x1b[0mReading 32-bit memory content\n"
               "0x48000000 : 6ABFFBCF\n0x48000010 : 00008024\n")
        swd = self.swd(out)
        self.assertEqual(swd.read((0x48000000, 32), (0x48000010, 32)), [0x6ABFFBCF, 0x8024])
        self.assertIn("mode=HOTPLUG", self.args)
        self.assertIn("sn=ABC", self.args)
        self.assertEqual(self.args[self.args.index("-r32") + 1:self.args.index("-r32") + 3],
                         ["0x48000000", "4"])

    def test_read_byte(self):
        swd = self.swd("0x48000000 : CF\n")
        self.assertEqual(swd.read((0x48000000, 8)), [0xCF])
        self.assertIn("-r8", self.args)

    def test_read_reports_programmer_error(self):
        swd = self.swd("Error: No STM32 target found!\n")
        with self.assertRaisesRegex(SwdError, "No STM32 target"):
            swd.read((0x48000000, 32))

    def test_hung_probe_gives_actionable_error(self):
        import subprocess

        def runner(args, **kw):
            raise subprocess.TimeoutExpired(args, kw["timeout"])
        swd = Swd("/x/STM32_Programmer_CLI", timeout=1, runner=runner)
        with self.assertRaisesRegex(SwdError, "CFG_DEBUGGER_SUPPORTED"):
            swd.read((0x48000000, 32))

    def test_write8_checks_status(self):
        self.swd("Downloading 8-bit data done successfully\n").write8(0x48000000, 0xDF)
        with self.assertRaises(SwdError):
            self.swd("Downloading 8-bit data failed...\n").write8(0x48000000, 0xDF)

    def test_write32_error_can_be_ignored(self):
        out = "Error: Failed to download data! If it's a Flash memory\n"
        self.swd(out).write32(0x48000018, 1 << 18, check=False)
        with self.assertRaises(SwdError):
            self.swd(out).write32(0x48000018, 1 << 18)


class HostTests(unittest.TestCase):
    IOREG = '''+-o Root  <class IORegistryEntry>
  +-o AppleT8142USBXHCI@00000000  <class AppleT8142USBXHCI>
  | +-o STLINK-V3@00100000  <class IOUSBHostDevice>
  | | {
  | |   "idProduct" = 14164
  | |   "idVendor" = 1155
  | | }
  | +-o FlySight GPS@01100000  <class IOUSBHostDevice>
  | | {
  | |   "idProduct" = 1385
  | |   "idVendor" = 5840
  | | }
'''

    def test_present(self):
        self.assertTrue(vbus.ioreg_has_device(self.IOREG, 0x16D0, 0x0569))

    def test_other_device_only(self):
        self.assertFalse(vbus.ioreg_has_device(self.IOREG, 0x16D0, 0x0001))
        self.assertFalse(vbus.ioreg_has_device(self.IOREG.split("+-o FlySight")[0], 0x16D0, 0x0569))

    def test_vid_and_pid_must_be_on_the_same_device(self):
        text = self.IOREG.replace('"idProduct" = 14164', '"idProduct" = 1385')
        self.assertFalse(vbus.ioreg_has_device(text.split("+-o FlySight")[0], 0x16D0, 0x0569))


if __name__ == "__main__":
    unittest.main()
