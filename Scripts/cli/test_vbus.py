"""
Tests for vbus.py that need no hardware.

Usage:
    python -m unittest test_vbus
"""

import contextlib
import io
import types
import unittest

import vbus
from vbus import (GPIOA_BSRR, GPIOA_IDR, GPIOA_MODER, GPIOA_ODR, MODER_MASK,
                  MODER_OUTPUT, VBUS_BIT, OpenOcd, State, Swd, SwdError)


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


class FakeGpioC:
    """GPIOC for PC12: a button that pulls the pin low when pressed, otherwise the pull-up wins."""

    GPIOC = vbus.GPIOC

    def __init__(self, moder=0xFFFFFFFF, odr=0x1000):
        self.moder = moder          # reset value of GPIOC.MODER: every pin an input
        self.odr = odr
        self.writes = []

    def idr(self):
        bit = 1 << vbus.BUTTON_PIN
        out = (self.moder >> (2 * vbus.BUTTON_PIN)) & 0b11 == 0b01
        high = (self.odr & bit) != 0 if out else True
        return bit if high else 0

    def _word(self, addr):
        return {self.GPIOC: self.moder, self.GPIOC + 0x10: self.idr(), self.GPIOC + 0x14: self.odr}[addr]

    def read(self, *specs):
        values = []
        for addr, width in specs:
            if width == 8:
                base = addr & ~3
                values.append((self.moder >> (8 * (addr - base))) & 0xFF)
            else:
                values.append(self._word(addr))
        return values

    def write8(self, addr, value):
        shift = 8 * (addr - self.GPIOC)
        self.writes.append(("w8", addr, value))
        self.moder = (self.moder & ~(0xFF << shift)) | (value << shift)

    def write32(self, addr, value, check=True):
        assert addr == self.GPIOC + 0x18
        self.writes.append(("w32", addr, value))
        self.odr = (self.odr | (value & 0xFFFF)) & ~(value >> 16)


class ButtonTests(unittest.TestCase):
    def test_press_pulls_pc12_low_and_leaves_other_pins(self):
        gpio = FakeGpioC(moder=0xABFFFFFF)
        vbus.press_button(gpio)
        self.assertFalse(gpio.idr())
        self.assertEqual(gpio.moder & ~(0b11 << 24), 0xABFFFFFF & ~(0b11 << 24))

    def test_press_only_touches_the_byte_holding_pc12(self):
        gpio = FakeGpioC()
        vbus.press_button(gpio)
        # PC12 is bits 24..25 of MODER, that is byte 3
        self.assertEqual([w for w in gpio.writes if w[0] == "w8"], [("w8", vbus.GPIOC + 3, 0xFD)])

    def test_release_gives_the_pin_back(self):
        gpio = FakeGpioC(moder=0xABFFFFFF)
        vbus.press_button(gpio)
        vbus.release_button(gpio)
        self.assertEqual(gpio.moder, 0xABFFFFFF & ~(0b11 << 24))
        self.assertTrue(gpio.idr())

    def test_odr_bit_is_cleared_through_bsrr(self):
        gpio = FakeGpioC(odr=0x1000)
        vbus.press_button(gpio)
        self.assertIn(("w32", vbus.GPIOC + 0x18, (1 << vbus.BUTTON_PIN) << 16), gpio.writes)

    def test_click_uses_one_access_per_edge(self):
        gpio = FakeGpioC()
        with contextlib.redirect_stdout(io.StringIO()):
            vbus.cmd_button(gpio, types.SimpleNamespace(action="click", count=2, hold=0, gap=0))
        edges = [value for kind, _, value in gpio.writes if kind == "w8"]
        # down, up, down, up, then the final release
        self.assertEqual(edges, [0xFD, 0xFC, 0xFD, 0xFC, 0xFC])
        self.assertTrue(gpio.idr())

    def test_click_releases_the_button_even_when_a_step_fails(self):
        class FailsOnce(FakeGpioC):
            def write8(self, addr, value):
                if len([w for w in self.writes if w[0] == "w8"]) == 1 and not getattr(self, "failed", False):
                    self.failed = True      # the write that should let the button up
                    raise SwdError("probe went away")
                super().write8(addr, value)

        gpio = FailsOnce()
        args = types.SimpleNamespace(action="click", count=2, hold=0, gap=0)
        with self.assertRaises(SwdError):
            vbus.cmd_button(gpio, args)
        self.assertTrue(gpio.idr())


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


