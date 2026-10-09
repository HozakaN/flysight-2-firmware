# FlySight 2 BLE on Windows

What was measured on Windows on 9 October 2026, with the same bench FlySight as on macOS and
Linux, and the scripts that did it. The macOS, Android, iPadOS and Linux results are in
`../ble-pairing-fixes-report.html` and `../linux/README.md`.

## Bench

- The bench FlySight 2, on its battery, ST-Link attached (an STLINK-V3), USB cable unplugged.
  Firmware: branch `fix/ble_pairing_issues` with the trace of `Scripts/cli/ble_trace.py`, except
  where another build is named.
- A desktop PC under Windows 11 Pro 25H2 (build 26200.9457), Intel Wireless Bluetooth adapter
  (USB `8087:0AAA`, driver 23.170.0.3). Public address `64:5D:86:**:**:**`.
- The ST-Link is driven through OpenOCD (xPack 0.12.0-7, installed with winget); see
  `Scripts/cli/README.md`, section Windows. The firmware is built in WSL (Ubuntu 24.04,
  `arm-none-eabi-gcc` 13.2.1, the compiler of the Linux session) with `build_variant.sh`.

## Results

### The FlySight seen from Windows alone (`winrt_probe.py`)

No SkyGames code here: the probe scans, opens a link, reads `FT_Packet_In` (which needs an
encrypted link) and closes everything. It uses the WinRT Bluetooth API from Python.

| Situation | Result |
|-----------|--------|
| Address the PC connects from | its public identity address, no resolvable private address (`CONNECTED ... peer=64:5D:86:**:**:** (public) peerRPA=none`), like the Mac and the Linux PC |
| First pairing, nothing asked for, the read left to trigger it | the read is refused at once with ATT error `0x0F` (insufficient encryption), twice, 6 s apart; Windows starts no pairing: the FlySight records the connection and no pairing event |
| First pairing asked for by the program ("confirm only" ceremony, accepted by the program) | paired and bonded in 0.7 s, without any window; read OK (244 bytes) 0.06 s after it, ping acknowledged |
| Bonded, FlySight idle | connects, encrypted read OK, no new pairing |
| Bonded, FlySight in pairing mode | connects, encrypted read OK, no new pairing |
| Same two, with the first fix switched off (`ble_trace.py set privacy off`) | never connects, and the FlySight records no connection at all: **Windows is affected by bug 1**, like the Mac |
| First fix switched on again, same boot and same bond | connects in both |

Bug 1 on Windows is the Mac's case: the PC connects from its identity address, and the
controller of the FlySight ignores it once the PC is in its resolving list. On the PC the link is
reported up for an instant, then down: the request was sent and nothing answered. With the fix
off, the two tries (pairing mode, then idle) gave no `CONNECTED` line on the FlySight; with it
on, every try gave one.

What a Windows host needs in order to pair is on its own side, as on Linux, and it is not the
same thing: the pairing has to be asked for by the application. Windows does not start one when
a read is refused.

Three more things Windows does, measured with the same probe:

- **The identifier of a bonded FlySight does not change.** A FlySight advertises from a
  resolvable private address that changes at every advertising start. Windows files the bond
  under the address the FlySight had when it was paired, and from then on reports its
  advertisements under that same address, whatever address it really uses, after a reset of the
  FlySight too. An application can keep that address as the identifier of the device.
  Removing the FlySight on Windows and pairing it again gives it a new one.
- **There is no way to end a link.** Closing what holds it ends it 3.1 s later (3.07 to 3.12 s
  over six links; the FlySight records `DISCONNECTED reason=0x13`).
- **Windows reads the Battery Service of a paired device by itself**, and told the user the
  battery of the FlySight was low. The FlySight was answering 0 %: see "Seen on the way" below.

### How long Windows takes to reach a bonded FlySight (`hear_rate.py`, `connect_timing.py`)

A FlySight that is idle advertises every 80 to 100 ms for 30 s after an advertising start, then
every 1 to 2.5 s as far as the firmware goes; the bench one does it every 1.75 s. Reaching it
then takes Windows longer than the other hosts, for two reasons.

**Windows listens little.** An advertisement watcher makes it listen 18 ms every 118 ms: those
are the scan parameters WinRT reports, and it offers nothing faster. Windows listens several
times more while the Bluetooth LE devices around are being enumerated, which is what its
Bluetooth settings do when the user adds a device. Over 120 s (`hear_rate.py`):

| What runs | The FlySight is heard |
|-----------|-----------------------|
| a watcher, active scanning | 14 times: every 8.7 s on average, longest silence 22.8 s |
| a watcher, passive scanning | 14 times: every 8.0 s on average, longest silence 12.3 s |
| a watcher, and an enumeration of the Bluetooth LE devices | 52 times: every 2.3 s on average, longest silence 7.0 s |

**Windows reaches a device at once only when it has just heard it, and reaches it sooner when
nothing listens any more.** Each line below is one connection (`connect_timing.py`), from the
start of the program to the services read:

