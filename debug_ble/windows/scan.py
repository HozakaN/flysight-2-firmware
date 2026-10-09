#!/usr/bin/env python3
"""Scan for FlySight 2 adverts through WinRT and print each one: address, kind of address, name,
pairing flag (00 or 01), RSSI, and whether Windows holds a pairing for that address.

usage: scan.py [seconds] [--all]

A line is printed when a FlySight is first heard, when its flag or its address changes, and
every ten seconds otherwise. `--all` prints every advertising packet.
"""
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth import BluetoothLEDevice
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
)

FLYSIGHT_MFG = 0x09DB
ADDRESS_TYPES = {0: 'public', 1: 'random', 2: 'unspecified'}
ADVERT_TYPES = {0: 'connectable', 1: 'directed', 2: 'scannable', 3: 'non-connectable',
                4: 'scan response', 5: 'extended'}


def text_address(value):
    return ':'.join('%02X' % ((value >> shift) & 0xFF) for shift in range(40, -8, -8))


def random_kind(value):
    """What the two top bits of a random address say it is."""
    return {0b11: 'static', 0b01: 'resolvable private', 0b00: 'non-resolvable private'}.get(
        (value >> 46) & 0b11, 'reserved')


async def is_paired(address):
    try:
        device = await BluetoothLEDevice.from_bluetooth_address_async(address)
    except OSError:
        return None
    if device is None:
        return None
    try:
        return bool(device.device_information.pairing.is_paired)
    finally:
        device.close()


async def main():
    arguments = [a for a in sys.argv[1:] if not a.startswith('--')]
    duration = float(arguments[0]) if arguments else 15
    everything = '--all' in sys.argv

    loop = asyncio.get_running_loop()
    start = time.time()
    names = {}      # address -> name, which only the scan response carries
    shown = {}      # address -> (flag, time of the last line)
    paired = {}     # address -> True, False or None
    queue = asyncio.Queue()

    def received(_, event):
        advert = event.advertisement
        flag = None
        for item in advert.manufacturer_data:
            if item.company_id == FLYSIGHT_MFG:
                flag = bytes(item.data).hex()
        loop.call_soon_threadsafe(queue.put_nowait, (
            time.time() - start, event.bluetooth_address, int(event.bluetooth_address_type),
            int(event.advertisement_type), advert.local_name, flag, event.raw_signal_strength_in_dbm))

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    token = watcher.add_received(received)
    watcher.start()

    try:
        while True:
            remaining = duration - (time.time() - start)
            if remaining <= 0:
                break
            try:
                when, address, address_type, advert_type, name, flag, rssi = await asyncio.wait_for(
                    queue.get(), remaining)
            except asyncio.TimeoutError:
                break

            if name:
                names[address] = name
            if flag is None and 'FlySight' not in names.get(address, ''):
                continue
            if flag is None:
                # The scan response of a FlySight: it carries the name and nothing else
                if not everything:
                    continue
            previous = shown.get(address)
            is_new = previous is None
            changed = previous is not None and flag is not None and previous[0] != flag
            if not (everything or is_new or changed or when - previous[1] >= 10):
                continue
            if address not in paired:
                paired[address] = await is_paired(address)
            kind = ADDRESS_TYPES.get(address_type, str(address_type))
            if address_type == 1:
                kind += ', ' + random_kind(address)
            print('%6.1f %s %s (%s) name=%r flag=%s rssi=%s %s paired=%s' % (
                when, 'new' if is_new else ('chg' if changed else 'adv'), text_address(address), kind,
                names.get(address, ''), flag, rssi, ADVERT_TYPES.get(advert_type, str(advert_type)),
                {True: 'yes', False: 'no', None: '?'}[paired[address]]), flush=True)
            if flag is not None:
                shown[address] = (flag, when)
    finally:
        watcher.stop()
        watcher.remove_received(token)

    print('scan done, %d FlySight address(es)' % len(shown))


if __name__ == '__main__':
    asyncio.run(main())
