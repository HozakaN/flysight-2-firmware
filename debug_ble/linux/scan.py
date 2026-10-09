#!/usr/bin/env python3
"""Scan for FlySight 2 adverts through BlueZ (D-Bus) and print each one: address, name, pairing
flag (00 or 01), RSSI, and whether BlueZ holds a bond with it.

usage: scan.py [seconds]
"""
import sys, time
import dbus, dbus.mainloop.glib
from gi.repository import GLib

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 15
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
om = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
adapter = dbus.Interface(bus.get_object('org.bluez', '/org/bluez/hci0'), 'org.bluez.Adapter1')
t0 = time.time()
seen = {}

def is_fs(props):
    md = props.get('ManufacturerData', {})
    name = str(props.get('Name', '') or props.get('Alias', ''))
    return 0x09DB in [int(k) for k in md.keys()] or 'FlySight' in name

def show(path, props, why):
    md = props.get('ManufacturerData', {})
    flag = None
    for k, v in md.items():
        if int(k) == 0x09DB:
            flag = ''.join('%02x' % int(b) for b in v)
    key = (str(props.get('Address', '')), flag)
    if seen.get(path) == key and why == 'chg':
        return
    seen[path] = key
    print('%6.1f %s %s type=%s name=%r flag=%s rssi=%s paired=%s bonded=%s connected=%s' % (
        time.time() - t0, why, props.get('Address', '?'), props.get('AddressType', '?'),
        str(props.get('Name', props.get('Alias', ''))), flag, props.get('RSSI', '?'),
        int(props.get('Paired', 0)), int(props.get('Bonded', 0)), int(props.get('Connected', 0))), flush=True)

def added(path, ifaces):
    p = ifaces.get('org.bluez.Device1')
    if p and is_fs(p):
        show(path, p, 'new')

def changed(iface, ch, inv, path=None):
    if iface != 'org.bluez.Device1':
        return
    if not ('ManufacturerData' in ch or 'RSSI' in ch or 'Address' in ch):
        return
    p = dbus.Interface(bus.get_object('org.bluez', path), 'org.freedesktop.DBus.Properties').GetAll('org.bluez.Device1')
    if is_fs(p):
        show(path, p, 'chg' if 'ManufacturerData' not in ch else 'adv')

bus.add_signal_receiver(added, dbus_interface='org.freedesktop.DBus.ObjectManager', signal_name='InterfacesAdded')
bus.add_signal_receiver(changed, dbus_interface='org.freedesktop.DBus.Properties', signal_name='PropertiesChanged', path_keyword='path')
for path, ifaces in om.GetManagedObjects().items():
    added(path, ifaces)
adapter.SetDiscoveryFilter({'Transport': 'le', 'DuplicateData': True})
adapter.StartDiscovery()
loop = GLib.MainLoop()
GLib.timeout_add(int(DUR * 1000), loop.quit)
loop.run()
try:
    adapter.StopDiscovery()
except Exception:
    pass
print('scan done, %d FlySight object(s)' % len(seen))
