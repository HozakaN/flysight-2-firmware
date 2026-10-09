# FlySight 2 test CLI (ST-Link)

Two tools that work through the ST-Link while the firmware runs: `vbus.py` (USB
plug/unplug and the user button) and `ble_trace.py` (what the BLE stack reports,
see below).

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
python vbus.py button                     # two clicks on the user button: BLE pairing mode
python vbus.py button press / release     # hold the button down, let go
python vbus.py reset                      # reset the MCU (clears any override)
python vbus.py reset-probe                # recover a stuck ST-Link
```

`unplug` and `plug` wait for the firmware (USB clock and D+ pull-up, read over SWD)
and for the host (`ioreg` on macOS, sysfs on Linux) to react, and print how long it
took. `cycle` also fails if the firmware faulted (CFSR/HFSR) and always gives the pin
back on exit, including on Ctrl-C. Exit codes: 0 ok, 1 a check failed, 2 error.

`button` drives the user button (PC12, pressed when low) the same way. Only the sleep
mode reacts to it, so `unplug` first. The default two clicks start BLE pairing mode:
for 30 s the FlySight advertises with the pairing flag set and accepts any central.

## Requirements

- Python 3.7+, no packages to install.
- An ST-Link on the SWD pins, and one of:
  - STM32CubeProgrammer or STM32CubeIDE (`STM32_Programmer_CLI` is found automatically,
    or pass `--programmer` / set `STM32_PROGRAMMER_CLI`);
  - OpenOCD (`openocd` on the `PATH`, or pass `--openocd` / set `OPENOCD`). It is used
    when `STM32_Programmer_CLI` is not installed, or on request with `--openocd`.
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
- `button` uses one SWD access per edge: the firmware only takes two presses for a double
  press when the second starts less than a second after the first (`HOLD_MSEC` in
  `FlySight/mode.c`), and one access already takes more than a tenth of a second.

### Linux

```bash
sudo apt-get install openocd
sudo sh -c 'printf "%s\n" "SUBSYSTEM==\"usb\", ATTR{idVendor}==\"0483\", ATTR{idProduct}==\"37*\", MODE=\"0660\", GROUP=\"plugdev\", TAG+=\"uaccess\"" > /etc/udev/rules.d/49-stlink.rules && udevadm control --reload-rules && udevadm trigger --subsystem-match=usb --attr-match=idVendor=0483'
```

The second command lets a user who is not root open the ST-Link; without it OpenOCD
fails with `Error: open failed`. To flash a build, without touching the bootloader or the
BLE stack:

```bash
openocd -f interface/stlink.cfg -c "transport select hla_swd" -f target/stm32wbx.cfg -c "program build.elf verify reset exit"
```

## BLE trace (`ble_trace.py`)

The BLE controller can refuse a connection without reporting anything to the
application, so some BLE problems cannot be seen from a normal build. `ble_trace.py`
adds a temporary trace to `app_ble.c` and reads it back over SWD while the firmware
runs:

```bash
python ble_trace.py instrument            # edits app_ble.c and app_conf.h in place
#   ... build and flash as usual ...
python ble_trace.py read --elf build.elf  # the last 64 events
python ble_trace.py mark --elf build.elf  # number of events so far
python ble_trace.py read --elf build.elf --since 18
python ble_trace.py set privacy off --elf build.elf
python ble_trace.py restore               # removes the trace from the sources
```

```
  18 ADV START    PAIRING mode bonded=3 adv_cmd=0x00 | static addr C0:01:02:03:04:05 | controller RPA for peer0 7E:00:00:00:00:27 | peer0 privacy: not set
  21 CONNECTED    status=0 peer=00:11:22:33:44:55 (public) peerRPA=none | our address in this link: 7A:00:00:00:00:4E (controller RPA)
  24 GAP/L2CAP    0x0401 PAIRING_COMPLETE status=0 reason=0x00
  25 ENCRYPTION   status=0x00 enabled=1
  26 DISCONNECTED reason=0x13 (remote user terminated)
```

It records every advertising start (pairing mode or not, addresses in use),
connection (address and address type of the central), disconnection, encryption
change and GAP event. A central whose request the controller drops leaves no
`CONNECTED` line at all.

`set` flips a switch in RAM that turns one fix off or on again, so the same boot
and the same bond can be compared with and without it:

| Switch    | Off means                                                              |
|-----------|------------------------------------------------------------------------|
| `privacy` | bonded peers stay in Network Privacy mode (`ble_count_bonded_devices`) |
| `window`  | `request_pairing` is not cleared in `Adv_Update`                       |

A switch applies from the next advertising start (a `button` double click, or a
connection ending) and a reset turns both back on.

The trace is test code: `instrument` marks everything it adds with `TEMP-BLE-TRACE`
and `restore` gives back the exact original files. Do not commit an instrumented
tree. `--elf` (or `FLYSIGHT_ELF`) must be the ELF that is running, because the
addresses of the trace come from its symbol table. `instrument` also sets
`CFG_DEBUGGER_SUPPORTED` to 1.

Example, a bonded Mac with the first fix off and then on:

```bash
python ble_trace.py set privacy off --elf build.elf
python vbus.py button                     # pairing mode: advertising restarts, fix off
#   ... connect from the Mac: it times out, and `read` shows no CONNECTED line
python ble_trace.py set privacy on --elf build.elf
python vbus.py button
#   ... connect from the Mac: CONNECTED, peer type "public", no peer RPA
```

## Tests

The tools' own tests need no hardware:

```bash
python -m unittest test_vbus test_ble_trace
```

`test_ble_trace` instruments a copy of the real sources, so it fails when a change in
`app_ble.c` breaks one of the places the trace hooks into.
