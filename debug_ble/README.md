# debug_ble

Material from the FlySight 2 BLE investigation done on macOS, Android and iPadOS, put
together to continue the work on Windows and Linux. Both have been done since: see `linux/`
and `windows/`.

| File | What it is |
|------|------------|
| `ble-pairing-fixes-report.html` | The conclusions: the firmware bugs (three in the first part, three more in the Linux part, two more in the Windows part), their fixes, what each commit changes in behaviour, and the test results. Open it in a browser. Start here. |
| `conversation.md` | The three sessions in readable form, one after the other: macOS, Android and iPadOS first, then Linux (heading "Second session: Linux"), then Windows (heading "Third session: Windows"). Every message, every command that was run and its output (long outputs are shortened, screenshots left out). Each was exported while its session was still going on, so its last exchanges may be missing. |
| `linux/` | The Linux results (`linux/README.md`) and the scripts that produced them: BlueZ alone, SkyGames' native library, and the Kotlin layers of FlySightApi. The scripts for the library and the Kotlin layers are not specific to Linux. |
| `windows/` | The Windows results (`windows/README.md`) and the scripts that produced them: WinRT alone, how often Windows hears a FlySight and how long it takes to reach it, the battery level, and the memory where the BLE stack keeps the bonds. The library and the Kotlin layers are tested there with the scripts of `linux/`. |
| `conversation-raw.zip` | The first session as exported by the Claude desktop app, complete and unshortened. **Not in the repository**: it is ignored by git (`.gitignore` in this folder) because it holds screenshots of personal devices. It exists on the machine where the session ran; copy it by hand if it is needed. |

## Where the code is

- **Firmware**: this repository, branch `fix/ble_pairing_issues`. One commit per fix
  (`app_ble.c` twice, `state.c`), plus the test tools in `Scripts/cli`: `vbus.py` (USB
  plug/unplug and button over the ST-Link) and `ble_trace.py` (what the BLE stack reports
  to the firmware, read over SWD). Both are plain Python and need `STM32_Programmer_CLI` or
  OpenOCD; see `Scripts/cli/README.md`.
- **App**: the SkyGames repository, submodule `Libs/FlySightApi`. The native BLE layer is in
  `core/src/nativeInterop/c/` with one folder per platform (`macos`, `linux`, `windows`).
  The Linux file and the Windows file have been rewritten and tested since (`linux/README.md`,
  `windows/README.md`).

## What decided the outcome on each platform

What decided the outcome on each platform so far is the address the host connects from
once it is bonded: the Mac uses its identity address and hits bug 1, Android and iPadOS use
a resolvable private address and do not. `ble_trace.py read` shows which one a host uses
(`CONNECTED ... peer=... (public)` against `(public identity (resolved))`), and whether its
connection request reaches the firmware at all.

Linux connects from its identity address like the Mac, and still is not affected by bug 1; what
stopped it was on the host side (BlueZ needs a pairing agent, and the native library of the app
was not finished). The details are in `linux/README.md`, with two more firmware defects found on
the way, both in the file transfer and both with a fix committed on the branch: a command dropped
when it comes right behind the end of a file, and a microSD card powered again too soon, which
left the FlySight locked in `Error_Handler()`. A third defect, with a fix committed on the branch, is not about the
file transfer: a reset during the first 150 ms of a start, while `flysight.txt` is being rewritten,
makes the FlySight draw new BLE keys.

Windows connects from its identity address like the Mac, and is affected by bug 1 like the Mac:
once bonded, it connects to no unmodified build, and to every build that has the fix. The rest
is on the host side (`windows/README.md`): the application has to ask Windows for the pairing,
and an idle FlySight is slow to reach because Windows listens little. Two more firmware defects
were found from Windows, neither of them particular to it, each with a fix on the branch: the
battery level a host reads is 0 % until the first active mode and is not measured again outside
it, and the BLE stack has room for the bonds of three hosts only (five on `master`), a fourth
pairing erasing the three others.

## The bench, as the Windows session left it

- **Branches.** Firmware: `fix/ble_pairing_issues`. SkyGames and its submodule FlySightApi:
  `release/1.1.0`.
- **The bench FlySight** runs the build of the branch, the fixes of bugs 7 and 8 included, with
  the trace of `ble_trace.py`. Its BLE keys changed many times during the tests of section 8 of
  the report, and its bonds were cleared many times during the Windows session: it is bonded with
  the Windows PC and with nothing else. The Mac, the Android phone, the iPad and the Linux PC have
  to forget it and pair again. The tests of the modes also added sessions to `TEMP` on its card,
  and sent `Temp_Folder` back to 0000 several times.
- **The mirror builds** of the Windows session (`windows/nvm-mirror.patch`) keep the bonds in RAM
  and forget them at each reset: they are for reading the memory of the BLE stack, not for use.
  None is left on the bench.
- **One stop in `Error_Handler()` is not explained**: once, on the branch without the fix of
  section 8, when active mode started after config mode. And once, during the Windows session,
  the FlySight stopped advertising on `develop` + patch after SkyGames had been connected to it;
  the next build was flashed before its state was read (`windows/README.md`, the four builds).
  If the red LED stays on, or if the FlySight no longer advertises, run `linux/postmortem.py`
  with the ELF that is flashed before any reset or flash.
- **The card of the bench FlySight lists a `FILL.BIN` of 2.07 GB that is not there**, now in the
  trash folder of the card (`.Trash-<uid>/files`). It is what a test left, and its clusters are
  free or belong to other files. Do not empty that trash and do not delete the file from the
  file manager: run `chkdsk /f` on the volume, or format the card. The firmware is not affected.
  The cause and the details are at the end of the section on the state file in
  [linux/README.md](linux/README.md). The card was not mounted on the PC during the Windows
  session: this is still to do.
- **Flashing or resetting while the card is mounted on a PC** loses the last thing the PC wrote:
  the firmware keeps it in RAM until USB mode ends. Unmount, then unplug the cable (or force
  VBUS low from the debugger), then flash.
- **Resets.** A firmware without the fix of section 8, `master` and `develop` included, draws new
  keys when it is reset about 130 ms after a start. From a script, leave a few seconds between
  two resets of such a build.
- **The tools.** `Scripts/cli/vbus.py` and `ble_trace.py` work with `STM32_Programmer_CLI` or
  OpenOCD, on Windows too. `linux/fs_probe.py` is plain `ctypes` and loads the library named by
  `FLYSIGHT_BLE_LIB`; `linux/jvm-probe` runs the Kotlin layers without the application. The other
  scripts of `linux/` talk to BlueZ (`bluez_probe.py`, `scan.py`, `stale_subscriber.py`,
  `crs_back_to_back.py`) or call `openocd` and the `arm-none-eabi` tools (`postmortem.py`,
  `sd_power_gap.py`, `state_rewrite_reset.py`); on Windows `postmortem.py` takes those tools
  from WSL, and the other two were not tried there. The scripts of `windows/` use WinRT.
