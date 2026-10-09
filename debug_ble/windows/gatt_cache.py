#!/usr/bin/env python3
"""The GATT services Windows holds in its cache for the paired FlySight, before and after a link.

usage: gatt_cache.py [--name FlySight] [--link SECONDS]

Windows keeps the services of a bonded device and does not look for them again at each
connection. A device whose services have changed, after a firmware update for instance, tells its
bonded hosts with a Service Changed indication, and Windows then reads them again.

This script never asks Windows to read the services again: it lists the cache, brings the link up
for --link seconds (default 8) without any request, and lists the cache again. A list that
changes was changed by Windows itself, on the indication of the FlySight.
"""
import argparse
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth import (
    BluetoothCacheMode, BluetoothConnectionStatus, BluetoothLEDevice)
from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus, GattSession
from winrt.windows.devices.enumeration import DeviceInformation

T0 = time.time()
SHORT = '-0000-1000-8000-00805f9b34fb'


def log(message):
    print('%7.2f %s' % (time.time() - T0, message), flush=True)


def label(uuid):
    text = str(uuid)
    return text[4:8] if text.startswith('0000') and text.endswith(SHORT) else text[:8]


async def paired_device(name):
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is not None and device.name == name:
            return device
        if device is not None:
            device.close()
    return None


async def cached(device, when):
    result = await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.CACHED)
    if result.status != GattCommunicationStatus.SUCCESS:
        log('cache %s: status %d' % (when, result.status))
        return None
    names = [label(service.uuid) for service in result.services]
    for service in result.services:
        service.close()
    log('cache %s: %d services: %s' % (when, len(names), ' '.join(names)))
    return names


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='FlySight')
    parser.add_argument('--link', type=float, default=8)
    args = parser.parse_args()

    device = await paired_device(args.name)
    if device is None:
        log('RESULT: no paired device named %r' % args.name)
        return 3
    before = await cached(device, 'before the link')

    session = await GattSession.from_device_id_async(device.bluetooth_device_id)
    session.maintain_connection = True
    end = time.time() + 30
    while device.connection_status != BluetoothConnectionStatus.CONNECTED and time.time() < end:
        await asyncio.sleep(0.1)
    if device.connection_status != BluetoothConnectionStatus.CONNECTED:
        log('RESULT: no link in 30 s')
        return 1
    log('link up')
    await asyncio.sleep(args.link)
    after = await cached(device, 'after %g s of link' % args.link)
    session.close()
    device.close()
    log('RESULT: %s' % ('cache unchanged' if before == after else 'cache changed'))
    return 0


sys.exit(asyncio.run(main()))