| What the program does, FlySight advertising every 1.75 s | Time until the services are read |
|-----------------------------------------------------------|----------------------------------|
| asks for the link at once, no scan | 23.8 s, 29.6 s |
| starts a scan and asks for the link at once | 9.8 s, 24.8 s |
| scans until the FlySight is heard, with the scan left running until the link is up | 6.3 s, 13.3 s, 16.1 s, 16.9 s |
| scans until the FlySight is heard, stops the scan, then asks | 4.6 s, 5.6 s, 16.8 s, 29.2 s |
| the same with the enumeration running during the scan | 3.5 to 12.6 s over 16 connections, half of them under 5.7 s |

In the third and fourth rows the time goes into waiting to hear the FlySight (1.6 to 20.8 s);
the enumeration brings that down to 0.2 to 3.5 s. Once the FlySight is heard and nothing listens,
the link is up at its next advertisement, 1.75 s later, in 9 connections of 16, and at the
second to the fifth in the others. A request for the services that finds nothing comes back after
7.8 s, and nothing else can be asked of the device meanwhile.

The last row is what the native library of SkyGames does. Its first version did the third: see
the series in the next section.

With the FlySight advertising every 80 to 100 ms, the link is up 0.2 s after it is asked for
when a scan has just heard the device, and 7.9 s after when no scan ran.

### The native library of SkyGames (`../linux/fs_probe.py`)

`Libs/FlySightApi/core/src/nativeInterop/c/windows/ble_windows.cpp` did not compile, and could
not have worked: six functions of the header were missing, nothing opened a link (it waited for
Windows to say "connected", which Windows only says once something is asked of the device),
nothing paired, and the DLL exported no function. It was rewritten; see
`WINDOWS_IMPLEMENTATION.md` next to it. The probe is the one of the Linux session, unchanged: it
loads the DLL named by `FLYSIGHT_BLE_LIB` and calls it as the app does.

