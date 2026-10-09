#!/usr/bin/env python3
"""How often Windows hears the advertisements of one FlySight.

usage: hear_rate.py --address AA:BB:CC:DD:EE:FF [--seconds 120] [--with-enumeration]

Runs the advertisement watcher of WinRT and prints each advertisement heard from that address,
then how many were heard and how far apart. --with-enumeration also runs, for the same time, the
device enumeration Windows uses when the user adds a Bluetooth device (a DeviceWatcher on the
Bluetooth LE association endpoints), to see whether Windows listens more while it runs.

Needs: pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Devices.Bluetooth
       winrt-Windows.Devices.Bluetooth.Advertisement winrt-Windows.Devices.Enumeration
"""
import argparse
import asyncio
import time

from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementType, BluetoothLEAdvertisementWatcher, BluetoothLEScanningMode)
from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind

BLE_ENDPOINTS = 'System.Devices.Aep.ProtocolId:="{bb7bb05e-5972-42b5-94fc-76eaa7084d49}"'
T0 = time.time()


def parse_address(text):
    return int(text.replace(':', '').replace('-', ''), 16)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--with-enumeration', action='store_true')
    parser.add_argument('--passive', action='store_true', help='passive scanning instead of active')
    args = parser.parse_args()
    wanted = parse_address(args.address)

    heard = []
    others = [0]

    def received(_, event):
        if event.bluetooth_address != wanted:
            others[0] += 1
        elif event.advertisement_type != BluetoothLEAdvertisementType.SCAN_RESPONSE:
            # The answer to a scan request comes with the advertisement it follows: not counted
            heard.append(time.time() - T0)

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.PASSIVE if args.passive else BluetoothLEScanningMode.ACTIVE
    watcher.add_received(received)
    parameters = watcher.scan_parameters
    if parameters is not None:
        print('scan parameters: window %.3f ms every %.3f ms' % (
            parameters.scan_window * 0.625, parameters.scan_interval * 0.625), flush=True)

    enumeration = None
    found = [0]
    if args.with_enumeration:
        enumeration = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
            BLE_ENDPOINTS, [], DeviceInformationKind.ASSOCIATION_ENDPOINT)
        enumeration.add_added(lambda *_: found.__setitem__(0, found[0] + 1))
        enumeration.add_updated(lambda *_: None)
        enumeration.add_removed(lambda *_: None)
        enumeration.add_enumeration_completed(lambda *_: print('%7.2f enumeration completed' % (time.time() - T0), flush=True))
        enumeration.add_stopped(lambda *_: print('%7.2f enumeration stopped' % (time.time() - T0), flush=True))
        enumeration.start()

    watcher.start()
    await asyncio.sleep(args.seconds)
    watcher.stop()
    if enumeration is not None:
        enumeration.stop()

    gaps = [b - a for a, b in zip(heard, heard[1:])]
    print('heard at: ' + ' '.join('%.1f' % t for t in heard))
    if gaps:
        print('RESULT: heard %d times in %.0f s, every %.1f s on average (longest silence %.1f s); '
              '%d advertisements from other devices%s' % (
                  len(heard), args.seconds, sum(gaps) / len(gaps), max(gaps + [heard[0], args.seconds - heard[-1]]),
                  others[0], '; %d endpoints enumerated' % found[0] if enumeration is not None else ''))
    else:
        print('RESULT: heard %d time(s) in %.0f s' % (len(heard), args.seconds))


asyncio.run(main())