class OpenOcdTests(unittest.TestCase):
    def ocd(self, stdout="", stderr=""):
        def runner(args, **kw):
            self.args = args
            return types.SimpleNamespace(stdout=stdout, stderr=stderr)
        return OpenOcd("/x/openocd", sn="ABC", runner=runner)

    def test_read_parses_words_and_bytes_in_order(self):
        err = ("Info : STLINK V3J17M10 (API v3) VID:PID 0483:3754\n"
               "0x48000000: 6bfffbcf \n\n0x48000810: 03 \n")
        ocd = self.ocd(stderr=err)
        self.assertEqual(ocd.read((0x48000000, 32), (0x48000810, 8)), [0x6BFFFBCF, 0x03])
        self.assertIn("mdw 0x48000000", self.args)
        self.assertIn("mdb 0x48000810", self.args)
        self.assertIn("adapter serial ABC", self.args)
        self.assertLess(self.args.index("init"), self.args.index("mdw 0x48000000"))
        self.assertEqual(self.args[-1], "exit")

    def test_does_not_let_the_target_script_touch_dbgmcu(self):
        self.ocd(stderr="0x48000000: 00 \n").read((0x48000000, 8))
        self.assertIn("stm32wbx.cpu configure -event examine-end {}", self.args)

    def test_swd_is_asked_for_under_the_name_each_driver_knows(self):
        # OpenOCD 0.12.0 drives an ST-Link through "hla_swd"; later versions through "swd"
        self.ocd(stderr="0x48000000: 00 \n").read((0x48000000, 8))
        transport = self.args[self.args.index("interface/stlink.cfg") + 2]
        self.assertLess(transport.index("hla_swd"), transport.index("select swd"))
        self.assertIn("catch", transport)
        self.assertLess(self.args.index(transport), self.args.index("target/stm32wbx.cfg"))

    def test_read_bytes_joins_the_lines_of_a_dump(self):
        err = ("0x20001000: 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f \n"
               "0x20001010: 10 11 12 13 \n")
        self.assertEqual(self.ocd(stderr=err).read_bytes(0x20001000, 20), bytes(range(20)))
        self.assertIn("mdb 0x20001000 20", self.args)

    def test_error_line_is_reported(self):
        with self.assertRaisesRegex(SwdError, "open failed"):
            self.ocd(stderr="Error: open failed\n").read((0x48000000, 32))

    def test_unreachable_target_explains_the_debugger_flag(self):
        err = "Error: init mode failed (unable to connect to the target)\n"
        with self.assertRaisesRegex(SwdError, "CFG_DEBUGGER_SUPPORTED"):
            self.ocd(stderr=err).write8(0x48000000, 0xDF)

    def test_writes_and_reset(self):
        ocd = self.ocd()
        ocd.write8(0x48000000, 0xDF)
        self.assertIn("mwb 0x48000000 0xDF", self.args)
        ocd.write32(0x48000018, 1 << 18, check=False)
        self.assertIn("mww 0x48000018 0x00040000", self.args)
        ocd.reset()
        self.assertIn("reset run", self.args)


class ConnectTests(unittest.TestCase):
    def args(self, **kw):
        return types.SimpleNamespace(**{"programmer": None, "openocd": None, "sn": None,
                                        "swd_timeout": 10, **kw})

    def patch(self, programmer, openocd):
        def find_programmer(explicit=None):
            if not programmer:
                raise SwdError("STM32_Programmer_CLI not found")
            return programmer
        saved = vbus.find_programmer, vbus.find_openocd
        vbus.find_programmer, vbus.find_openocd = find_programmer, lambda explicit=None: explicit or openocd
        self.addCleanup(lambda: setattr(vbus, "find_programmer", saved[0]))
        self.addCleanup(lambda: setattr(vbus, "find_openocd", saved[1]))

    def test_programmer_is_preferred(self):
        self.patch("/x/STM32_Programmer_CLI", "/x/openocd")
        self.assertIsInstance(vbus.connect(self.args()), Swd)

    def test_openocd_when_there_is_no_programmer(self):
        self.patch(None, "/x/openocd")
        self.assertIsInstance(vbus.connect(self.args()), OpenOcd)

    def test_openocd_on_request(self):
        self.patch("/x/STM32_Programmer_CLI", None)
        swd = vbus.connect(self.args(openocd="/y/openocd"))
        self.assertIsInstance(swd, OpenOcd)
        self.assertEqual(swd.programmer, "/y/openocd")

    def test_an_explicit_programmer_that_is_missing_is_an_error(self):
        self.patch(None, "/x/openocd")
        with self.assertRaises(SwdError):
            vbus.connect(self.args(programmer="/nowhere/STM32_Programmer_CLI"))

    def test_nothing_installed(self):
        self.patch(None, None)
        with self.assertRaisesRegex(SwdError, "OpenOCD"):
            vbus.connect(self.args())


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

    # What Windows 11 prints for an ST-Link, a mass-storage FlySight and one of its interfaces
    PNPUTIL = (
        "Instance ID:                USB\\VID_0483&PID_3754\\003F004D3234511437333934\n"
        "Device Description:         USB Composite Device\n"
        "Status:                     Started\n\n"
        "Instance ID:                USB\\VID_0483&PID_3754&MI_00\\6&379B478D&0&0000\n"
        "Device Description:         ST-Link Debug\n\n"
        "Instance ID:                USB\\VID_16D0&PID_0569\\0123456789AB\n"
        "Device Description:         USB Mass Storage Device\n")

    def test_pnputil_device_present(self):
        self.assertTrue(vbus.pnputil_has_device(self.PNPUTIL, 0x16D0, 0x0569))
        self.assertTrue(vbus.pnputil_has_device(self.PNPUTIL.lower(), 0x16D0, 0x0569))

    def test_pnputil_other_device_only(self):
        self.assertFalse(vbus.pnputil_has_device(self.PNPUTIL.split("Instance ID:                USB\\VID_16D0")[0],
                                                 0x16D0, 0x0569))
        self.assertFalse(vbus.pnputil_has_device(self.PNPUTIL, 0x16D0, 0x0001))

    def test_pnputil_an_interface_is_not_the_device(self):
        only_interface = self.PNPUTIL.split("\n\n")[1]
        self.assertFalse(vbus.pnputil_has_device(only_interface, 0x0483, 0x3754))

    def test_pnputil_label_in_another_language(self):
        self.assertTrue(vbus.pnputil_has_device("ID d'instance :  USB\\VID_16D0&PID_0569\\0123\n", 0x16D0, 0x0569))


if __name__ == "__main__":
    unittest.main()
