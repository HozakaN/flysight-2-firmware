#!/usr/bin/env python3
"""Baseline BLE probe for a FlySight 2, straight on BlueZ (D-Bus), without the SkyGames library.

    bluez_probe.py connect [--name FlySight] [--flag 00|01] [--agent] [--pair] [--scan 20]
                           [--no-read] [--hold 0] [--address AA:BB:..]
    bluez_probe.py status  [--name FlySight]
    bluez_probe.py forget  [--name FlySight]

`connect` scans until the FlySight advertises (optionally with a given pairing flag), connects,
reads FT_Packet_In (which needs an encrypted link, so it triggers pairing on a host that is not
bonded), prints what happened and disconnects. Exit code 0 only if the read succeeded.
"""
import argparse
import sys
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BLUEZ = 'org.bluez'
DEV = 'org.bluez.Device1'
CHR = 'org.bluez.GattCharacteristic1'
PROPS = 'org.freedesktop.DBus.Properties'
OM = 'org.freedesktop.DBus.ObjectManager'
FT_PACKET_IN = '00000002-8e22-4541-9d4c-21edae82ed19'
FLYSIGHT_MFG = 0x09DB
AGENT_PATH = '/fr/hozakan/blelab/agent'

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
loop = GLib.MainLoop()
ctx = loop.get_context()
T0 = time.time()


def log(msg):
    print('%7.2f %s' % (time.time() - T0, msg), flush=True)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.01)


