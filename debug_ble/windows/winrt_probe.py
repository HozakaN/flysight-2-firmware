#!/usr/bin/env python3
"""Baseline BLE probe for a FlySight 2, straight on WinRT, without the SkyGames library.

    winrt_probe.py adapter
    winrt_probe.py status
    winrt_probe.py forget  [--name FlySight]
    winrt_probe.py connect [--name FlySight] [--flag 00|01] [--pair none|custom|system]
                           [--protection default|none|encryption|authentication]
                           [--no-scan] [--address AA:BB:..] [--no-session] [--no-read]
                           [--ping] [--hold 0] [--scan 20]

`connect` scans until the FlySight advertises (optionally with a given pairing flag), opens the
link, pairs the way `--pair` says, reads FT_Packet_In (which needs an encrypted link), prints
what happened and closes everything. Exit code 0 only if the read succeeded.

    --pair none     nothing is asked for: whatever Windows does when the read is refused
    --pair custom   DeviceInformationCustomPairing with the "confirm only" ceremony, accepted by
                    this program: no window, what an application does for a "Just Works" device
    --pair system   DeviceInformationPairing.PairAsync(): Windows asks the user
"""
import argparse
import asyncio
import sys
import time

from winrt.windows.devices.bluetooth import (
    BluetoothAdapter,
    BluetoothAddressType,
    BluetoothCacheMode,
    BluetoothConnectionStatus,
    BluetoothLEDevice,
)
from winrt.windows.devices.bluetooth.advertisement import (
    BluetoothLEAdvertisementWatcher,
    BluetoothLEScanningMode,
)
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattClientCharacteristicConfigurationDescriptorValue,
    GattCommunicationStatus,
    GattSession,
    GattSessionStatus,
    GattWriteOption,
)
from winrt.windows.devices.enumeration import (
    DeviceInformation,
    DevicePairingKinds,
    DevicePairingProtectionLevel,
    DevicePairingResultStatus,
    DeviceUnpairingResultStatus,
)
from winrt.windows.storage.streams import Buffer

FT_SERVICE = '00000000-cc7a-482a-984a-7f2ed5b3e58f'
FT_PACKET_OUT = '00000001-8e22-4541-9d4c-21edae82ed19'
FT_PACKET_IN = '00000002-8e22-4541-9d4c-21edae82ed19'
FLYSIGHT_MFG = 0x09DB
PROTECTION = {'default': DevicePairingProtectionLevel.DEFAULT, 'none': DevicePairingProtectionLevel.NONE,
              'encryption': DevicePairingProtectionLevel.ENCRYPTION,
              'authentication': DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION}
T0 = time.time()


def log(message):
    print('%7.2f %s' % (time.time() - T0, message), flush=True)


def name_of(enum_type, value):
    for name in dir(enum_type):
        if name.isupper() and getattr(enum_type, name) == value:
            return name
    return str(int(value))


def text_address(value):
    return ':'.join('%02X' % ((value >> shift) & 0xFF) for shift in range(40, -8, -8))


def parse_address(text):
    return int(text.replace(':', '').replace('-', ''), 16)


def address_kind(value, address_type):
    if address_type == BluetoothAddressType.PUBLIC:
        return 'public'
    if address_type == BluetoothAddressType.RANDOM:
        return 'random, ' + {0b11: 'static', 0b01: 'resolvable private',
                             0b00: 'non-resolvable private'}.get((value >> 46) & 0b11, 'reserved')
    return 'unspecified'


def describe(device):
    pairing = device.device_information.pairing
    return 'addr=%s (%s) paired=%d can_pair=%d protection=%s connected=%d' % (
        text_address(device.bluetooth_address),
        address_kind(device.bluetooth_address, device.bluetooth_address_type),
        pairing.is_paired, pairing.can_pair, name_of(DevicePairingProtectionLevel, pairing.protection_level),
        device.connection_status == BluetoothConnectionStatus.CONNECTED)


def link_parameters(device):
    """The parameters of the link, which Windows chose or accepted from the peripheral."""
    try:
        p = device.get_connection_parameters()
        return 'interval %.2f ms, latency %d, supervision timeout %d ms' % (
            p.connection_interval * 1.25, p.connection_latency, p.link_timeout * 10)
    except Exception as error:      # not on every version of Windows
        return 'not available (%s)' % error


