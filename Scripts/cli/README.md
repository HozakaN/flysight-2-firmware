# FlySight CLI Scripts

Python scripts for communicating with a FlySight 2 device over USB serial (CDC).

## Prerequisites

- FlySight 2 with CLI-enabled firmware
- `Competition_Mode: 1` set in `/flysight.txt` on the device (CDC-only mode)
- Python 3.7+

## Setup

```bash
cd Scripts/cli
python3 -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

## Scripts

### Read device state

```bash
python read_flysight.py                        # print to stdout
python read_flysight.py -o flysight.txt        # save to file
python read_flysight.py --port /dev/tty.usbmodemXXXX  # specify port
```

### Write device state

```bash
python write_flysight.py flysight.txt
python write_flysight.py -p /dev/tty.usbmodemXXXX flysight.txt
```

### Read logging configuration

```bash
python read_config.py                          # print to stdout
python read_config.py -o config.txt            # save to file
```

### Write logging configuration

```bash
python write_config.py config.txt
```

### Download latest track

```bash
python track_latest.py                         # save to current directory
python track_latest.py -o ./tracks             # save to specific directory
```

Files are saved under `<output-dir>/<date>/<time>/` (e.g., `./tracks/25-02-28/14-30-00/track.csv`).

### List temp folder contents

```bash
python temp_list.py
python temp_list.py -p /dev/tty.usbmodemXXXX
```

### Delete temp folder contents

```bash
python temp_delete.py
python temp_delete.py -p /dev/tty.usbmodemXXXX
```

### Deploy firmware

Upload a firmware binary (.sfb) to the device:

```bash
python deploy_firmware.py firmware.sfb
python deploy_firmware.py -p /dev/tty.usbmodemXXXX firmware.sfb
```

The file is written to `/FW/app.sfb` on the device using a binary-safe size-prefixed protocol.

### Enable mass storage mode

Switches the device back to mass storage mode on the next USB replug:

```bash
python enable_msc.py
python enable_msc.py -p /dev/tty.usbmodemXXXX
```

After running this, unplug and replug USB. The device will enumerate as a mass storage drive.

## Port auto-detection

All scripts automatically detect the FlySight serial port. If multiple serial devices are connected, use `--port` / `-p` to specify the correct one.

## Firmware CLI commands

These scripts use the following firmware CLI commands over serial:

| Command          | Description                                      |
|------------------|--------------------------------------------------|
| `flysight read`  | Read `/flysight.txt` (device state)              |
| `flysight write` | Write `/flysight.txt` (end with Ctrl-D)          |
| `config read`    | Read `/config.txt` (logging config)              |
| `config write`   | Write `/config.txt` (end with Ctrl-D)            |
| `track latest`   | Dump all files from latest track folder          |
| `temp list`      | List contents of `/temp` folder                  |
| `temp delete`    | Delete all contents of `/temp` folder            |
| `fw upload <n>`  | Upload firmware binary (n bytes to `/FW/app.sfb`)|
| `msc`            | Enable mass storage on next USB replug           |
| `pair start`     | Start BLE pairing                                |
| `pair stop`      | Stop BLE pairing                                 |
| `status`         | Show device mode and BLE state                   |
| `version`        | Show firmware version                            |

You can also use these commands interactively via a serial terminal:

```bash
picocom /dev/tty.usbmodemXXXX -b 115200
```
