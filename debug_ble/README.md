# debug_ble

Material from the FlySight 2 BLE investigation done on macOS, Android and iPadOS, put
together to continue the work on Windows and Linux.

| File | What it is |
|------|------------|
| `ble-pairing-fixes-report.html` | The conclusions: the three firmware bugs, their fixes, what each commit changes in behaviour, and the test results. Open it in a browser. Start here. |
| `conversation.md` | The whole session in readable form: every message, every command that was run and its output (long outputs are shortened, screenshots left out). It was exported while the session was still going on, so its last exchanges may be missing. |
| `conversation-raw.zip` | The same session as exported by the Claude desktop app, complete and unshortened. **Not in the repository**: it is ignored by git (`.gitignore` in this folder) because it holds screenshots of personal devices. It exists on the machine where the session ran; copy it by hand if it is needed. |

## Where the code is

- **Firmware**: this repository, branch `fix/ble_pairing_issues`. One commit per fix
  (`app_ble.c` twice, `state.c`), plus the test tools in `Scripts/cli`: `vbus.py` (USB
  plug/unplug and button over the ST-Link) and `ble_trace.py` (what the BLE stack reports
  to the firmware, read over SWD). Both are plain Python and only need
  `STM32_Programmer_CLI`; see `Scripts/cli/README.md`.
- **App**: the SkyGames repository, submodule `Libs/FlySightApi`. The native BLE layer is in
  `core/src/nativeInterop/c/` with one folder per platform (`macos`, `linux`, `windows`).
  At the time of this export its changes were not committed, and the Linux and Windows
  files had been edited but never compiled.

## Nothing has been measured on Windows or Linux yet

What decided the outcome on each platform so far is the address the host connects from
once it is bonded: the Mac uses its identity address and hits bug 1, Android and iPadOS use
a resolvable private address and do not. `ble_trace.py read` shows which one a host uses
(`CONNECTED ... peer=... (public)` against `(public identity (resolved))`), and whether its
connection request reaches the firmware at all.
