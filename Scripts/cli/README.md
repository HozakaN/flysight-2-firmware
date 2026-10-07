# FlySight 2 test CLI (ST-Link)

`vbus.py` drives a FlySight 2 through its ST-Link, so firmware can be tested
without touching the USB cable. There is no CLI over USB: the device only
enumerates as mass storage.

It makes the firmware believe the USB cable was unplugged or plugged back in,
while the cable stays connected. It takes over `VBUS_DIV` (PA2), the pin the
firmware uses to detect USB power (`FlySight/vbus.c`), so the real code path
runs: EXTI2 → mode state machine → `FS_USBMode_Init` / `FS_USBMode_DeInit`.

```bash
python vbus.py status                     # what the firmware and the host see
python vbus.py unplug                     # firmware sees VBUS low, host loses the device
python vbus.py plug                       # real VBUS again, host re-enumerates
python vbus.py cycle -n 20 --off 1 --on 1 # soak test, checks every transition
python vbus.py reset                      # reset the MCU (clears any override)
python vbus.py reset-probe                # recover a stuck ST-Link
```

`unplug` and `plug` wait for the firmware (USB clock and D+ pull-up, read over SWD)
and for the host (`ioreg` on macOS, sysfs on Linux) to react, and print how long it
took. `cycle` also fails if the firmware faulted (CFSR/HFSR) and always gives the pin
back on exit, including on Ctrl-C. Exit codes: 0 ok, 1 a check failed, 2 error.

## Requirements

- Python 3.7+, no packages to install.
- STM32CubeProgrammer or STM32CubeIDE (`STM32_Programmer_CLI` is found automatically,
  or pass `--programmer` / set `STM32_PROGRAMMER_CLI`) and an ST-Link on the SWD pins.
- **A firmware built with `CFG_DEBUGGER_SUPPORTED=1`** (CubeMX: STM32_WPAN → Debugger,
  or `Core/Inc/app_conf.h`). Otherwise `APPD_Init` switches PA13/PA14 to analog and
  disables debug in low-power modes, and the probe cannot reach the MCU while it runs
  (the tool reports this after `--swd-timeout`, default 10 s).

## Notes

- The override lives in the GPIO registers: it survives the script exiting and is
  cleared by any reset. Booting with VBUS low cannot be simulated this way, because
  the firmware reconfigures PA2 during start-up.
- Use `--vid` / `--pid` if the USB descriptor changes, `--no-host` when the cable is
  not plugged into the machine running the script, `--sn` with several ST-Links.
- The ST-Link can get stuck when an SWD access is interrupted (libusb timeouts,
  `ST-LINK SN : -`). `reset-probe` resets it at USB level; unplugging it also works.

## Tests

The tool's own tests need no hardware:

```bash
python -m unittest test_vbus
```
