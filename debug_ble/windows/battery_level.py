#!/usr/bin/env python3
"""Read the Battery Level (0x2A19) of a FlySight through WinRT, once or for a while.

usage: battery_level.py [--name FlySight] [--first] [--watch SECONDS] [--every 2]

By default the FlySight is the one Windows is paired with. With --first it is not paired yet:
it is looked for in pairing mode, connected to and paired with, and the level is read over that
same link. --watch keeps the link and reads the level again every --every seconds, sending the
FlySight a ping from time to time so that it does not end the link.

Windows reads that characteristic by itself for a paired device, and tells the user when the
battery is low.
"""
import argparse
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher, BluetoothLEScanningMode)
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus, GattSession, GattWriteOption)
from winrt.windows.devices.enumeration import (
    DeviceInformation, DevicePairingKinds, DevicePairingProtectionLevel)
from winrt.windows.storage.streams import Buffer

BATTERY_SERVICE = '0000180f-0000-1000-8000-00805f9b34fb'
BATTERY_LEVEL = '00002a19-0000-1000-8000-00805f9b34fb'
FT_SERVICE = '00000000-cc7a-482a-984a-7f2ed5b3e58f'
FT_PACKET_IN = '00000002-8e22-4541-9d4c-21edae82ed19'
FLYSIGHT_MFG = 0x09DB
T0 = time.time()


def log(message):
    print('%7.2f %s' % (time.time() - T0, message), flush=True)


async def paired_device(name):
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is not None and device.name == name:
            return device
        if device is not None:
            device.close()
    return None


async def device_in_pairing_mode(name, seconds):
    """The first FlySight of that name heard with its pairing flag set."""
    loop = asyncio.get_running_loop()
    found = loop.create_future()
    names = {}

    def received(_, event):
        if event.advertisement.local_name:
            names[event.bluetooth_address] = event.advertisement.local_name
        for item in event.advertisement.manufacturer_data:
            data = bytes(item.data)
            if item.company_id == FLYSIGHT_MFG and data[:1] == b'\x01' and names.get(event.bluetooth_address) == name:
                if not found.done():
                    loop.call_soon_threadsafe(found.set_result, (event.bluetooth_address, event.bluetooth_address_type))

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    watcher.add_received(received)
    watcher.start()
    try:
        address, kind = await asyncio.wait_for(found, seconds)
    except asyncio.TimeoutError:
        return None
    finally:
        watcher.stop()
    return await BluetoothLEDevice.from_bluetooth_address_with_bluetooth_address_type_async(address, kind)


async def characteristic(services, service_uuid, char_uuid):
    for service in services:
        if str(service.uuid) != service_uuid:
            continue
        found = await service.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        for item in found.characteristics:
            if str(item.uuid) == char_uuid:
                return item
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='FlySight')
    parser.add_argument('--first', action='store_true', help='not paired yet: pair over this link (FlySight in pairing mode)')
    parser.add_argument('--watch', type=float, default=0, help='seconds to keep reading')
    parser.add_argument('--every', type=float, default=2)
    args = parser.parse_args()

    device = await (device_in_pairing_mode(args.name, 25) if args.first else paired_device(args.name))
    if device is None:
        log('RESULT: no %s named %r' % ('FlySight in pairing mode' if args.first else 'paired device', args.name))
        return 3

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True
    services = None
    for attempt in range(6):
        result = await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if result.status == GattCommunicationStatus.SUCCESS:
            services = list(result.services)
            break
        await asyncio.sleep(1)
    if services is None:
        log('RESULT: not reachable')
        return 1
    log('connected')

    if args.first and not device.device_information.pairing.is_paired:
        custom = device.device_information.pairing.custom
        custom.add_pairing_requested(lambda sender, event: event.accept())
        outcome = await custom.pair_with_protection_level_async(
            DevicePairingKinds.CONFIRM_ONLY, DevicePairingProtectionLevel.ENCRYPTION)
        log('pairing: status %d' % outcome.status)
        await asyncio.sleep(1)
        for service in services:
            service.close()
        services = list((await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)).services)

    level = await characteristic(services, BATTERY_SERVICE, BATTERY_LEVEL)
    if level is None:
        log('RESULT: this FlySight has no Battery Service (%d services)' % len(services))
        return 2
    packet_in = await characteristic(services, FT_SERVICE, FT_PACKET_IN) if args.watch else None

    code = 1
    last = None
    end = time.time() + args.watch
    next_ping = time.time() + 10
    durations = []
    while True:
        asked = time.time()
        value = await level.read_value_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        durations.append(time.time() - asked)
        if value.status != GattCommunicationStatus.SUCCESS:
            log('read failed: status %d protocol error %s' % (value.status, value.protocol_error))
        else:
            now = bytes(value.value)[0]
            if now != last or not args.watch:
                log('battery level: %d %%' % now)
            last, code = now, 0
        if time.time() >= end:
            break
        if packet_in is not None and time.time() >= next_ping:
            ping = Buffer(1)
            ping.length = 1
            with memoryview(ping) as view:
                view[0] = 0xFE
            await packet_in.write_value_with_result_and_option_async(ping, GattWriteOption.WRITE_WITHOUT_RESPONSE)
            next_ping = time.time() + 10
        await asyncio.sleep(args.every)
    if args.watch and last is not None:
        log('battery level at the end: %d %%' % last)
    log('%d read(s), answered in %.0f to %.0f ms' % (len(durations), min(durations) * 1000, max(durations) * 1000))

    for service in services:
        service.close()
    session.close()
    device.close()
    return code


sys.exit(asyncio.run(main()))
