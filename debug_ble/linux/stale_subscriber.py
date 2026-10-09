#!/usr/bin/env python3
"""Reproduces what an idle application left behind before the library gave its sessions back:
connects to the bonded FlySight, starts notifications on its characteristics, disconnects, and
stays on the bus. BlueZ then emits every notification twice for whoever connects next.

usage: stale_subscriber.py [seconds to stay] [identifier]
  identifier  the address BlueZ first knew the FlySight by, as fs_probe.py prints it;
              without it, the first bonded FlySight BlueZ lists
"""
import sys, time
import dbus, dbus.mainloop.glib
from gi.repository import GLib

STAY = float(sys.argv[1]) if len(sys.argv) > 1 else 300
IDENTIFIER = sys.argv[2] if len(sys.argv) > 2 else None
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
ctx = GLib.MainLoop().get_context()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.02)


def objects():
    return dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager').GetManagedObjects()


path = next(p for p, i in objects().items()
            if 'org.bluez.Device1' in i and str(i['org.bluez.Device1'].get('Name', '')) == 'FlySight'
            and i['org.bluez.Device1'].get('Paired')
            and (IDENTIFIER is None or p.endswith('/dev_' + IDENTIFIER.replace(':', '_'))))
device = dbus.Interface(bus.get_object('org.bluez', path), 'org.bluez.Device1')
props = dbus.Interface(bus.get_object('org.bluez', path), 'org.freedesktop.DBus.Properties')
device.Connect()
for _ in range(100):
    if props.Get('org.bluez.Device1', 'ServicesResolved'):
        break
    pump(0.1)
started = 0
for p, i in objects().items():
    c = i.get('org.bluez.GattCharacteristic1')
    if p.startswith(path + '/') and c and ('notify' in c['Flags'] or 'indicate' in c['Flags']):
        try:
            dbus.Interface(bus.get_object('org.bluez', p), 'org.bluez.GattCharacteristic1').StartNotify()
            started += 1
        except dbus.DBusException:
            pass
pump(1.0)
device.Disconnect()
print('stale subscriber: %d notification sessions left behind, staying %.0f s' % (started, STAY), flush=True)
pump(STAY)
