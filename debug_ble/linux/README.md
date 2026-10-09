# FlySight 2 BLE on Linux

What was measured on Linux on 8 October 2026, with the same bench FlySight as on macOS, and the
scripts that did it. The macOS, Android and iPadOS results are in
`../ble-pairing-fixes-report.html`.

## Bench

- The bench FlySight 2, on its battery, ST-Link attached, USB cable unplugged. Firmware: branch
  `fix/ble_pairing_issues` with the trace of `Scripts/cli/ble_trace.py`, except in the section on
  the four builds of the report.
- A laptop under Ubuntu 24.04: kernel 6.8, BlueZ 5.72, Intel AX211 adapter, `main.conf` as
  shipped (`Privacy` off). Public address `E4:0D:36:**:**:**`.
- The ST-Link is driven through OpenOCD: `vbus.py` and `ble_trace.py` now use it when
  `STM32_Programmer_CLI` is not installed. See `Scripts/cli/README.md`, section Linux.

## Results

### The FlySight seen from BlueZ alone (`bluez_probe.py`)

No SkyGames code here: the probe scans, connects, reads `FT_Packet_In` (which needs an encrypted
link) and disconnects.

| Situation | Result |
|-----------|--------|
| Address the PC connects from | its public identity address, no resolvable private address (`CONNECTED ... peer=E4:0D:36:**:**:** (public) peerRPA=none`), like the Mac |
| First pairing, no BlueZ agent registered, pairing triggered by the read | refused: the FlySight records `PAIRING_COMPLETE status=2 reason=0x0c`, the read never completes, and the FlySight closes the link at its 30-second timeout |
| First pairing, no agent, `Device1.Pair` called explicitly | refused the same way (`org.bluez.Error.AuthenticationFailed` after 0.3 s) |
| First pairing, an agent registered | paired and bonded, read OK (244 bytes) 1.1 s after the request |
| Bonded, FlySight idle | connects in 0.8 s, encrypted read OK, no new pairing |
| Bonded, FlySight in pairing mode | connects in 0.9 s, encrypted read OK, no new pairing |
| Same two, with the first fix switched off (`ble_trace.py set privacy off`) | connects in both, read OK: **Linux is not affected by bug 1** |
| Pairing window left to expire | flag `01` for 30.4 s, then `00`, still advertising 256 s after the double press |

Why bug 1 does not apply here: with `Privacy` off, a Linux host gives the FlySight no identity
resolving key when it bonds, and the controller only ignores a peer's identity address when it
holds such a key for it. That last part is the specification as I read it, not something
measured; what is measured is that the connection goes through with the fix off.

So on Linux the firmware was never the obstacle. Without an agent, BlueZ keeps the adapter
non-bondable (`bluetoothctl show`: `Pairable: no`) and the kernel's request to confirm a "Just
Works" pairing finds nobody to answer. An application has to register an agent, and then pair.

### The native library of SkyGames (`fs_probe.py`)

`Libs/FlySightApi/core/src/nativeInterop/c/linux/ble_linux.c` could not work: six functions of
the header were missing, D-Bus signals were never dispatched (so no scan result and no
notification reached the caller), and nothing dealt with pairing. It was rewritten on GDBus; see
`LINUX_IMPLEMENTATION.md` next to it. The probe loads the library and calls it as the app does.