| Situation | Result |
|-----------|--------|
| Scan | the three FlySights in range within 2 s, idle ones included, with their pairing flag; the bonded one under its identifier |
| First pairing (FlySight in pairing mode, bond removed on the PC first) | connected and bonded in 3.0 s, encrypted read OK, ping acknowledged; the FlySight records `PAIRING_COMPLETE status=0` |
| Bonded, FlySight idle, after a scan | connected in 1.7 s, read OK, ping acknowledged |
| Bonded, FlySight in pairing mode, after a scan | connected in 1.8 s, read OK, ping acknowledged |
| New process, saved identifier, no scan (what the app does when it starts), less than 30 s after a link ended | connected in 1.6 s |
| The same with the FlySight advertising every 1.75 s, 16 times | connected each time, in 3.3 to 15.6 s, half of them under 6 s; 10 more connections made later took 4.5 to 18.8 s |
| Identifier never seen | refused at once (`BLE_ERROR_INVALID_PARAM`) |
| 40 s connected, ping every 14 s | link kept, every ping acknowledged |
| No ping | disconnect callback 30.2 s after the last write (the FlySight's timeout) |
| FlySight reset while connected | disconnect callback 9.5 to 9.8 s after the reset: the link Windows opens has a supervision timeout of 9.6 s (interval 60 ms, latency 0) |
| Reconnection after that reset | connected in 1.7 s |
| A second connection asked for less than 3 s after `ble_disconnect()`, from another process | connected in 2.1 s over the link of the first: the FlySight records one connection and one disconnection |
| A connection 0, 2, 3, 4, 5 or 7 s after the previous one let go of the link, 3 times each | connected in 1.5 to 2.2 s, 18 of 18 |
| The Kotlin layers, 25 connections made 3 to 5 s after a disconnection | 23 in 1.5 to 2.3 s, 2 in 10 s: Windows still said it held a link that had ended, and the first request was lost |
| A FlySight this PC is not bonded with, idle | connect callback with `BLE_ERROR_CONNECT_FAILED` after 3.8 s, 7.4 s and 14.4 s (three tries) |
| An identifier whose bond has been removed on the PC, the FlySight paired again since under another one | refused after 12.1 s (`BLE_ERROR_CONNECT_FAILED`, "is not advertising, and Windows is not paired with it"), twice |
| The FlySight has forgotten its bonds, the PC has not; FlySight idle, then in pairing mode | no connection: gives up at its limit of 30 s (`BLE_ERROR_TIMEOUT`) both times, and the FlySight records no connection |

The first version of the library kept its scan running while it asked for the link, without the
enumeration. With the FlySight advertising every 1.75 s it connected in 2.1 to 22.6 s over 8
connections, and gave up once at its limit of 30 s.

Two cases of that table say what happens when the two sides no longer agree on the bond:

- **The identifier is one of before.** Windows still reaches the FlySight through an address it
  had under an earlier pairing, as a device it is not paired with: asked for it, it brought the
  link up in 5.6 s, the library then asked for a pairing, as for any device that is not paired,
  and that pairing was never answered. The library no longer asks for a device that is not
  paired and does not advertise: it refuses the identifier after listening for 12 s.
- **The FlySight has forgotten its bonds** (`forget-bonds.patch`: a build that clears them at
  each start). With no bond the FlySight advertises from its static address, which it draws
  at each start, and no longer from an address its key gives: Windows has nothing to recognise
  it by, and the bonded device stays out of reach, in pairing mode too. A scan shows the
  FlySight under that static address, as a device Windows does not know: the first pairing of
  "Bug 1 from a FlySight without any bond", below, was made that way. The library cannot tell
  that case from a FlySight that is not there, and never reports `BLE_ERROR_PAIRING_REMOVED`.

### The Kotlin layers of FlySightApi (`../linux/jvm-probe/`)

The JVM program of the Linux session, unchanged: it uses `FlySightApi` as SkyGames does (scan
or saved identifier, `createDevice`, `connect`, then what the library reads by itself:
`FLYSIGHT.TXT`, `CONFIG.TXT`, mode, battery). It loads the DLL through JNA, as the application
does.

| Situation | Result |
|-----------|--------|
| First pairing (FlySight in pairing mode, bond removed on the PC first) | connected and bonded in 3.5 s, both files read |
| Scan, bonded FlySight | found under the saved identifier, connected in 1.5 s, both files read |
| Saved identifier, FlySight idle, 40 s | both files read, still connected at 40 s |
| Saved identifier, FlySight in pairing mode | connected in 1.5 to 1.9 s, both files read (3 of 3) |
| Saved identifier, FlySight idle, 12 connections in a row, 4 s apart | 12 of 12 clean: both files whole each time, no request sent again; connected in 1.5 to 2.8 s, and once in 9.4 s |

Nothing had to change in the Kotlin code: what runs here is the code of the Linux session, with
its four fixes.

### The four builds of the report, and the branch

`master` `9ec7186` and `develop` `e67ee9f` as published upstream, and each with the three fixes
of the report and nothing else, apart from `CFG_DEBUGGER_SUPPORTED` set to 1 for the bench. They
were built again here with `build_variant.sh`. The PC was bonded with the board beforehand, and
the FlySight saved in SkyGames. For each build: the native library and the Kotlin layers connect
with the FlySight idle and in pairing mode, SkyGames does the same from its window, then a
pairing window is left to expire (double press, `pairing_window.py` for 262 s). The last row is
the branch as it is, without the trace. The times are those of each connection.

| Firmware | Library, idle and pairing mode | Kotlin layers, idle and pairing mode | SkyGames, idle and pairing mode | Pairing window left to expire |
|----------|--------------------------------|--------------------------------------|---------------------------------|-------------------------------|
| `master`, unmodified | does not connect (told after 1.2 s and 1.3 s) | does not connect (1.2 s, 10.0 s) | does not connect: back to its Connect button after 11.6 s and 1.9 s | `01` for 29 s, then `00`, still advertising at 254 s |
| `master` + patch | connects in 3.3 s and 1.6 s, encrypted read OK | connects in 2.5 s and 1.6 s, both files read | connects in 4.0 s and 2.2 s, configuration read | `01` for 30 s, then `00`, still advertising at 236 s |
| `develop`, unmodified | does not connect (1.7 s, 1.1 s) | does not connect (1.8 s, 13.6 s) | does not connect: back to its Connect button after 18.8 s and 1.5 s | `01` for 204 s, then no advertising (bug 2) |
| `develop` + patch | connects in 3.7 s and 1.4 s, encrypted read OK | connects in 2.0 s and 1.9 s, both files read | connects in 5.8 s and 1.6 s, configuration read | `01` for 29 s, then `00`, still advertising at 246 s |
| branch | connects in 1.6 s and 1.7 s, encrypted read OK | connects in 1.7 s and 1.8 s, both files read | connects in 14.7 s and 1.8 s, configuration read | `01` for 29 s, then `00`, still advertising at 246 s |

Windows does not connect to the two unmodified builds, idle or in pairing mode: bug 1 concerns
it, as it concerns the Mac. The patch is enough for it to connect. Bug 2 shows from Windows as
from the other hosts, and the patch removes it.

On the unmodified builds the library says "does not accept the connection" the second time
Windows reports the link up and takes it back. How long that takes is up to Windows: 1 to 19 s
here. SkyGames then shows its Connect button again, with no message.

When SkyGames connects with the FlySight idle, the FlySight has been advertising every 1.75 s
for a while: 4.0, 5.8 and 14.7 s are in the range measured above for that case. In the other
columns the FlySight had just been restarted or had just left a link, and advertised fast.

This table is one pass: the five builds one after the other, each flashed once, nothing run
again. The pairing window is timed by a scanner on the PC, and Windows hears an idle FlySight
every 10 to 13 s: the first advertisement with the flag back to `00` was heard 37 to
39 s after the double press, and "still advertising at" is the last one heard before the 262 s
were over.

The library has had two changes since that pass, both described above: it refuses an
identifier Windows is not paired with and that does not advertise, and it listens for 1.5 s
before it asks when Windows says it already holds the link. The rows of the library and of the
Kotlin layers that are about those two cases, and a last run of the other cases (a FlySight the
PC is not bonded with, a FlySight advertising slowly, pairing mode, scan), were made with the
library as it is.

Two passes came before it:

- The first one had the first version of the library, and no SkyGames. It gave the same results
  for the library, the Kotlin layers and the pairing windows.
- The second one was the first with SkyGames in it. Several steps of SkyGames were not carried
  out: its window had stopped being the active one, and it then ignores what is posted to it. On
  `develop` + patch, the scanner of the pairing window heard nothing at all from the FlySight
  for 262 s after the SkyGames steps, and the board was flashed with the next build before its
  state was read. Since then the pass checks that the FlySight advertises, after the Kotlin
  layers and after SkyGames, and reads its state with `../linux/postmortem.py` if it does not.
  It advertised every time in the pass of the table.

### Bug 1 from a FlySight without any bond

The sequence of the report (first pairing, then reconnection) from a FlySight whose bonds had
been cleared first, on unmodified `master`, with the library:

| Step | Result |
|------|--------|
| First pairing, FlySight in pairing mode | connected and paired in 3.5 s, encrypted read OK, ping acknowledged |
| Reconnection 6 s later, FlySight idle | does not connect (told after 1.8 s) |
| Reconnection, FlySight in pairing mode | does not connect (9.4 s) |
| FlySight removed on the PC, then paired again, FlySight in pairing mode | does not connect (1.2 s); with WinRT alone, Windows reports the link up and down six times in 4.5 s |
| Then the branch build with the trace, FlySight in pairing mode | connected and paired in 3.9 s; the FlySight records `CONNECTED ... (public) peerRPA=none`, `ENCRYPTION`, `PAIRING_COMPLETE status=0` |

Windows filed that first bond under the static address of the FlySight, not under a resolvable
private one: a FlySight without any bond advertises from its static address.

### The SkyGames application itself

SkyGames had not been built on Windows before. Apart from the BLE library, three things stopped
it, none of them about BLE, all dealt with in the SkyGames repository (`docs/native-libraries.md`
there, section "Building on Windows"):

- The text-to-speech library (`AudioModule`, eSpeak-ng) had no build script for Windows, and
  like the BLE library its DLL exported nothing.
- The Firebase library (`UserModule`) did not link: the CMake script of the Firebase C++ SDK
  looked for its Windows libraries in a folder that does not exist unless it is told the build
  type and the C runtime.
- Gradle could not start: the JetBrains JDK 21 it is told to download for its daemon is no
  longer at the addresses the project gives. The JDK was downloaded by hand and Gradle told
  where it is.

Signing in with Google works: the browser opens, and the application is signed in when it comes
back.

Then, in the window of SkyGames, signed in, on the branch build with the trace, with the bond
removed on the PC first:

| Situation | Result |
|-----------|--------|
| "Add a device", FlySight in pairing mode | the list shows the bench FlySight and another one within 3 s; the bench one connects and pairs in 3.3 s (`PAIRING_COMPLETE status=0` on the FlySight), no window of Windows is shown, and its configuration is displayed |
| Disconnect, then Connect, FlySight idle and advertising every 1.75 s | connected in 3.6 s, configuration displayed |
| Disconnect, FlySight put back in pairing mode, Connect | connected in 1.7 s, configuration displayed: the sequence the investigation started from |
| Application closed and started again | still signed in; reconnects by itself to the saved FlySight in 1.8 s, configuration displayed |

With the library as it is now, at the end of the session: the entry of the FlySight removed in
SkyGames (its identifier was no longer the one of the bond), "Add a device" again with the PC
already bonded (the three FlySights in range listed within 3 s, connected in 1.6 s, configuration
displayed), then Disconnect and, 40 s later, Connect (connected in 4.6 s).

The window was driven without the mouse and the keyboard of the PC, which stayed with whoever
was using it: clicks and keys were posted to the window, and it was told it was the active one
when it was not. For most of the session the clicks were posted 23 pixels too high, the height
of the title bar: they opened the tiles and the lists, which are large, and missed the buttons
of the dialog of a device. So in the passes over the builds, Connect and Disconnect were pressed
with Tab and Enter. With the height corrected, posted clicks press those buttons as well, which
is how the last line above was done. Each time its window becomes active, the application checks
the account again and shows another screen for a moment; nothing was posted while it showed.

## Observations with no change proposed

- **An idle FlySight is slow to reach from Windows**, for the two reasons measured above: Windows
  listens 15 % of the time, and the firmware advertises every 1 to 2.5 s once 30 s have passed
  since the last advertising start (`CFG_LP_CONN_ADV_INTERVAL_MIN` and `_MAX` in
  `Core/Inc/app_conf.h`). An application that only asks for the link waits 20 to 30 s. The
  library of SkyGames gets it in 3 to 19 s by making Windows listen more. A double press on the
  button makes the FlySight advertise every 80 to 100 ms for 30 s, and the link is up in 2 s.
- **Windows does not connect by itself to a FlySight it is paired with.** Over 300 s with no
  application asking for it, the FlySight recorded no connection.
- **The identifier Windows gives a bonded FlySight is the address the FlySight had when it was
  paired.** Five pairings of the same FlySight gave five identifiers. An application that saved
  one finds nothing under it once the FlySight has been removed on Windows and paired again.

## Bug 7: the battery level (`battery_level.py`)

Windows showed a "low battery" notification for the FlySight, with its battery at 4.09 V. Report,
section 12.

`develop` and the branch have a Battery Service (`0x180F`); `master` has none: on unmodified
`master` and on `master` + patch, `battery_level.py --first` finds five services and no Battery
Level. The level a host reads is `current_battery_level_percent` in
`STM32_WPAN/App/custom_app.c`, which starts at 0 and is only changed by `Custom_VBAT_Update()`,
called from `FS_VBAT_ValueReady_Callback()` in `FlySight/active_control.c` in active mode and in
no other: the converter and the timer of `vbat.c` only run there.

`battery_level.py --watch 40` holds one link, reads the level every two seconds and prints it
when it changes, while the button takes the FlySight into active mode and back (a press held
1.5 s, twice). The mode, the level the firmware holds and the last voltage it measured are read
over SWD at four moments. On `develop` the PC cannot connect a second time (bug 1): its lines
were read over the link of a first pairing (`--first`), from a FlySight whose bonds had been
cleared.

| Build | Just started, sleep mode | Active mode | Back in sleep mode | In the firmware at the end |
|---|---|---|---|---|
| `develop` unmodified | 0 % | 83 to 87 % | 84 %, for 20 s | level 84, last voltage 4064 mV, measured in active mode |
| `develop` unmodified, an earlier run | 0 % | 84 to 88 % | 87 %, for 17 s | level 87, 4085 mV |
| `develop` + fix | 88 % | 84 to 86 % | 88 % | level 88; 4080 mV is the last voltage of active mode |
| branch before the fix | 0 % | 85 to 89 % | 86 %, for 25 s | level 86, 4079 mV |
| branch + fix | 88 % | 83 to 86 % | 88 % | level 88; 4079 mV is the last voltage of active mode |

The fix measures the battery when a host reads the level or subscribes to it outside active mode
(`FS_VBAT_Measure()` in `vbat.c`: converter on, calibration, one conversion, converter off), and
gives the level to the stack once a link is encrypted. `vbatData`, which the SWD column reads,
only holds the measurements of active mode: after the fix it still shows the last voltage of
the last active mode, which is why the level and the voltage of that column no longer match.

Twenty reads of the level, FlySight in sleep mode, were answered in 65 to 116 ms without the fix
and in 66 to 132 ms with it. Over a link during which the FlySight enters and leaves active mode,
the longest of twenty reads took 215 ms on unmodified `develop`, 280 ms on `develop` + fix and
936 ms on the branch + fix.

**What Windows holds.** Windows keeps a battery level for a paired device. It is a property of the
device node, with the time it was written:

```powershell
$d = Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -eq 'FlySight' -and $_.InstanceId -like 'BTHLE\DEV_*' }
Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2'   # the level
Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 7'   # when it was written
```

At a pairing Windows subscribes to the notifications of the Battery Level by itself: the record
the stack keeps for the PC has that descriptor set (`002c=1` in `nvm_dump.py`), with programs that
never write it. The property is written a few seconds after the pairing.

| What was done, FlySight in sleep mode | Level held by Windows |
|---|---|
| Pairing with the branch before the fix, FlySight just started | 0 % |
| A later connection, same firmware | 0 %, not written again |
| That PC, branch with the first change of the fix only (measure on a read), a connection during which the FlySight went into active mode and back | 0 %, not written again |
| That PC, branch + fix, its next connection | 88 % |
| Pairing with the branch + fix, FlySight just started | 88 % |
| A PC bonded with unmodified `develop`, which held 84 % from an active mode: `develop` + patch + fix, its next connection | 88 % |

The third line is why the fix has a second change. At a later connection Windows neither reads
the level nor subscribes again: it waits for a notification. The firmware only notifies a host
that subscribed during the current link (`Custom_CRS_OnDisconnect()` clears
`Battery_level_Notification_Status`), while the stack gives a bonded host its subscription back
without telling the firmware. `aci_gatt_update_char_value()` goes by what the stack holds: called
once the link is encrypted (`Custom_VBAT_Notify()`), it sends the level to such a host.

When a program reads the level, the firmware updates the characteristic before it answers, and
the stack notifies Windows as well: the property then follows what the program reads. The lines
above were measured with no program reading the battery.

Not looked at: a low battery, a battery on charge, `Enable_Vbat: 0`, and what macOS, Linux, iOS
and Android do with the level.

## Bug 8: the bonds the stack can hold (`nvm_dump.py`, `nvm-mirror.patch`, `gatt_cache.py`)

At the first pairing of this PC the FlySight held four bonds, and one afterwards. Report,
section 13. The trace of that pairing:

```
   8 ADV START    PAIRING mode bonded=4 ...
   9 CONNECTED    status=0 peer=64:5D:86:**:**:** (public) ...
  12 ENCRYPTION   status=0x00 enabled=1
  13 GAP/L2CAP    0x0006 FW_ERROR type=0x03 (NVM level warning) data=00
  14 GAP/L2CAP    0x0401 PAIRING_COMPLETE status=0 reason=0x00
  16 DISCONNECTED reason=0x13 (remote user terminated)
  17 ADV START    idle         bonded=1 ...
```

### What ST says

- The stack keeps the bonds in a memory of its own: 2028 bytes (`BLE_NVM_SRAM_SIZE`, 507 words,
  in `shci.h`), a size the application does not choose.
- The release notes of the coprocessor binaries (STM32CubeWB,
  `Projects/STM32WB_Copro_Wireless_Binaries/STM32WB5x/Release_Notes.html`), V1.1.0: the behaviour
  of the BLE NVM when it is full was changed, to inform the application before the latest record
  and to erase and keep the latest record.
- An answer of ST on its forum ("Number of bonding/bonded devices and validation", June 2024): when
  the NVM is full only the last bond is kept, and a warning event normally comes first.
- The wiki page "STM32WB-WBA GATT Data Base and bonded devices information storage" gives the
  number of hosts as (2028 - 1) / ((80 + 1) + (GATT record + 1)), and counts 5 bytes per
  characteristic with a 16-bit UUID in its examples. Read in the memory, the stack writes one
  entry per attribute, the declaration of each characteristic included, and each record has a
  header of 4 bytes, not 1. For the database of `develop` the examples of that page lead to a
  record of 286 bytes and to 5 hosts; the record is 420 bytes and 3 hosts fit.

The board runs version 1.19.0 of the stack (`Stack_Ver` in `flysight.txt`, and the table the
coprocessor publishes at `0x20030028`: `01130002`).

### Reading the memory of the stack

The stack keeps that memory in the flash of CPU2, which a debugger cannot read. `nvm-mirror.patch`
makes it keep it in a buffer of RAM instead, with the option ST provides
(`SHCI_C2_CONFIG_CONFIG1_BIT0_BLE_NVM_DATA_TO_SRAM` in `SHCI_C2_Config()`); `nvm_dump.py` reads the
buffer over SWD while the firmware runs, and decodes it:

```
   0  security   80 + 4 bytes  state 1  device 64:5D:86:**:**:**
  84  GATT      420 + 4 bytes  state 1  device 64:5D:86:**:**:**  full: 44 attributes (12 with a 128-bit UUID), descriptors set: 0004=2 002c=1
508 of 2028 bytes used, 1520 free; 1 device(s) with a security record in state 1
```

The memory is a list of records. Each has a header of 4 bytes (length of the data on 2 bytes, kind,
state) and its data, padded to a multiple of 4 bytes.

- **Security record**, kind 0: 80 bytes. The address of the host is at offset 62. It holds the
  keys: `nvm_dump.py` does not print them, `--raw` does.
- **GATT record**, kind 1: one byte, the address of the host, one byte, then the length of what
  follows on 2 bytes and 2 more bytes. With `SHCI_C2_BLE_INIT_OPTIONS_FULL_GATTDB_NVM`: one entry
  per attribute of the database, which is its handle (2 bytes), the kind of its UUID (1 byte), the
  UUID (2 or 16 bytes) and, for a Client Characteristic Configuration descriptor, its value
  (2 bytes). With `SHCI_C2_BLE_INIT_OPTIONS_REDUC_GATTDB_NVM`: a hash of the database (16 bytes),
  then 3 bytes per descriptor, its handle and its value.

`0004=2` is the indication of Service Changed, `002c=1` the notification of the Battery Level:
the two subscriptions Windows makes by itself.

SRAM2, where ST asks for the buffer to be, is erased by a reset on this board: the option bit
`SRAM2RST` is 0 (`FLASH_OPTR` reads `39fff1aa`), and a word written there is read back as 0 right
after `reset halt`. With the mirror build a reset therefore forgets every bond.
`nvm_dump.py --restore FILE` resets the board, stops it where the firmware is about to give the
buffer to the stack (a breakpoint on `SHCI_C2_Config`), writes a saved memory there and lets it
go: the stack starts with those bonds (`bonded=` in the trace says so).

### One PC for several hosts

A bond is made by a pairing, and a PC only has one address. `nvm_dump.py --rename OLD NEW` does
the same as `--restore` with the address of a host replaced by another one in its two records.
After each pairing the address of the PC was replaced by a made-up one (`C0:00:00:00:00:01` and
so on) and the PC forgot the FlySight: to the stack the PC was then a host it did not know, and
the records it had written stayed as another host's. The stack listed those hosts as bonded
after each reset.

Pairing the same host again does not fill the memory. A second pairing of the PC marked its
security record (state 0) and wrote a new one (592 bytes used); a third one left the memory as
the second had.

### How many hosts fit

Hosts pairing one after the other, the memory read after each. "+ patch" is the patch of the
report, "+ fix" the reduced record and nothing else. The builds of `master` and `develop` have no
trace: the number of bonds is the number of security records in state 1.

| Build | One host | Bytes used after each host | What the next pairing leaves |
|---|---|---|---|
| `master` unmodified | 372 bytes (GATT record 284 + 4: 30 attributes, 8 with a 128-bit UUID) | 372, 744, 1116, 1488, 1860 | the 6th: 372 bytes, 1 bond |
| `master` + patch | 372 bytes | the same | the 6th: 1 bond |
| `develop` unmodified | 508 bytes (GATT record 420 + 4: 44 attributes, 12 with a 128-bit UUID) | 508, 1016, 1524 | the 4th: 508 bytes, 1 bond |
| `develop` + patch | 508 bytes | the same | the 4th: 1 bond |
| branch | 508 bytes | the same | the 4th: 1 bond |
| `master` + fix | 132 bytes (GATT record 43 + 4, padded: a hash and 5 descriptors) | 132, 264, ... 1980 for 15 hosts | the 16th: 132 bytes, 1 bond |
| `develop` + fix | 144 bytes (GATT record 55 + 4, padded: a hash and 9 descriptors) | 144, 288, ... 2016 for 14 hosts | the 15th: 144 bytes, 1 bond |
| branch + fix | 144 bytes (GATT record 55 + 4, padded: a hash and 9 descriptors) | 144, 288, ... 2016 for 14 hosts | the 15th: 144 bytes, 1 bond |

**When the others are erased.** On the branch, with three hosts held, the memory was read every
two seconds while a fourth paired and stayed connected for 20 s: 1608 bytes used and four
security records during the whole link, 508 bytes and one bond once the link had ended. The
security record of a host is written when it pairs, its GATT record when its link ends; the
stack erases the other bonds when that record does not fit (424 bytes needed, 420 free).

**The pairing as it was found**, reproduced. One host bonded by `master` (its memory, saved, put
back in the mirror build of the branch) and three by the branch: 1896 bytes, `bonded=4`. The
fifth pairing gives the seven lines of the trace above, warning included, and leaves `bonded=1`.

**The warning of the stack**, `ACI_HAL_FW_ERROR_EVENT` of type 3. `ble_trace.py` decodes it; the
firmware has no case for it. On the branch, where the trace records it:

| Record just written | Bytes left free | Warning |
|---|---|---|
| security record of host 4, three hosts of 508 bytes held | 420 | none: the three hosts are erased without one |
| GATT record of host 13, reduced records | 156 | none |
| GATT record of host 4, mixed memory above | 132 | `data=01` |
| security record of host 14, reduced records | 72 | `data=00` |
| security record of host 5, mixed memory above | 48 | `data=00` |
| GATT record of host 14, reduced records | 12 | `data=01` |

### What an erased host sees

The PC paired, then its address was replaced in the memory of the stack (`--rename`) and the PC
was not told: the FlySight held two bonds, neither of them the PC's, and the PC still held its
keys.

| | WinRT alone (`winrt_probe.py connect --no-scan`) | Library (`fs_probe.py connect`) | In the trace of the firmware |
|---|---|---|---|
| FlySight idle | no connection: services unreachable, link reported up for 0.36 s after 8 s | "does not accept the connection" after 1.6 s | nothing: outside pairing mode the FlySight accepts bonded hosts only |
| FlySight in pairing mode | no connection: link up for 0.34 s | "does not accept the connection" after 1.6 s | `CONNECTED`, then `DISCONNECTED reason=0x13 (remote user terminated)` |
| The PC removes the FlySight and pairs again, pairing mode | paired in 0.75 s, encrypted read OK | | `CONNECTED`, `ENCRYPTION`, `PAIRING_COMPLETE status=0`, `bonded=3` |

### The reduced record with a real bond

On builds without the mirror, the bond kept by the stack in its flash (`reduc_real` in the logs).

- **A bonded FlySight that gets the fix.** The PC pairs with the branch (full record). The branch
  with the reduced record is flashed: `bonded=1`, and the PC connects twice without pairing again,
  encrypted read and ping answered. The branch without the fix is flashed back: `bonded=1`, and
  the PC connects.
- **A firmware with more services** (`gatt_cache.py`). Windows keeps the services of a bonded
  device in a cache. The script lists it, holds a link for 8 s without asking for anything, and
  lists it again. With the same firmware on both sides of the link the cache does not change.
  The PC pairs with `master` + patch (five services), then the branch (seven) is flashed:

  | GATT record | Cache before the link | Cache after 8 s of link |
  |---|---|---|
  | full (`master` + patch, then the branch) | 5 services | 7 services |
  | reduced (the same two with the fix) | 5 services | 7 services |

- **A subscription kept in the reduced record.** The PC paired with the branch + reduced record,
  which has not the fix of bug 7: Windows subscribed to the Battery Level and held 0 %. The
  branch with the two fixes was flashed: after the next connection of the PC, Windows held 88 %.
  The stack had given the subscription back from its reduced record, and notified the level.
- **The pass of the report**, from a FlySight without any bond (`pass_reduc` and `pass_next` in
  the logs):

  | | Branch + reduced record | Branch + the two fixes |
  |---|---|---|
  | First pairing | WinRT alone, 0.72 s | SkyGames adds the FlySight: connected and paired in 3.3 s, both files read |
  | Library, FlySight idle | 1.77 s, encrypted read OK, ping acknowledged | 1.64 s, the same |
  | Kotlin layers, FlySight idle | 1.6 s, both files read | 2.1 s, both files read |
  | Library, pairing mode | 1.56 s, encrypted read OK, ping acknowledged | 1.75 s, the same |
  | Kotlin layers, pairing mode | 2.0 s, both files read | 1.7 s, both files read |
  | SkyGames, FlySight idle | not run | connects in 14.7 s, configuration read 18.6 s after Connect |
  | SkyGames, pairing mode | not run | connects in 1.5 s, configuration read 5.6 s after Connect |
  | Pairing window left to expire | `01` for 29 s, then `00`, still advertising at 233 s | `01` for 29 s, then `00`, still advertising at 247 s |

### Fourteen bonds held

On the mirror build of the branch with the two fixes, the memory of thirteen hosts put back and
the PC pairing as the fourteenth: the stack sends its warning twice (`data=00` when the PC pairs,
`data=01` when its link ends), `bonded=14`, 2016 bytes used. The PC then connects again with the
FlySight idle and in pairing mode, encrypted read and ping answered both times; `bonded=14` and
the memory do not change.

### Not done

- No second host paired: every bond but the last was the PC's, renamed.
- The series were made on mirror builds. With the memory in flash the erasure was seen once, at
  the pairing that found it.
- What an erased host sees was measured from Windows only.
- `flysight-2-firmware.ioc` was not changed: `app_conf.h` is generated from it, and the line of
  `CFG_BLE_OPTIONS` is outside the user sections.
- Nothing was tried to keep the FlySight from erasing its bonds when the place runs out, with
  the reduced record either: the fifteenth host erases the fourteen others.

## Running the scripts

They need Python 3 and the WinRT projections for Python:

```bat
python -m venv venv
venv\Scripts\pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Storage.Streams winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Bluetooth.Advertisement winrt-Windows.Devices.Bluetooth.GenericAttributeProfile
```

| Command | What it does |
|---------|--------------|
| `python scan.py 20` | every FlySight advert for 20 s: address, pairing flag, paired or not |
| `python winrt_probe.py status` | what Windows is paired with, and under which identifier |
| `python winrt_probe.py connect --flag 01` | WinRT alone, first pairing left to the read: refused, no pairing (FlySight in pairing mode) |
| `python winrt_probe.py connect --flag 01 --pair custom` | WinRT alone, first pairing asked for |
| `python winrt_probe.py connect --flag 00 --ping` | WinRT alone: bonded, FlySight idle |
| `python winrt_probe.py forget` | removes the bond on the PC |
| `python hear_rate.py --address AA:BB:CC:DD:EE:FF` | how often Windows hears that FlySight; `--with-enumeration`, `--passive` |
| `python connect_timing.py --scan before --enumerate` | time to reach the bonded FlySight, the way the library does; the other ways are in its header |
| `python pairing_window.py --address AA:BB:CC:DD:EE:FF` | then a double press: how the pairing window ends |
| `python battery_level.py --watch 40` | the Battery Level over one link, read every 2 s; `--first` pairs first (FlySight in pairing mode) |
| `python gatt_cache.py` | the services Windows holds in its cache for the FlySight, before and after a link during which nothing is asked |
| `python nvm_dump.py --elf build.elf` | with a mirror build: the bonds and the records in the memory of the BLE stack; `--save`, `--restore`, `--rename` |

`AA:BB:CC:DD:EE:FF` is the identifier of the bonded FlySight, which `winrt_probe.py status`
prints.

The library and the Kotlin layers are tested with the scripts of the Linux session, unchanged.
With `%SKYGAMES%` standing for the SkyGames checkout:

```bat
cd %SKYGAMES%\Libs\FlySightApi\core\src\nativeInterop
build.bat
set FLYSIGHT_BLE_LIB=%SKYGAMES%\Libs\FlySightApi\core\src\nativeInterop\build\windows\flysight_ble.dll
set FLYSIGHT_BLE_DEBUG=1
```

| Command, from `debug_ble\linux` | What it does |
|---------------------------------|--------------|
| `python fs_probe.py scan` | the scan of the library |
| `python fs_probe.py connect --flag 01` | first pairing through the library (FlySight in pairing mode) |
| `python fs_probe.py connect --id AA:BB:CC:DD:EE:FF --stay 40` | saved identifier, no scan, 40 s connected |
| `%SKYGAMES%\gradlew.bat -p jvm-probe installDist` | builds the program that runs the Kotlin layers; needs `ANDROID_HOME`, as the build of SkyGames does |
| `jvm-probe\build\install\fs-jvm-probe\bin\fs-jvm-probe.bat FlySight` | Kotlin layers: scan, connect, read |
| `jvm-probe\build\install\fs-jvm-probe\bin\fs-jvm-probe.bat FlySight AA:BB:CC:DD:EE:FF 40` | Kotlin layers: saved identifier, stay 40 s |

`FLYSIGHT_BLE_DEBUG` makes the library print the steps of each connection. `jvm-probe` takes the
DLL that its build has just compiled and packaged, not the one `FLYSIGHT_BLE_LIB` names: run
`installDist` again after a change to the library.

With the bench (`Scripts/cli/README.md`, section Windows, for OpenOCD):
`python ..\..\Scripts\cli\vbus.py button` puts the FlySight in pairing mode, and
`python ..\..\Scripts\cli\ble_trace.py read --elf build.elf` shows what the FlySight saw. The
firmware builds were made in WSL with `build_variant.sh`, whose header lists the six of the
report; its options `file:` and `commit:` make the others, a fix alone on `master` or `develop`
for instance. Two patches are test aids for that script: `forget-bonds.patch`, a firmware that
forgets its bonds at every start, and `nvm-mirror.patch`, a firmware whose BLE stack keeps its
bonds where the ST-Link can read them (`nvm_dump.py`).
