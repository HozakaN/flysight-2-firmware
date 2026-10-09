#!/usr/bin/env python3
"""Follow the pairing flag a FlySight 2 advertises, to see how a pairing window ends.

    pairing_window.py [--name FlySight] [--address AA:BB:..] [--seconds 262]

Start it, then put the FlySight in pairing mode (`Scripts/cli/vbus.py button`). It prints each
change of the flag, and at the end how long the flag stayed at 01 and when the FlySight was
last heard, counted from the first advertisement that carried 01.

A FlySight in pairing mode advertises every 80 to 100 ms, an idle one every 1 to 2.5 s, and
Windows listens 15 % of the time (18 ms every 118 ms): an idle FlySight is only heard every few
seconds, so the times of what follows the window are no more precise than that. The result says
how often it was heard.
"""
import argparse
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
)

FLYSIGHT_MFG = 0x09DB


def text_address(value):
    return ':'.join('%02X' % ((value >> shift) & 0xFF) for shift in range(40, -8, -8))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='FlySight')
    parser.add_argument('--address', help='follow this address instead of the name')
    parser.add_argument('--seconds', type=float, default=262)
    args = parser.parse_args()
    wanted = int(args.address.replace(':', ''), 16) if args.address else None

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    start = time.time()

    def received(_, event):
        flag = None
        for item in event.advertisement.manufacturer_data:
            if item.company_id == FLYSIGHT_MFG:
                flag = bytes(item.data).hex()
        loop.call_soon_threadsafe(queue.put_nowait, (
            time.time() - start, event.bluetooth_address, event.advertisement.local_name, flag))

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    token = watcher.add_received(received)
    watcher.start()

    names = {}
    current = None          # flag last heard
    first_pairing = None    # first advertisement with the flag at 01
    last_pairing = None
    back_to_idle = None     # first advertisement with the flag at 00 after the window
    last_heard = None
    heard = 0
    idle_heard = 0          # advertisements heard once the flag is back to 00
    try:
        while time.time() - start < args.seconds:
            try:
                when, address, name, flag = await asyncio.wait_for(queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            if name:
                names[address] = name
            if flag is None:
                continue
            if wanted is not None:
                if address != wanted:
                    continue
            elif names.get(address) != args.name:
                continue
            heard += 1
            last_heard = when
            if flag != current:
                print('%7.1f %s flag %s -> %s' % (when, text_address(address), current or '--', flag), flush=True)
                current = flag
            if flag == '01':
                if first_pairing is None:
                    first_pairing = when
                last_pairing = when
                back_to_idle = None
                idle_heard = 0
            elif first_pairing is not None:
                if back_to_idle is None:
                    back_to_idle = when
                idle_heard += 1
    finally:
        watcher.stop()
        watcher.remove_received(token)

    end = time.time() - start
    if first_pairing is None:
        print('RESULT: flag 01 never heard in %.0f s (%d advertisement(s) of the FlySight)' % (end, heard))
        return 1
    held = last_pairing - first_pairing
    if back_to_idle is not None:
        every = (last_heard - back_to_idle) / (idle_heard - 1) if idle_heard > 1 else 0
        print('RESULT: flag 01 for %.0f s, then 00 (first heard %.0f s after the start of the window), '
              'still advertising at %.0f s; heard %d times with flag 00, every %.1f s on average' % (
                  held, back_to_idle - first_pairing, last_heard - first_pairing, idle_heard, every))
    elif end - last_pairing > 30:
        print('RESULT: flag 01 for %.0f s, then no advertising (nothing heard for the last %.0f s)' % (
            held, end - last_pairing))
    else:
        print('RESULT: flag 01 for %.0f s, until the end of the listening' % held)
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