| Situation | Result |
|-----------|--------|
| Scan | the three FlySights in range, with their pairing flag |
| First pairing (FlySight in pairing mode) | connected and bonded in 1.6 s, encrypted read OK, ping acknowledged |
| Bonded, FlySight idle | connected in 0.6 s, read OK, ping acknowledged |
| Bonded, FlySight in pairing mode | connected in 0.3 s, read OK, ping acknowledged |
| New process, saved identifier, no scan (what the app does when it starts) | connected in 0.6 s |
| Identifier never seen | refused at once (`BLE_ERROR_INVALID_PARAM`) |
| 40 s connected, ping every 14 s | link kept, every ping acknowledged |
| No ping | disconnect callback 30.1 s after the last write (the FlySight's timeout) |
| FlySight reset while connected | disconnect callback within a second |
| Reconnection after that reset | connected in 0.6 s |
| A FlySight this PC is not bonded with, idle | connect callback with `BLE_ERROR_CONNECT_FAILED` after 3.8 s |

### The Kotlin layers of FlySightApi (`jvm-probe/`)

A JVM program without a window that uses `FlySightApi` as SkyGames does: scan or saved
identifier, `createDevice`, `connect`, then what the library reads by itself (`FLYSIGHT.TXT`,
`CONFIG.TXT`, mode, battery).

| Situation | Result |
|-----------|--------|
| First pairing (FlySight in pairing mode) | connected and bonded in 1.6 s, both files read |
| Saved identifier, FlySight idle, 40 s | both files read, pings answered, still connected at 40 s |
| Saved identifier, FlySight in pairing mode | connected in 0.3 to 0.4 s, both files read (3 of 3, after the fixes below) |
| Scan, bonded FlySight | found under the saved identifier, both files read |

Repeating the connection found four defects, none of them in the Linux library. Each series
below is the same run, saved identifier and FlySight idle, repeated; a run is clean when both
files come back whole. The numbers 1 to 4 are those of the list that follows.

| What was fixed | Clean runs |
|----------------|------------|
| Nothing | 7 of 10: two with `CONFIG.TXT` stalled for 20 s, one with a file 242 bytes short |
| In the app: 2, and the read request sent again (1) | 12 of 12, six of them after a second read request |
| Plus the firmware fix (1) | 11 of 12, no second request any more; one killed by 3 |
| Plus 3 | 24 of 25; one with a file 242 bytes short (4) |
| Plus 4, that is everything | 40 of 40: no second request, no packet sent again |
| Everything in the app, firmware without its fix | 20 of 20, eight of them after a second read request |

1. **Firmware, `FlySight/crs.c`.** A command that reaches the FlySight right behind the last
   acknowledgement of a file read is taken out of the queue while the firmware is still in its
   reading state, and dropped without an answer. BlueZ sends the acknowledgement and the next
   read request back to back, so the request for `CONFIG.TXT` was lost in 6 runs of 12.
   Fix (`fa2a4ad` on the branch): stop reading the queue once the file is fully acknowledged,
   and run the idle state for what is left. In the app, a read request that nothing answers within 2 s is now sent again,
   which is what makes it work with a firmware that does not have the fix.
2. **`KmpGatt.desktop.kt` / `DesktopKmpBleSocket.kt`.** One coroutine was launched per native
   callback on a parallel dispatcher, so two notifications received back to back could be
   handled in the wrong order. They now go through one single-file dispatcher.
3. **`GattTaskQueue.kt`.** One removal from the task list was done without the lock the event
   collector holds when it copies that list. The copy could hold a null; the collector died of it
   and no GATT event was handled any more.
4. **`GattTaskQueue.kt` and `BleFileReader.kt`.** `addTask` launched a coroutine per task, so two
   acknowledgements could be sent in the wrong order. The FlySight ignores an acknowledgement
   that comes before the previous one, and later sends the rest of the file again; the next
   reader took those packets for the start of its own file and returned 674 bytes of
   `FLYSIGHT.TXT` as `CONFIG.TXT`. Tasks are now queued in the order they are added, and a reader
   only starts with packet 0.

Fixes 2 to 4 are in code shared with macOS (2) or with every platform (3, 4). They were run on
Linux only.

The firmware fix (1) alone, on the published `master` and `develop` and on each with that fix
and nothing else. A file is read 30 times, and each time a ping is written right behind the
acknowledgement of its last packet (`crs_back_to_back.py`; the ping does not use the card):

| | `master` | `master` + fix | `develop` | `develop` + fix |
|---|---|---|---|---|
| Ping right behind the last acknowledgement, 30 times | dropped 19 times | answered 30 times | dropped 27 times | answered 30 times |
| The application's two files, 12 connections | both read, 12 of 12 | both read, 12 of 12 | both read, 12 of 12 | both read, 12 of 12 |

In that series the application lost no request on any build: whether its request lands behind
the acknowledgement or a moment later depends on timing.

### The four builds of the report, and the branch

`master` `9ec7186` and `develop` `e67ee9f` as published upstream, and each with the three fixes
of the report and nothing else, apart from `CFG_DEBUGGER_SUPPORTED` set to 1 for the bench. The
PC was bonded with the board beforehand. For each build: the native library and the Kotlin
layers connect with the FlySight idle and in pairing mode, SkyGames does the same from its
window, then a pairing window is left to expire (double press, scanner for 262 s). The last row
is the branch with the fixes of the report and the two file-transfer fixes described on this
page, plus the trace of `ble_trace.py`.

| Firmware | Library, idle and pairing mode | Kotlin layers, idle and pairing mode | SkyGames, idle and pairing mode | Pairing window left to expire |
|----------|--------------------------------|--------------------------------------|---------------------------------|-------------------------------|
| `master`, unmodified | connects, encrypted read OK | connects, both files read | connected, configuration loaded | `01` for 33 s, then `00`, still advertising at 255 s |
| `master` + patch | connects, encrypted read OK | connects, both files read | connected, configuration loaded | `01` for 30 s, then `00`, still advertising at 255 s |
| `develop`, unmodified | connects, encrypted read OK | connects, both files read | connected, configuration loaded | `01` for 209 s, then no advertising (bug 2) |
| `develop` + patch | connects, encrypted read OK | connects, both files read | connected, configuration loaded | `01` for 30 s, then `00`, still advertising at 254 s |
| branch, with the two firmware fixes of this page | connects, encrypted read OK | connects, both files read | connected, configuration loaded | `01` for 34 s, then `00`, still advertising at 255 s |

Linux connects on the two unmodified builds: bug 1 does not concern it. Bug 2 shows from Linux as
from the other hosts, and the patch removes it.

This table is one pass: the five builds one after the other, each flashed once, nothing run
again. The pairing window is timed by the scanner on the PC, which gave 30 to 34 s from one build
to the next. An earlier pass had not gone that way: SkyGames had been left open, idle,
next to the scripts, and two things came out of it:

- **The library left its notification sessions behind.** BlueZ keeps such a session for as long
  as the program that asked for it is on the bus, across disconnections, and emits each
  notification once per session: the idle application made every other program receive each
  notification twice. `ble_linux.c` now gives its sessions back when a link ends
  (`stop_notifications`). The three other builds went through that first series all the same.
- **The firmware could end in `Error_Handler()`.** On `master` + patch that first series stopped
  there: the FlySight no longer advertised and answered nothing, until a reset. The patch is not
  the cause; the cause and its fix are in the next section.

### The lockup: a microSD card powered again about 1 ms after losing power

Read over SWD, the firmware was in the endless loop of `Error_Handler()`, called from
`FatFS_Init()` (`resource_manager.c`) because `f_mount()` had failed, itself reached from
`FS_CRS_State_Idle()` on a read request. Replayed on purpose with doubled notifications
(`stale_subscriber.py`), it happened in 2 sessions out of 4 on `master` + patch and in 1 out of 4
on unmodified `master`.

The firmware cuts the power of the card (`VCC_EN`) when a file transfer ends, and turns it on
again for the next command. What the frozen firmware held when it locked up again
(`postmortem.py`): the card had answered its initialisation, but had been taken for a
standard-capacity card when it is a high-capacity one, had refused the switch to high speed, and
the sector read as sector 0 was the boot sector of the partition with 32 foreign bytes at its
start. FatFs found no file system in that and returned `FR_NO_FILESYSTEM`.

The same state can be produced at will, without BLE. `sd-power-gap-experiment.patch` (for
`master`) makes the firmware, at start-up, mount the card, cut its power for a chosen time and
mount it again, six times for each of twenty durations; `sd_power_gap.py` reads the result:

| Time without power | Second mount |
|--------------------|--------------|
| 0 to 0.7 ms | 6 of 6 OK, for each of the 8 durations |
| 1.0 ms | 2 of 6 fail |
| 1.3 ms | 6 of 6 fail |
| 1.6 to 45 ms | 6 of 6 OK, for each of the 10 durations |

Each failure is the one above: `FR_NO_FILESYSTEM`, wrong card type, no high speed. My reading:
under 1 ms the rail has not dropped enough for the card to notice, from 1.6 ms the card restarts
from scratch, and in between it does neither. A firmware that records each of its mounts showed
how close a session comes. SkyGames reads two files one after the other at each connection, and
the second request finds the card:

- in 16 normal sessions (8 on `master`, 8 on `develop`): less than 1 ms after its power was cut
  in 13 of them, and 2 s after in the 3 where the request had to be sent again;
- in the sessions with doubled notifications: 0.5 to 2 ms after (43 ms when the request came one
  connection event later).

So a normal session stays under the window, by less than a millisecond, and the lockup was only
ever seen with the doubled notifications. Gaps from 76 ms to 36 s, measured the same way, all
mounted.

Fix, in `VCC_DeInit()` (`resource_manager.c`): once `VCC_EN` is low, wait 50 ms before returning,
so that nothing turns the rail on again before it has discharged. With it:

- the same experiment gives 60 mounts OK out of 60, the 1.0 and 1.3 ms ones included;
- `master` + patch + this fix went through 12 replayed sessions out of 12 without locking up; without
  it, it had just locked up at the second one;
- the branch build with this fix and the one of `crs.c`: 8 replayed sessions out of 8.

A reset is the other way to cut the power of a card for a short time, and the fix does nothing
there: the firmware turns the rail on again when its millisecond counter reads 1. It does no harm:
in 68 resets sent over SWD while the card was powered and mounted, the firmware started every
time, opened and read `flysight.txt` correctly, and kept its keys.

The same two tests on the published `master` and `develop`, and on each with this fix and
nothing else (6 mounts per duration on `master`, 3 on the others):

| | `master` | `master` + fix | `develop` | `develop` + fix |
|---|---|---|---|---|
| Second mount after 1.0 ms without power | 2 of 6 fail | 3 of 3 OK | 3 of 3 fail | 3 of 3 OK |
| Second mount after 1.3 ms | 6 of 6 fail | 3 of 3 OK | 3 of 3 fail | 3 of 3 OK |
| Second mount after the 18 other durations, 0 to 45 ms | all OK | all OK | all OK | all OK |
| Lockups in BLE sessions with doubled notifications | 1 in 6 | 0 in 6 | 1 in 6 | 0 in 6 |

The 50 ms are a margin chosen from these measurements, 30 times the longest gap that failed on
this board with this card; the rail was not looked at with an oscilloscope. A card that cannot be
mounted still ends in `Error_Handler()`: that is how the firmware treats it everywhere, and it is
left as it is. The fix is committed on the branch as `d94ea11`.

### A reset while `flysight.txt` is being rewritten: the FlySight forgets its BLE keys

Found by accident while measuring the above, then reproduced on purpose. It has nothing to do
with Linux, nor with the card losing power.

At every start, `FS_State_Init()` (`state.c`) reads `/flysight.txt`, then writes it back from
memory with `FA_CREATE_ALWAYS`, which empties the file first. On the bench build, timed with
breakpoints on three starts: the write begins 125 ms after the reset, the file is emptied at
130 ms and closed at 145 ms. A reset that falls in between leaves an empty file. At the next
start the firmware opens it, finds no `BLE_IRK` and no `BLE_ERK`, draws new ones, and writes them:
the change is permanent, and every other value of the file is back to its default.

| Reset sent while the firmware is stopped... | Keys afterwards |
|---------------------------------------------|-----------------|
| in `FS_State_Write()`, at its entry or just after its `f_open()` | changed, 6 times out of 6 |
| in `FS_State_Read()`, just after its `f_open()` (50 ms earlier) | kept, 4 out of 4 |
| in `FatFS_DeInit()`, once the file is closed | kept, 68 out of 68 |
| nowhere: an ordinary reset, card off | kept, 17 out of 17 |

In the six cases of the first row the next start was caught at the call that draws the new IRK,
in `FS_State_Complete()`, and the root directory it had just read showed `FLYSIGHT.TXT` with a
size of 0 and no cluster. A reset sent at the entry of `FS_State_Write()` counts as "during the
write" because the reset of OpenOCD lets the firmware run a few milliseconds more.

How it happened the first time: three SWD measurements run back to back, each one starting with a
reset about 0.15 s after the previous one had let the firmware go. Replayed eight times, that
sequence changed the keys six times. The keys were compared by the SHA-1 of what the firmware
holds in memory, read twice.

What it does to the hosts: with a new IRK, a bonded host no longer recognises the FlySight, and
the PC had to pair again each time. The FlySight still lists its bonded devices (4 here).

`master` `9ec7186` and `develop` `e67ee9f` have the same `FS_State_Init()` and the same emptying
`f_open()`, and lose their keys the same way: see the table below.

Fix, in `state.c`, committed on the branch as `ba7e1d8`. `FS_State_Write()` writes the new state
to `/flysight.tmp`; only once that file is complete does it remove `/flysight.txt` and rename the
new file. The previous file stays valid until then. Between the removal and the renaming only the
new file exists: `FS_State_Read()`, when it cannot open `/flysight.txt`, renames `/flysight.tmp`
and reads it.

When the new file cannot be written in full, the state is written to `/flysight.txt` in place, as
before the fix. That is for a card with no free cluster: FatFs then writes less than asked
without reporting an error, and a first version of the fix would have replaced a good file by an
incomplete one. The code before the fix gets through on such a card, because emptying the file
frees the cluster it writes to next.

FatFs also searches the whole FAT before such a write fails. A first version of the fix wrote the whole
state before it looked at the result, one search for each of 136 writes: 43 s on a card that was
really full. The fix gives the new file up at the first write that fails.

Each build below went through the same resets, sent over SWD with the firmware stopped at the
place named. "Kept" and "changed" are about the keys. `master` and `develop` are the published
commits, "+ fix" is the same with this fix and nothing else.

| Reset sent with the firmware stopped... | `master` | `master` + fix | `develop` | `develop` + fix | branch |
|-----------------------------------------|----------|----------------|-----------|-----------------|--------|
| nowhere: an ordinary reset | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 |
| just after the state file is opened for reading | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 |
| just after the file to write is opened | **changed 4/4** | kept 4/4 | **changed 4/4** | kept 4/4 | kept 4/4 |
| new file complete, previous one about to be removed | n/a | kept 4/4 | n/a | kept 4/4 | kept 4/4 |
| previous file removed, renaming skipped by the debugger | n/a | kept 3/3 | n/a | kept 3/3 | kept 3/3 |
| after a start in which the debugger refused the new file its first cluster | n/a | kept 3/3 | n/a | kept 3/3 | kept 3/3 |
| everything written and closed, card still powered | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 | kept 3/3 |

Without the fix, each of the 8 resets of the third row was followed by a start caught drawing a
new IRK. With the fix, a start that found `/flysight.txt` missing put the new file in its place
and read the same keys: 3 times out of 3 in row five on each build. In row four the reset came
before the removal in this series, and the next start was an ordinary one; in the series run with
the first version of the fix it came after it, 4 times out of 4 on each build, and the next start put
the file back the same way. In row six the firmware gave up the second file and rewrote the first in place, 3 times out of 3 on each
build. After every reset of a build with the fix the root directory showed `FLYSIGHT.TXT` at its
usual size and no `FLYSIGHT.TMP`.

Before and after its resets, each build was also used over BLE: the PC connected and read
`CONFIG.TXT` (6020 characters) and `FLYSIGHT.TXT`. On `master` and on `develop` it had to pair
again after the resets; on the builds with the fix it connected under the same identifier as
before, with no new pairing.

**Outside start-up.** `FS_State_Write()` also runs when active mode or start mode starts
(`FS_State_NextSession()`), when config mode ends and after USB is unplugged. Tested with the USB
cable of the FlySight plugged into the PC, which mounts the card and reads `FLYSIGHT.TXT` from it
after each step; the button and the VBUS detection pin are driven over SWD (`state_modes.py`).
The branch is taken without this fix and with it.

| Test | branch without the fix | branch | `master` | `master` + fix | `develop` | `develop` + fix |
|------|------------------------|--------|----------|----------------|-----------|-----------------|
| Active mode entered and left, twice | OK | OK | OK | OK | OK | OK |
| Start mode entered and left | OK | OK | OK | OK | OK | OK |
| Config mode | OK | OK | OK | OK | OK | OK |
| USB unplugged and plugged back, the PC as host | OK 3/3 | OK 2/2 | OK 3/3 | OK 2/2 | OK 3/3 | OK 2/2 |
| Reset just after the file to write is opened, as active mode starts | **keys changed 2/2** | kept 2/2 | **keys changed 2/2** | kept 2/2 | **keys changed 2/2** | kept 2/2 |
| The same reset, in the write that follows a USB unplug | **keys changed 2/2** | kept 2/2 | **keys changed 2/2** | kept 2/2 | **keys changed 2/2** | kept 2/2 |

"OK" is: the mode is entered; `Temp_Folder` goes up by one and a new `Session_ID` is drawn where
the mode does that; the keys are the same before, during and after; and the PC then reads a
complete `FLYSIGHT.TXT` holding the new values, with no `FLYSIGHT.TMP` next to it. This card has
no `/config` folder, so config mode ends at once: what is exercised is the write at its end. A
USB unplug is VBUS forced low while the PC has the card mounted, not a cable pulled out. Every
start was made to see VBUS low at the point where the firmware first reads it, because `develop`
faults when it starts with the cable in (the branch has a fix for that).

**A cable really pulled out.** Someone pulls the cable of the FlySight out of the PC and plugs it
back in each time `state_real_unplug.py` asks for it; the script sees by itself that it was done.
The 14 pulls of the table are two per build, and a third on the two builds of the branch. Each
build is flashed with the cable out, on the battery.

| | branch without the fix | branch | `master` | `master` + fix | `develop` | `develop` + fix |
|---|---|---|---|---|---|---|
| Cable pulled out with the card mounted on the PC, nothing stopping the firmware | OK | OK | OK | OK | OK | OK |
| Cable pulled out after the PC ejected the card: writes of the state file, time of the write | 1, 20 ms | 1, 27 ms | 1, 20 ms | 1, 36 ms | 1, 19 ms | 1, 35 ms |
| Cable pulled out, and a reset just after the file to write is opened | **keys changed** | kept | not run | not run | not run | not run |

"OK" is: four seconds after the pull the firmware is asleep with the same keys; plugged back in,
the PC reads a complete `FLYSIGHT.TXT` with the same keys and the same `Temp_Folder`, and no
`FLYSIGHT.TMP`. One write per pull: the falling VBUS does not bounce into a second one. The file
read after the pull is one byte longer than the one read before it. On the last build the two
were compared field by field: `FUS_Ver` and `Stack_Ver`, which read `8.0.0` in a file written at
start-up, before the second processor runs, and `1.2.0` and `1.19.0` afterwards. That is so with
and without the fix. After the reset on the branch without the fix, the whole file was lost, not
only the keys: `Temp_Folder` was back to 0000, so the sessions that follow are written over
`TEMP/0001` and the next ones.

**Config mode with a `/config` folder.** The card is given a `/config` folder with three files,
`ALPHA.TXT`, `BRAVO.TXT` and `CHARLIE.TXT`, each holding an `Init_File` line. The card has no
`/audio` folder, so nothing is played and each file is announced for about half a second.

| | branch without the fix | branch | `master` | `master` + fix | `develop` | `develop` + fix |
|---|---|---|---|---|---|---|
| Config mode goes through the three files and ends by itself | OK | OK | OK | OK | OK | OK |
| A press while a file is announced, then active mode entered and left | **stopped once**, then OK 14/14 | OK 2/2 | OK 2/2 | OK 2/2 | OK 2/2 | OK 2/2 |
| Reset just after the file to write is opened, as the press leaves config mode | **keys changed** 1/1 | kept 2/2 | **keys changed** 2/2 | kept 2/2 | **keys changed** 2/2 | kept 2/2 |

First line: `Config_File` ends empty, in the firmware and in the file the PC reads. Second line:
`Config_File` holds the name of a file of the folder, the same in the firmware and on the card
(`ALPHA.TXT`, the first: the press came earlier than aimed); active mode then reads that file
from inside `/config` and writes the state while `/config` is the current directory:
`Temp_Folder` goes up by one, the keys are kept, and no state file appears in `/config`. Third
line: with the fix the keys and `Config_File` are those of before the press; without it the keys
change and `Config_File` is emptied.

**One stop that is not explained.** On the branch without this fix, the first entry into active
mode after config mode ended in `Error_Handler()`. `Temp_Folder` had gone from 2 to 3 and was
written; `TEMP/0003` held the four files of the session, `EVENT.CSV` and `TRACK.CSV` with their
header and no line after it; the mode was still "sleep". So it stopped inside
`FS_ActiveMode_Init()`, after the files of the session were opened: in what follows there, the
audio, the ADC, the GNSS or the sensors. The firmware was not read at that point: the test chain
went on and flashed the next build, and the second try of that build and the first reset test
found a firmware still stopped, which is why they are not counted. Run again on the same build,
14 times, the test never stopped. That build has the 50 ms of the microSD fix, and the card had
just been mounted and written to. A firmware stopped like this has to be read with
`postmortem.py` before anything else touches it.

**Time added.** Time spent in `FS_State_Write()`, from the millisecond counter of the firmware,
three measurements each (`state_modes.py timing`):

| | branch without the fix | branch | `master` | `master` + fix | `develop` | `develop` + fix |
|---|---|---|---|---|---|---|
| At start-up | 20 to 23 ms | 31 to 32 ms | 20 ms | 40 to 43 ms | 20 ms | 39, 40 and 93 ms |
| When active mode starts | 21 to 24 ms | 40 ms | 20 ms | 40 to 43 ms | 21 to 24 ms | 40 to 41 ms |

**A card that is really full.** `card-fill-test.patch` makes a build give one file, `/FILL.BIN`,
every free cluster of the card: 63 365 clusters, 2.07 GB, after which the PC reports 0 KB free.
Deleting the file from the PC gives the space back, with one precaution: unplug the cable, or
make the firmware leave USB mode, before the next flash or reset (see the end of this section).
On that card:

| | Without the fix | With the first version of the fix | With the fix |
|---|---|---|---|
| Time spent in `FS_State_Write()` at start-up | 20 ms | **42 to 43 s** | 338 to 341 ms |
| Two starts | state rewritten, keys kept | keys kept; still writing 4 s after each start | second file given up, state rewritten in place, keys kept |
| USB unplugged and plugged back | OK | not judged: the write was not over when the test looked | OK |
| Active mode entered and left | OK on the branch and on `master` | not judged, for the same reason | OK |

The first column is the branch without this fix, `master` and `develop`; the two others are the
same three with the fix. On `develop` without the fix the firmware had not finished entering
active mode 6 s after the press, busy looking for free clusters for its own files, so the test
pressed again too early; the last column allowed 20 s. The 0.34 s is one search of the FAT
followed by the write in place; that time grows with the size of the FAT, and this card has a
small one (a 2 GB FAT16 volume with 32 KB clusters).

Still not tested: a jump, or any active mode longer than a few seconds on a bench; a reset that
does not come from the debugger; config mode with audio files to play; another card. And a
`flysight.txt` that a firmware without the fix has already emptied stays lost.

The tests of the modes added sessions to `TEMP` on the bench card (about 3.7 MB; the folder
holds 34 of them), and `Temp_Folder` was sent back to 0000 several times by the resets on the
builds without the fix: the sessions of the later tests were written over the first ones.

**The bench card still lists `/FILL.BIN`, and the file is not there.** The firmware keeps the
last sectors a PC writes in RAM (`usb_storage_cache.c`) and puts them on the card when the PC
touches another part of the card, when the PC asks for it, or when USB mode ends. Linux never
asks: the kernel log says `Assuming drive cache: write through`. The test deleted `FILL.BIN`
from the PC, ran `sync`, and flashed the next build three seconds later. The sectors of the FAT
had reached the card, the sector of the root directory had not, and the reset of the flash
dropped it. The card is left with 2 024 256 KB free and a directory entry of 2.07 GB whose
clusters are free, or belong by now to the sessions written to `TEMP` afterwards. The firmware
never opens that file and works as before. A PC must not delete it the normal way: the deletion
follows the clusters from the first one, and would free those of another file, or stop on an
error (Linux then puts the volume in read-only mode). What repairs it is `chkdsk /f` on Windows
or `fsck.vfat` on Linux, a format of the card, or marking that one entry deleted. A build that
does the last was written and not run. A human gets there only if the FlySight resets with the
cable in, between a write of the PC and the unplugging. The entry has since been moved to the
trash folder of the card (`.Trash-<uid>/files`) from the file manager. That moved the entry
and nothing else: emptying that trash is the deletion to avoid.

`state_rewrite_reset.py` is the script of both tables. On a firmware without the fix, its
`midwrite` mode changes the keys of the FlySight for good: every host has to pair again
afterwards.

### The SkyGames application itself

SkyGames did not build on this PC, for two reasons that have nothing to do with BLE, both dealt
with in the SkyGames repository:

- eSpeak-ng keeps paths in a buffer of 160 characters, and the build tree under a long home
  directory overflowed it (`Bad vowel file`). The buffer is now 1024 (`AudioModule`).
- The Firebase C++ SDK ships static libraries for Linux that are not position independent, so
  `libskygames_firebase.so` could not be linked. On Linux the same functions are now built into
  a program the JVM talks to (`skygames_firebase_helper`, `UserModule`; see
  `docs/native-libraries.md` there). Signing in with Google works through it.

Then, in the window of SkyGames, signed in, with the bond removed on the PC first:

| Situation | Result |
|-----------|--------|
| "Add a device", FlySight in pairing mode | the three FlySights in range are listed; the bench one connects, pairs (`PAIRING_COMPLETE status=0` on the FlySight) and its configuration is shown |
| Disconnect, then Connect, FlySight idle | connected, configuration shown |
| Disconnect, FlySight put back in pairing mode, Connect | connected, configuration shown: the sequence the investigation started from |
| Application closed and started again | still signed in; reconnects by itself to the saved FlySight, configuration shown |

The application ran in a nested X server without a window manager and with software rendering,
which is enough for these tests but says nothing of how it looks on a normal desktop.

## Observations with no change proposed

- The FlySight draws its static random address at every boot (`app_ble.c`, `FS_Common_GetRandomBytes`):
  the trace shows a different `static addr` after each reset. Bonded hosts are not affected, they
  recognise it by its key. BlueZ files a bond under that address, so a FlySight forgotten and
  paired again after a reboot appears under a new one.
- A host that has forgotten the FlySight while the FlySight still holds the bond can pair again
  with the FlySight idle, without pairing mode: its address is still in the accept list.

## Running the scripts

They need Python 3 with `python3-dbus` and `python3-gi`, which Ubuntu installs by default.

```bash
python3 scan.py 20                              # every FlySight advert, with its pairing flag
python3 bluez_probe.py connect --flag 01 --agent   # BlueZ alone: first pairing (FlySight in pairing mode)
python3 bluez_probe.py connect --flag 00           # BlueZ alone: bonded, FlySight idle
python3 bluez_probe.py forget                      # remove the bond on the PC
python3 fs_probe.py connect --flag 01 -v           # SkyGames' native library: same sequence as the app
python3 fs_probe.py connect --id AA:BB:CC:DD:EE:FF --stay 40   # saved identifier, no scan
```

`stale_subscriber.py` leaves notification sessions behind in BlueZ, which doubles every
notification for the next program that connects: run it, then `jvm-probe`, to replay the sessions
after which the `master` builds ended in `Error_Handler()`.

`crs_back_to_back.py AA:BB:CC:DD:EE:FF 10` reads a file ten times and writes a ping right behind
each last acknowledgement: a firmware without the fix of `crs.c` drops most of those pings.

For the two firmware defects of the SD card, with the ST-Link connected and
`arm-none-eabi-binutils` installed:

```bash
python3 postmortem.py build.elf        # a firmware stopped in Error_Handler: card state, last sector read, stack
git apply sd-power-gap-experiment.patch   # on master 9ec7186; build, flash, wait 5 minutes, then:
python3 sd_power_gap.py build.elf      # second mount for each time without power
python3 state_rewrite_reset.py build.elf 3 preread dumps/    # resets before the rewrite: keys kept
python3 state_rewrite_reset.py build.elf 4 midwrite dumps/   # during the rewrite: NEW KEYS without the fix, hosts must pair again
python3 state_rewrite_reset.py build.elf 3 gap dumps/        # with the fix only: between the removal and the renaming
python3 state_rewrite_reset.py build.elf 3 full dumps/       # with the fix only: the card made to look full
python3 state_modes.py build.elf active 2      # USB cable in: active mode entered and left, card read from the PC
python3 state_modes.py build.elf reset-active 2   # reset as active mode starts: NEW KEYS without the fix
./state_write_time.sh build.elf 3              # time spent in FS_State_Write() at start-up
python3 state_modes.py build.elf config-select 2   # with a /config folder of three files on the card
python3 state_modes.py build.elf reset-config 2    # reset as a press leaves config mode: NEW KEYS without the fix
python3 state_real_unplug.py a.elf b.elf:reset     # someone pulls the cable out when the script says so
```

`fs_probe.py` loads `libflysight_ble.so` from the SkyGames checkout
(`./gradlew -p Libs/FlySightApi :core:buildNativeLib`), or from `FLYSIGHT_BLE_LIB`. It is plain
`ctypes`, so it also runs against the macOS and Windows builds of the library.

```bash
cd jvm-probe
~/AndroidStudioProjects/skygames/gradlew installDist    # FLYSIGHT_API_DIR if FlySightApi is elsewhere
build/install/fs-jvm-probe/bin/fs-jvm-probe FlySight                       # scan, connect, read
build/install/fs-jvm-probe/bin/fs-jvm-probe FlySight AA:BB:CC:DD:EE:FF 40  # saved identifier, stay 40 s
```

With the bench: `python3 ../../Scripts/cli/vbus.py button` puts the FlySight in pairing mode, and
`python3 ../../Scripts/cli/ble_trace.py read --elf build.elf` shows what the FlySight saw.