def wait_for(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        ctx.iteration(False)
        time.sleep(0.01)
    return predicate()


def objects():
    return dbus.Interface(bus.get_object(BLUEZ, '/'), OM).GetManagedObjects()


def flag_of(props):
    for key, value in props.get('ManufacturerData', {}).items():
        if int(key) == FLYSIGHT_MFG:
            return ''.join('%02x' % int(b) for b in value)
    return None


def describe(props):
    return 'addr=%s (%s) paired=%d bonded=%d connected=%d resolved=%d flag=%s' % (
        props.get('Address'), props.get('AddressType'), int(props.get('Paired', 0)),
        int(props.get('Bonded', 0)), int(props.get('Connected', 0)),
        int(props.get('ServicesResolved', 0)), flag_of(props))


class Agent(dbus.service.Object):
    """Accepts everything; only there so that BlueZ makes the adapter bondable."""

    @dbus.service.method('org.bluez.Agent1', in_signature='', out_signature='')
    def Release(self):
        log('agent: Release')

    @dbus.service.method('org.bluez.Agent1', in_signature='o', out_signature='')
    def RequestAuthorization(self, device):
        log('agent: RequestAuthorization %s -> accepted' % device)

    @dbus.service.method('org.bluez.Agent1', in_signature='ou', out_signature='')
    def RequestConfirmation(self, device, passkey):
        log('agent: RequestConfirmation %s %06d -> accepted' % (device, passkey))

    @dbus.service.method('org.bluez.Agent1', in_signature='os', out_signature='')
    def AuthorizeService(self, device, uuid):
        log('agent: AuthorizeService %s %s -> accepted' % (device, uuid))

    @dbus.service.method('org.bluez.Agent1', in_signature='', out_signature='')
    def Cancel(self):
        log('agent: Cancel')


def find_known(name, address=None):
    for path, ifaces in objects().items():
        props = ifaces.get(DEV)
        if not props:
            continue
        if address and str(props.get('Address', '')).upper() == address.upper():
            return path, props
        if not address and str(props.get('Name', '')) == name and props.get('Paired'):
            return path, props
    return None, None


def scan_for(name, flag, seconds, address=None):
    """Path of the first device advertising under `name` (and `flag`) during this scan."""
    adapter = dbus.Interface(bus.get_object(BLUEZ, '/org/bluez/hci0'), 'org.bluez.Adapter1')
    found = []

    def consider(path, why):
        try:
            props = dbus.Interface(bus.get_object(BLUEZ, path), PROPS).GetAll(DEV)
        except dbus.DBusException:
            return
        if address:
            if str(props.get('Address', '')).upper() != address.upper():
                return
        elif str(props.get('Name', '')) != name:
            return
        if flag is not None and flag_of(props) != flag:
            return
        if not found:
            log('scan: %s %s rssi=%s (%s)' % (path.split('/')[-1], describe(props), props.get('RSSI', '?'), why))
        found.append(path)

    def added(path, ifaces):
        if DEV in ifaces:
            consider(path, 'new')

    def changed(iface, ch, inv, path=None):
        if iface == DEV and ('RSSI' in ch or 'ManufacturerData' in ch):
            consider(path, 'advert')

    m1 = bus.add_signal_receiver(added, dbus_interface=OM, signal_name='InterfacesAdded')
    m2 = bus.add_signal_receiver(changed, dbus_interface=PROPS, signal_name='PropertiesChanged', path_keyword='path')
    adapter.SetDiscoveryFilter({'Transport': 'le', 'DuplicateData': True})
    adapter.StartDiscovery()
    wait_for(lambda: found, seconds)
    try:
        adapter.StopDiscovery()
    except dbus.DBusException:
        pass
    m1.remove()
    m2.remove()
    return found[0] if found else None


def cmd_connect(args):
    agent = None
    if args.agent:
        agent = Agent(bus, AGENT_PATH)
        dbus.Interface(bus.get_object(BLUEZ, '/org/bluez'), 'org.bluez.AgentManager1').RegisterAgent(
            AGENT_PATH, 'NoInputNoOutput')
        pump(0.3)
    pairable = bool(dbus.Interface(bus.get_object(BLUEZ, '/org/bluez/hci0'), PROPS).Get('org.bluez.Adapter1', 'Pairable'))
    log('adapter Pairable(bondable)=%s agent=%s' % (pairable, 'registered' if agent else 'none'))

    path = None
    if args.no_scan:
        path, props = find_known(args.name, args.address)
        if path:
            log('known device %s %s' % (path.split('/')[-1], describe(props)))
    if not path:
        path = scan_for(args.name, args.flag, args.scan, args.address)
    if not path:
        log('RESULT: NOT FOUND (no advert from %r%s within %ss)' % (
            args.name, '' if args.flag is None else ' with flag ' + args.flag, args.scan))
        return 3

    device = dbus.Interface(bus.get_object(BLUEZ, path), DEV)
    dprops = dbus.Interface(bus.get_object(BLUEZ, path), PROPS)

    def on_change(iface, ch, inv, path=None):
        if iface == DEV:
            keep = {k: (str(v) if k in ('Address', 'AddressType') else int(v))
                    for k, v in ch.items()
                    if k in ('Connected', 'Paired', 'Bonded', 'ServicesResolved', 'Address', 'AddressType')}
            if keep:
                log('  device: %s' % keep)

    bus.add_signal_receiver(on_change, dbus_interface=PROPS, signal_name='PropertiesChanged', path=path, path_keyword='path')

    result = {}

    log('Connect() ...')
    t = time.time()
    device.Connect(reply_handler=lambda: result.setdefault('connect', 'ok'),
                   error_handler=lambda e: result.setdefault('connect', e), timeout=args.connect_timeout)
    wait_for(lambda: 'connect' in result, args.connect_timeout + 2)
    if result.get('connect') != 'ok':
        log('RESULT: CONNECT FAILED after %.1fs: %s' % (time.time() - t, result.get('connect', 'no answer')))
        try:
            device.Disconnect()
        except dbus.DBusException:
            pass
        return 1
    log('connected in %.2fs: %s' % (time.time() - t, describe(dprops.GetAll(DEV))))

    code = 0
    try:
        if args.pair and not dprops.Get(DEV, 'Paired'):
            log('Pair() ...')
            t = time.time()
            device.Pair(reply_handler=lambda: result.setdefault('pair', 'ok'),
                        error_handler=lambda e: result.setdefault('pair', e), timeout=40)
            wait_for(lambda: 'pair' in result, 42)
            log('Pair() -> %s after %.2fs' % (result.get('pair', 'no answer'), time.time() - t))

        if not wait_for(lambda: dprops.Get(DEV, 'ServicesResolved'), 15):
            log('RESULT: services not resolved (%s)' % describe(dprops.GetAll(DEV)))
            return 1
        log('services resolved: %s' % describe(dprops.GetAll(DEV)))

        if not args.no_read:
            char_path = None
            for p, ifaces in objects().items():
                if p.startswith(path + '/') and str(ifaces.get(CHR, {}).get('UUID', '')) == FT_PACKET_IN:
                    char_path = p
            if not char_path:
                log('RESULT: FT_Packet_In not found')
                return 1
            chrc = dbus.Interface(bus.get_object(BLUEZ, char_path), CHR)
            log('ReadValue(FT_Packet_In) ...')
            t = time.time()
            chrc.ReadValue({}, reply_handler=lambda v: result.setdefault('read', bytes(v)),
                           error_handler=lambda e: result.setdefault('read', e), timeout=40)
            wait_for(lambda: 'read' in result, 42)
            value = result.get('read', 'no answer')
            if isinstance(value, bytes):
                log('read OK after %.2fs: %d bytes' % (time.time() - t, len(value)))
            else:
                log('read FAILED after %.2fs: %s' % (time.time() - t, value))
                code = 1
        pump(args.hold)
        try:
            log('state before disconnect: %s' % describe(dprops.GetAll(DEV)))
        except dbus.DBusException as e:
            log('state before disconnect: device gone (%s)' % e.get_dbus_name())
    finally:
        try:
            device.Disconnect()
            pump(0.8)
        except dbus.DBusException:
            pass
    try:
        log('state after disconnect: %s' % describe(dprops.GetAll(DEV)))
    except dbus.DBusException as e:
        log('state after disconnect: device object removed by BlueZ')
    log('RESULT: %s' % ('OK' if code == 0 else 'FAILED'))
    return code


def cmd_status(args):
    for path, ifaces in sorted(objects().items()):
        props = ifaces.get(DEV)
        if props and (str(props.get('Name', '')).startswith('FlySight') or flag_of(props) is not None):
            print('%s name=%r %s' % (path.split('/')[-1], str(props.get('Name', '')), describe(props)))
    return 0


def cmd_forget(args):
    adapter = dbus.Interface(bus.get_object(BLUEZ, '/org/bluez/hci0'), 'org.bluez.Adapter1')
    n = 0
    for path, ifaces in objects().items():
        props = ifaces.get(DEV)
        if props and str(props.get('Name', '')) == args.name:
            print('removing %s %s' % (path.split('/')[-1], describe(props)))
            adapter.RemoveDevice(path)
            n += 1
    print('%d device(s) removed' % n)
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('connect')
    p.add_argument('--name', default='FlySight')
    p.add_argument('--address', help='match this address instead of the name')
    p.add_argument('--flag', choices=['00', '01'], help='only an advert with this pairing flag')
    p.add_argument('--agent', action='store_true', help='register a BlueZ agent first (makes the adapter bondable)')
    p.add_argument('--pair', action='store_true', help='call Pair() after connecting if the device is not paired')
    p.add_argument('--no-read', action='store_true')
    p.add_argument('--no-scan', action='store_true', help='connect to the paired device BlueZ already knows, without scanning')
    p.add_argument('--scan', type=float, default=20)
    p.add_argument('--hold', type=float, default=0, help='seconds to stay connected after the read')
    p.add_argument('--connect-timeout', type=float, default=30)
    p.set_defaults(func=cmd_connect)
    p = sub.add_parser('status')
    p.set_defaults(func=cmd_status)
    p = sub.add_parser('forget')
    p.add_argument('--name', default='FlySight')
    p.set_defaults(func=cmd_forget)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
