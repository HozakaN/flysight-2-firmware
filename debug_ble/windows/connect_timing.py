#!/usr/bin/env python3
"""How long Windows takes to reach a FlySight it is paired with, depending on what the
application does about scanning.

    connect_timing.py [--name FlySight] [--scan none|during|before|before-keep] [--enumerate] [--timeout 40]

    --scan none          no scan: the link is asked for, and Windows finds the device by itself
    --scan during        a scan is started, and the link is asked for at once
    --scan before        the scan runs until the FlySight is heard, is stopped, then the link is asked for
    --scan before-keep   the same, with the scan left running until the link is up
    --enumerate          for as long as the scan runs, the Bluetooth LE devices around are also
                         enumerated, as the Bluetooth settings do: Windows listens more meanwhile
    --late-session       the session that asks Windows to keep the link is only created once
                         the services are read, instead of before the link is asked for

--scan before --enumerate is what the native library of SkyGames does.

Prints when the FlySight was heard, when Windows reported the link up, and when its services
could be read, in seconds from the start. Exit code 0 if the services were read.
"""
import argparse
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth import (
    BluetoothCacheMode,
    BluetoothConnectionStatus,
    BluetoothLEDevice,
)
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
)
from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus, GattSession
from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind

BLE_ENDPOINTS = 'System.Devices.Aep.ProtocolId:="{bb7bb05e-5972-42b5-94fc-76eaa7084d49}"'

T0 = time.time()


def log(message):
    print('%7.2f %s' % (time.time() - T0, message), flush=True)


async def paired_address(name):
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is None:
            continue
        found = device.bluetooth_address if device.name == name else None
        device.close()
        if found is not None:
            return found
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='FlySight')
    parser.add_argument('--scan', choices=['none', 'during', 'before', 'before-keep'], default='none')
    parser.add_argument('--timeout', type=float, default=40)
    parser.add_argument('--passive', action='store_true', help='scan without asking for scan responses')
    parser.add_argument('--enumerate', action='store_true', help='enumerate the devices while the scan runs')
    parser.add_argument('--late-session', action='store_true', help='no session before the services are read')
    args = parser.parse_args()

    address = await paired_address(args.name)
    if address is None:
        print('Windows holds no pairing for %r' % args.name)
        return 3

    loop = asyncio.get_running_loop()
    heard = asyncio.Event()
    up = asyncio.Event()
    times = {}

    def received(_, event):
        if event.bluetooth_address == address and 'heard' not in times:
            times['heard'] = time.time() - T0
            loop.call_soon_threadsafe(heard.set)

    watcher = None
    enumeration = None

    def stop_scan():
        watcher.stop()
        if enumeration is not None:
            enumeration.stop()

    if args.scan != 'none':
        watcher = BluetoothLEAdvertisementWatcher()
        watcher.scanning_mode = BluetoothLEScanningMode.PASSIVE if args.passive else BluetoothLEScanningMode.ACTIVE
        watcher.add_received(received)
        watcher.start()
        if args.enumerate:
            enumeration = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
                BLE_ENDPOINTS, [], DeviceInformationKind.ASSOCIATION_ENDPOINT)
            enumeration.add_added(lambda *_: None)
            enumeration.add_updated(lambda *_: None)
            enumeration.add_removed(lambda *_: None)
            enumeration.start()
        if args.scan in ('before', 'before-keep'):
            try:
                await asyncio.wait_for(heard.wait(), args.timeout)
            except asyncio.TimeoutError:
                stop_scan()
                log('RESULT: the FlySight was not heard in %ss' % args.timeout)
                return 1
            if args.scan == 'before':
                stop_scan()
                watcher = None

    asked = time.time() - T0
    device = await BluetoothLEDevice.from_bluetooth_address_async(address)
    if device is None:
        log('RESULT: Windows gave no device object')
        return 1

    def on_connection(sender, _):
        if sender.connection_status == BluetoothConnectionStatus.CONNECTED:
            times.setdefault('link', time.time() - T0)
            loop.call_soon_threadsafe(up.set)
        else:
            times['dropped'] = times.get('dropped', 0) + 1

    device.add_connection_status_changed(on_connection)
    session = None
    if not args.late_session:
        session = await GattSession.from_device_id_async(device.bluetooth_device_id)
        session.maintain_connection = True

    services = []
    attempts = 0
    code = 1
    end = T0 + asked + args.timeout
    while time.time() < end:
        attempts += 1
        result = await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if result.status == GattCommunicationStatus.SUCCESS:
            times['services'] = time.time() - T0
            services = list(result.services)
            code = 0
            if session is None:
                session = await GattSession.from_device_id_async(device.bluetooth_device_id)
                session.maintain_connection = True
            break
        await asyncio.sleep(0.5)

    if watcher is not None:
        stop_scan()

    def since(key):
        return '%.2f' % (times[key] - asked) if key in times else '-'

    log('scan=%s%s%s%s: heard at %s, link asked for at %.2f; from then: link up after %s s, services after %s s '
        '(%d request(s), %d link(s) lost at once)' % (
            args.scan, ' (passive)' if args.passive else '', ' with enumeration' if args.enumerate else '',
            ', session after the services' if args.late_session else '',
            '%.2f' % times['heard'] if 'heard' in times else '-', asked, since('link'), since('services'),
            attempts, times.get('dropped', 0)))

    for service in services:
        service.close()
    if session is not None:
        session.maintain_connection = False
        session.close()
    device.close()
    # Windows takes about three seconds to end the link once nothing holds it
    await asyncio.sleep(4)
    return code


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
