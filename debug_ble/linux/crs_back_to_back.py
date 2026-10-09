#!/usr/bin/env python3
"""Reads a file from a bonded FlySight and writes a ping right behind the last acknowledgement,
several times, and counts the pings that got an answer.

A firmware whose FS_CRS_State_Read() empties the receive queue in one pass takes that ping out of
the queue while it is still reading, and drops it. The ping does not use the microSD card, so
nothing else than that is measured.

usage: crs_back_to_back.py <identifier> <count> [file]
  identifier  the address BlueZ first knew the FlySight by, as fs_probe.py prints it
"""
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

FT_OUT = '00000001-8e22-4541-9d4c-21edae82ed19'
FT_IN = '00000002-8e22-4541-9d4c-21edae82ed19'
DEV = 'org.bluez.Device1'
CHR = 'org.bluez.GattCharacteristic1'
PING = bytes([0xfe])

IDENTIFIER, COUNT = sys.argv[1], int(sys.argv[2])
FILE = sys.argv[3] if len(sys.argv) > 3 else '/FLYSIGHT.TXT'
READ = bytes([0x02]) + bytes(8) + FILE.encode()

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
ctx = GLib.MainLoop().get_context()
received = []


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        while ctx.iteration(False):
            pass
        if until and until():
            return True
        time.sleep(0.001)
    return False


def objects():
    return dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager').GetManagedObjects()


path = next((p for p in objects() if p.endswith('/dev_' + IDENTIFIER.replace(':', '_'))), None)
if not path:
    sys.exit('BlueZ does not know %s' % IDENTIFIER)
device = dbus.Interface(bus.get_object('org.bluez', path), DEV)
props = dbus.Interface(bus.get_object('org.bluez', path), 'org.freedesktop.DBus.Properties')
for attempt in range(3):
    try:
        device.Connect(timeout=30)
        break
    except dbus.DBusException as e:
        if attempt == 2:
            sys.exit('connect failed: %s' % e.get_dbus_message())
        time.sleep(2)
if not pump(15, lambda: bool(props.Get(DEV, 'ServicesResolved'))):
    sys.exit('services not resolved')
chars = {str(i[CHR]['UUID']): p for p, i in objects().items() if p.startswith(path + '/') and CHR in i}
out_path = chars[FT_OUT]
rx = dbus.Interface(bus.get_object('org.bluez', chars[FT_IN]), CHR)
tx = dbus.Interface(bus.get_object('org.bluez', out_path), CHR)


def on_change(iface, changed, invalidated, path=None):
    if path == out_path and 'Value' in changed:
        received.append(bytes(changed['Value']))


bus.add_signal_receiver(on_change, dbus_interface='org.freedesktop.DBus.Properties',
                        signal_name='PropertiesChanged', path_keyword='path')
tx.StartNotify()
pump(0.5)


def write(data):
    rx.WriteValue(dbus.Array(data, signature='y'), {'type': 'command'})


answered = lost = failed = 0
for n in range(COUNT):
    del received[:]
    seen = 0
    expected = 0
    last = None
    write(READ)
    deadline = time.time() + 6
    while last is None and time.time() < deadline:
        pump(0.01)
        while seen < len(received):
            packet = received[seen]
            seen += 1
            if packet[:1] != b'\x10' or len(packet) < 2 or packet[1] != (expected & 0xff):
                continue                      # an answer to the request, or a packet sent again
            if len(packet) == 2:
                last = packet[1]              # the empty packet that ends the file
                break
            write(bytes([0x12, packet[1]]))
            expected += 1
    if last is None:
        failed += 1
        pump(1.0)
        continue
    mark = len(received)
    write(bytes([0x12, last]))                # the last acknowledgement...
    write(PING)                               # ...and a command right behind it
    if pump(1.5, lambda: b'\xf1\xfe' in received[mark:]):
        answered += 1
    else:
        lost += 1
    pump(0.2)

del received[:]
write(PING)
alive = pump(3, lambda: b'\xf1\xfe' in received)
try:
    tx.StopNotify()
    device.Disconnect()
except dbus.DBusException:
    pass
pump(0.5)
print('RESULT file=%s reads=%d ping right behind the last acknowledgement: answered=%d dropped=%d; reads that did not finish=%d; alive at the end=%s'
      % (FILE, COUNT, answered, lost, failed, alive))