async def paired_devices():
    """The Bluetooth LE devices Windows holds a pairing for, as BluetoothLEDevice objects."""
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    found = []
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is not None:
            found.append(device)
    return found


async def scan_for(name, flag, seconds, address=None):
    """(address, address type) of the first FlySight advertising under `name` and `flag`."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    names = {}
    flags = {}

    def received(_, event):
        found_flag = None
        for item in event.advertisement.manufacturer_data:
            if item.company_id == FLYSIGHT_MFG:
                found_flag = bytes(item.data).hex()
        loop.call_soon_threadsafe(queue.put_nowait, (
            event.bluetooth_address, event.bluetooth_address_type, event.advertisement.local_name,
            found_flag, event.raw_signal_strength_in_dbm))

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    token = watcher.add_received(received)
    watcher.start()
    end = time.time() + seconds
    try:
        while time.time() < end:
            try:
                found, kind, local_name, found_flag, rssi = await asyncio.wait_for(queue.get(), end - time.time())
            except asyncio.TimeoutError:
                break
            if local_name:
                names[found] = local_name
            if found_flag is not None:
                flags[found] = found_flag
            if found not in flags:
                continue
            if address is not None:
                if found != address:
                    continue
            elif names.get(found) != name:
                continue
            if flag is not None and flags[found] != flag:
                continue
            log('scan: %s (%s) name=%r flag=%s rssi=%s' % (
                text_address(found), address_kind(found, kind), names.get(found, ''), flags[found], rssi))
            return found, kind
    finally:
        watcher.stop()
        watcher.remove_received(token)
    return None, None


async def cmd_adapter(args):
    adapter = await BluetoothAdapter.get_default_async()
    if adapter is None:
        print('no Bluetooth adapter')
        return 1
    print('address %s  low energy=%d  central=%d  peripheral=%d  LE secure connections=%d  '
          'extended advertising=%d' % (
              text_address(adapter.bluetooth_address), adapter.is_low_energy_supported,
              adapter.is_central_role_supported, adapter.is_peripheral_role_supported,
              adapter.are_low_energy_secure_connections_supported, adapter.is_extended_advertising_supported))
    return 0


async def cmd_status(args):
    for device in await paired_devices():
        print('name=%r %s id=%s' % (device.name, describe(device), device.device_id))
        device.close()
    return 0


async def cmd_forget(args):
    count = 0
    for device in await paired_devices():
        if device.name == args.name:
            print('removing name=%r %s' % (device.name, describe(device)))
            result = await device.device_information.pairing.unpair_async()
            print('  -> %s' % name_of(DeviceUnpairingResultStatus, result.status))
            count += 1
        device.close()
    print('%d device(s) removed' % count)
    return 0


async def cmd_connect(args):
    adapter = await BluetoothAdapter.get_default_async()
    log('adapter %s' % text_address(adapter.bluetooth_address))

    device = None
    if args.no_scan:
        for candidate in await paired_devices():
            wanted = (candidate.bluetooth_address == parse_address(args.address) if args.address
                      else candidate.name == args.name)
            if wanted and device is None:
                device = candidate
            else:
                candidate.close()
        if device is None:
            log('RESULT: NOT FOUND (Windows holds no pairing for %r)' % (args.address or args.name))
            return 3
        log('known device, no scan: %s' % describe(device))
    else:
        address, kind = await scan_for(args.name, args.flag, args.scan,
                                       parse_address(args.address) if args.address else None)
        if address is None:
            log('RESULT: NOT FOUND (no advert from %r%s within %ss)' % (
                args.address or args.name, '' if args.flag is None else ' with flag ' + args.flag, args.scan))
            return 3
        device = await BluetoothLEDevice.from_bluetooth_address_with_bluetooth_address_type_async(address, kind)
        if device is None:
            log('RESULT: Windows gave no device object for %s' % text_address(address))
            return 1
        log('device object: name=%r %s' % (device.name, describe(device)))
    log('device id %s' % device.device_id)

    loop = asyncio.get_running_loop()
    disconnected = asyncio.Event()
    connected = asyncio.Event()

    def on_connection(sender, _):
        up = sender.connection_status == BluetoothConnectionStatus.CONNECTED
        loop.call_soon_threadsafe(log, '  device: connection status -> %s' % ('CONNECTED' if up else 'DISCONNECTED'))
        loop.call_soon_threadsafe((connected if up else disconnected).set)
        if up:
            loop.call_soon_threadsafe(disconnected.clear)

    def on_services_changed(sender, _):
        loop.call_soon_threadsafe(log, '  device: GATT services changed')

    def on_parameters(sender, _):
        loop.call_soon_threadsafe(log, '  device: link parameters -> %s' % link_parameters(sender))

    device.add_connection_status_changed(on_connection)
    device.add_gatt_services_changed(on_services_changed)
    try:
        device.add_connection_parameters_changed(on_parameters)
    except Exception:
        pass

    session = None
    services = []
    code = 1
    try:
        started = time.time()
        if not args.no_session:
            session = await GattSession.from_device_id_async(device.bluetooth_device_id)

            def on_session(sender, event):
                loop.call_soon_threadsafe(log, '  session: %s (error %s)' % (
                    name_of(GattSessionStatus, event.status), event.error))

            session.add_session_status_changed(on_session)
            session.maintain_connection = True
            log('session: maintain_connection set (can maintain: %d)' % session.can_maintain_connection)

        async def discover():
            result = await device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
            log('services: %s, %d service(s) after %.2fs' % (
                name_of(GattCommunicationStatus, result.status), len(result.services), time.time() - started))
            return result

        result = await discover()
        if result.status != GattCommunicationStatus.SUCCESS:
            # With a session that maintains the connection, Windows keeps trying by itself
            try:
                await asyncio.wait_for(connected.wait(), args.connect_timeout)
                result = await discover()
            except asyncio.TimeoutError:
                pass
        if result.status != GattCommunicationStatus.SUCCESS:
            log('RESULT: CONNECT FAILED after %.1fs' % (time.time() - started))
            return 1
        services = list(result.services)
        log('connected in %.2fs: %s' % (time.time() - started, describe(device)))
        log('link: %s' % link_parameters(device))

        pairing = device.device_information.pairing
        if args.pair != 'none' and not pairing.is_paired:
            level = PROTECTION[args.protection]
            pair_started = time.time()
            if args.pair == 'custom':
                custom = pairing.custom

                def on_pairing_requested(sender, event):
                    loop.call_soon_threadsafe(log, '  pairing requested: kind=%s -> accepted' % name_of(
                        DevicePairingKinds, event.pairing_kind))
                    event.accept()

                custom.add_pairing_requested(on_pairing_requested)
                log('custom pairing (confirm only, protection %s) ...' % args.protection)
                outcome = await custom.pair_with_protection_level_async(DevicePairingKinds.CONFIRM_ONLY, level)
            else:
                log('system pairing (protection %s): Windows may ask the user ...' % args.protection)
                outcome = await pairing.pair_with_protection_level_async(level)
            log('pairing -> %s, protection used %s, after %.2fs' % (
                name_of(DevicePairingResultStatus, outcome.status),
                name_of(DevicePairingProtectionLevel, outcome.protection_level_used), time.time() - pair_started))
            log('after pairing: %s secure connections=%s' % (
                describe(device), device.was_secure_connection_used_for_pairing))
            # Windows gives a paired device new service objects
            await asyncio.sleep(args.settle)
            for service in services:
                service.close()
            result = await discover()
            services = list(result.services)

        if args.no_read:
            code = 0
        else:
            packet_in = None
            packet_out = None
            for service in services:
                if str(service.uuid) != FT_SERVICE:
                    continue
                found = await service.get_characteristics_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
                log('characteristics of the file transfer service: %s, %d' % (
                    name_of(GattCommunicationStatus, found.status), len(found.characteristics)))
                for characteristic in found.characteristics:
                    if str(characteristic.uuid) == FT_PACKET_IN:
                        packet_in = characteristic
                    if str(characteristic.uuid) == FT_PACKET_OUT:
                        packet_out = characteristic
            if packet_in is None:
                log('RESULT: FT_Packet_In not found')
                return 1

            for attempt in range(args.reads):
                log('read of FT_Packet_In ...')
                read_started = time.time()
                value = await packet_in.read_value_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
                if value.status == GattCommunicationStatus.SUCCESS:
                    log('read OK after %.2fs: %d bytes' % (time.time() - read_started, len(bytes(value.value))))
                    code = 0
                    break
                log('read FAILED after %.2fs: %s protocol error=%s' % (
                    time.time() - read_started, name_of(GattCommunicationStatus, value.status),
                    value.protocol_error))
                log('  %s' % describe(device))
                if attempt + 1 < args.reads:
                    await asyncio.sleep(args.read_interval)

            if args.ping and code == 0 and packet_out is not None:
                acknowledged = asyncio.Event()

                def on_value(sender, event):
                    payload = bytes(event.characteristic_value)
                    loop.call_soon_threadsafe(log, '  notification FT_Packet_Out %s' % payload[:20].hex())
                    if payload[:2] == b'\xf1\xfe':
                        loop.call_soon_threadsafe(acknowledged.set)

                packet_out.add_value_changed(on_value)
                written = await packet_out.write_client_characteristic_configuration_descriptor_with_result_async(
                    GattClientCharacteristicConfigurationDescriptorValue.NOTIFY)
                log('notifications of FT_Packet_Out: %s' % name_of(GattCommunicationStatus, written.status))
                ping = Buffer(1)
                ping.length = 1
                with memoryview(ping) as view:
                    view[0] = 0xFE
                sent = await packet_in.write_value_with_result_and_option_async(
                    ping, GattWriteOption.WRITE_WITHOUT_RESPONSE)
                log('ping written: %s' % name_of(GattCommunicationStatus, sent.status))
                try:
                    await asyncio.wait_for(acknowledged.wait(), 5)
                    log('ping acknowledged')
                except asyncio.TimeoutError:
                    log('ping: no acknowledgement')
                    code = 1

        if args.hold:
            try:
                await asyncio.wait_for(disconnected.wait(), args.hold)
                log('the link ended by itself')
            except asyncio.TimeoutError:
                pass
        log('state before closing: %s' % describe(device))
    finally:
        closing = time.time()
        for service in services:
            service.close()
        if session is not None:
            session.maintain_connection = False
            session.close()
        still_up = device.connection_status == BluetoothConnectionStatus.CONNECTED
        device.close()
        if still_up:
            try:
                await asyncio.wait_for(disconnected.wait(), args.close_timeout)
                log('link down %.2fs after closing' % (time.time() - closing))
            except asyncio.TimeoutError:
                log('link still up %.0fs after closing' % args.close_timeout)
    log('RESULT: %s' % ('OK' if code == 0 else 'FAILED'))
    return code


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('adapter')
    p.set_defaults(func=cmd_adapter)
    p = sub.add_parser('status')
    p.set_defaults(func=cmd_status)
    p = sub.add_parser('forget')
    p.add_argument('--name', default='FlySight')
    p.set_defaults(func=cmd_forget)
    p = sub.add_parser('connect')
    p.add_argument('--name', default='FlySight')
    p.add_argument('--address', help='match this address instead of the name')
    p.add_argument('--flag', choices=['00', '01'], help='only an advert with this pairing flag')
    p.add_argument('--pair', choices=['none', 'custom', 'system'], default='none')
    p.add_argument('--protection', choices=sorted(PROTECTION), default='default')
    p.add_argument('--no-scan', action='store_true', help='use the paired device Windows already knows')
    p.add_argument('--no-session', action='store_true', help='no GattSession maintaining the connection')
    p.add_argument('--no-read', action='store_true')
    p.add_argument('--reads', type=int, default=1, help='number of times the read is tried')
    p.add_argument('--read-interval', type=float, default=5)
    p.add_argument('--ping', action='store_true', help='also send a ping and wait for its acknowledgement')
    p.add_argument('--scan', type=float, default=20)
    p.add_argument('--settle', type=float, default=1, help='seconds to wait after a pairing')
    p.add_argument('--hold', type=float, default=0, help='seconds to stay connected after the read')
    p.add_argument('--connect-timeout', type=float, default=30)
    p.add_argument('--close-timeout', type=float, default=20)
    p.set_defaults(func=cmd_connect)
    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == '__main__':
    sys.exit(main())
