#!/usr/bin/env python3
"""Drive libflysight_ble.so (the native BLE layer of SkyGames' FlySightApi) the way the app does.

    fs_probe.py scan [--seconds 10]
    fs_probe.py connect [--name FlySight] [--flag 00|01] [--id ADDRESS] [--stay 0] [--scan 20]

`connect` scans for the FlySight (or takes `--id`, an identifier from an earlier scan, with no
scan at all, as the app does when it starts), connects, discovers the services, subscribes to
the notifications the app subscribes to, reads FT_Packet_In (needs an encrypted link) and sends
a ping, which the FlySight acknowledges with a notification. Exit code 0 only if all of it worked.
"""
import argparse
import ctypes
import os
import sys
import threading
import time

LIB = os.environ.get('FLYSIGHT_BLE_LIB') or os.path.expanduser(
    '~/AndroidStudioProjects/skygames/Libs/FlySightApi/core/src/nativeInterop/build/linux/libflysight_ble.so')

FT_SERVICE = '00000000-cc7a-482a-984a-7f2ed5b3e58f'
CRS_TX = '00000001-8e22-4541-9d4c-21edae82ed19'      # FT_Packet_Out, notify
CRS_RX = '00000002-8e22-4541-9d4c-21edae82ed19'      # FT_Packet_In, read / write without response
NAMES = {
    CRS_TX: 'FT_Packet_Out', CRS_RX: 'FT_Packet_In',
    '00000000-8e22-4541-9d4c-21edae82ed19': 'SD_GNSS_Measurement',
    '00000006-8e22-4541-9d4c-21edae82ed19': 'SD_Control_Point',
    '00000003-8e22-4541-9d4c-21edae82ed19': 'SP_Control_Point',
    '00000004-8e22-4541-9d4c-21edae82ed19': 'SP_Result',
    '00000005-8e22-4541-9d4c-21edae82ed19': 'DS_Mode',
    '00002a19-0000-1000-8000-00805f9b34fb': 'Battery_Level',
}
# What BleDeviceSocket.doDiscoverGattServices subscribes to
SUBSCRIBED = [CRS_TX, '00000000-8e22-4541-9d4c-21edae82ed19', '00000004-8e22-4541-9d4c-21edae82ed19',
              '00000005-8e22-4541-9d4c-21edae82ed19', '00000006-8e22-4541-9d4c-21edae82ed19',
              '00002a19-0000-1000-8000-00805f9b34fb']
PROPS = [(0x02, 'read'), (0x04, 'write-no-rsp'), (0x08, 'write'), (0x10, 'notify'), (0x20, 'indicate')]


class BleDevice(ctypes.Structure):
    _fields_ = [('address', ctypes.c_char * 40), ('name', ctypes.c_char * 256), ('rssi', ctypes.c_int32),
                ('manufacturer_data', ctypes.c_uint8 * 512), ('manufacturer_data_len', ctypes.c_size_t),
                ('manufacturer_id', ctypes.c_char * 16)]


SCAN_CB = ctypes.CFUNCTYPE(None, ctypes.POINTER(BleDevice), ctypes.c_void_p)
CONNECT_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
DISCONNECT_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
CHAR_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_int,
                           ctypes.c_void_p)
DISCOVERY_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)

lib = ctypes.CDLL(LIB)
lib.ble_get_error_string.restype = ctypes.c_char_p
lib.ble_connect.argtypes = [ctypes.c_char_p, CONNECT_CB, ctypes.c_void_p]
lib.ble_start_scan.argtypes = [SCAN_CB, ctypes.c_void_p]
lib.ble_disconnect.argtypes = [ctypes.c_int, DISCONNECT_CB, ctypes.c_void_p]
lib.ble_set_disconnect_callback.argtypes = [ctypes.c_int, DISCONNECT_CB, ctypes.c_void_p]
lib.ble_discover_services_async.argtypes = [ctypes.c_int, DISCOVERY_CB, ctypes.c_void_p]
lib.ble_get_service_uuid.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
lib.ble_get_characteristic_uuid.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
lib.ble_read_characteristic.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, CHAR_CB, ctypes.c_void_p]
lib.ble_write_characteristic_no_response.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                                                     ctypes.c_char_p, ctypes.c_size_t]
lib.ble_enable_notifications.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, CHAR_CB, ctypes.c_void_p]

T0 = time.time()
keep = []                 # ctypes callbacks must outlive the native calls that use them
lock = threading.Lock()


def log(msg):
    with lock:
        print('%7.2f %s' % (time.time() - T0, msg), flush=True)


def err(code):
    return '%d (%s)' % (code, lib.ble_get_error_string(code).decode())


def scan(name, flag, seconds, verbose=False):
    """(address, name, flag) of the first matching FlySight heard during this scan."""
    found = threading.Event()
    result = {}
    seen = {}

    def on_device(dev, _):
        d = dev.contents
        mfg = d.manufacturer_id.decode()
        if mfg not in ('0xDB09', '0x09DB'):
            return
        dname = d.name.decode(errors='replace')
        dflag = bytes(d.manufacturer_data[:d.manufacturer_data_len]).hex()
        address = d.address.decode()
        key = (address, dname, dflag)
        if verbose and seen.get(address) != key:
            log('scan: %-18s %-20r flag=%s rssi=%d' % (address, dname, dflag, d.rssi))
        seen[address] = key
        if (name is None or dname == name) and (flag is None or dflag == flag) and not found.is_set():
            result.update(address=address, name=dname, flag=dflag, rssi=d.rssi)
            found.set()

    cb = SCAN_CB(on_device)
    keep.append(cb)
    rc = lib.ble_start_scan(cb, None)
    if rc != 0:
        log('ble_start_scan -> %s' % err(rc))
        return None
    if verbose:
        time.sleep(seconds)
    else:
        found.wait(seconds)
    lib.ble_stop_scan()
    if verbose:
        log('scan stopped: %d FlySight(s) advertising' % len(seen))
    return result or None


def cmd_scan(args):
    scan(None, None, args.seconds, verbose=True)
    return 0


def cmd_connect(args):
    address = args.id
    if not address:
        hit = scan(args.name, args.flag, args.scan)
        if not hit:
            log('RESULT: NOT FOUND (no advert from %r%s within %ss)' % (
                args.name, '' if args.flag is None else ' with flag ' + args.flag, args.scan))
            return 3
        log('scan: found %(name)r as %(address)s flag=%(flag)s rssi=%(rssi)d' % hit)
        address = hit['address']
    else:
        log('no scan: connecting to the saved identifier %s' % address)

    events = {'connect': threading.Event(), 'disconnect': threading.Event(), 'discovery': threading.Event(),
              'read': threading.Event(), 'ack': threading.Event()}
    state = {'notifications': 0}

    def on_connect(handle, status, _):
        state['connect_status'] = status
        log('connect callback: handle=%d status=%s' % (handle, err(status)))
        events['connect'].set()

    def on_disconnect(handle, _):
        log('disconnect callback: handle=%d' % handle)
        events['disconnect'].set()

    def on_discovery(handle, status, _):
        state['discovery_status'] = status
        log('service discovery callback: status=%s' % err(status))
        events['discovery'].set()

    def on_read(handle, data, length, status, _):
        state['read_status'] = status
        state['read_len'] = length
        log('read callback: status=%s len=%d' % (err(status), length))
        events['read'].set()

    def make_notify(uuid):
        def on_notify(handle, data, length, status, _):
            payload = bytes(data[:length]) if length else b''
            state['notifications'] += 1
            if state['notifications'] <= 12:
                log('notification %-20s %s' % (NAMES.get(uuid, uuid), payload[:20].hex()))
            if uuid == CRS_TX and payload[:2] == b'\xf1\xfe':
                events['ack'].set()
        return on_notify

    cbs = [CONNECT_CB(on_connect), DISCONNECT_CB(on_disconnect), DISCOVERY_CB(on_discovery), CHAR_CB(on_read)]
    keep.extend(cbs)
    connect_cb, disconnect_cb, discovery_cb, read_cb = cbs

    t = time.time()
    handle = lib.ble_connect(address.encode(), connect_cb, None)
    if handle < 0:
        log('RESULT: ble_connect refused: %s' % err(handle))
        return 1
    lib.ble_set_disconnect_callback(handle, disconnect_cb, None)
    if not events['connect'].wait(args.connect_timeout + 5):
        log('RESULT: no connect callback within %ss' % (args.connect_timeout + 5))
        lib.ble_disconnect(handle, disconnect_cb, None)
        return 1
    if state['connect_status'] != 0:
        log('RESULT: CONNECT FAILED after %.1fs' % (time.time() - t))
        return 1
    log('connected in %.2fs' % (time.time() - t))

    ok = True
    try:
        time.sleep(0.5)       # BleDeviceSocket waits that long before discovering
        rc = lib.ble_discover_services_async(handle, discovery_cb, None)
        if rc != 0 or not events['discovery'].wait(12) or state['discovery_status'] != 0:
            log('service discovery failed (%s)' % err(rc))
            return 1

        table = {}
        for si in range(lib.ble_get_service_count(handle)):
            buf = ctypes.create_string_buffer(37)
            lib.ble_get_service_uuid(handle, si, buf, 37)
            service = buf.value.decode()
            for ci in range(lib.ble_get_characteristic_count(handle, si)):
                lib.ble_get_characteristic_uuid(handle, si, ci, buf, 37)
                uuid = buf.value.decode()
                props = lib.ble_get_characteristic_properties(handle, si, ci)
                table[uuid] = service
                if args.verbose:
                    log('  %s / %-22s %s' % (service[:8], NAMES.get(uuid, uuid[:8]),
                                             ','.join(n for b, n in PROPS if props & b)))
        log('%d characteristics in %d services' % (len(table), lib.ble_get_service_count(handle)))

        for uuid in SUBSCRIBED:
            if uuid not in table:
                log('  %s: not exposed' % NAMES.get(uuid, uuid))
                continue
            cb = CHAR_CB(make_notify(uuid))
            keep.append(cb)
            rc = lib.ble_enable_notifications(handle, table[uuid].encode(), uuid.encode(), cb, None)
            if rc != 0:
                log('  enable notifications %s -> %s' % (NAMES.get(uuid, uuid), err(rc)))
                ok = False

        rc = lib.ble_read_characteristic(handle, FT_SERVICE.encode(), CRS_RX.encode(), read_cb, None)
        if rc != 0 or not events['read'].wait(40) or state['read_status'] != 0:
            log('read of FT_Packet_In failed (%s)' % err(rc))
            ok = False

        time.sleep(0.3)
        state['last_write'] = time.time()
        rc = lib.ble_write_characteristic_no_response(handle, FT_SERVICE.encode(), CRS_RX.encode(), b'\xfe', 1)
        if rc != 0 or not events['ack'].wait(5):
            log('ping: no acknowledgement (%s)' % err(rc))
            ok = False
        else:
            log('ping acknowledged')

        end = time.time() + args.stay
        while time.time() < end and not events['disconnect'].is_set():
            if args.no_ping:
                events['disconnect'].wait(max(0.1, end - time.time()))
                continue
            time.sleep(min(14, max(0.1, end - time.time())))      # the app pings every 14 s
            if time.time() < end and not events['disconnect'].is_set():
                events['ack'].clear()
                lib.ble_write_characteristic_no_response(handle, FT_SERVICE.encode(), CRS_RX.encode(), b'\xfe', 1)
                log('ping %s' % ('acknowledged' if events['ack'].wait(5) else 'NOT acknowledged'))
        if events['disconnect'].is_set():
            log('link dropped by itself %.1fs after the last write' % (time.time() - state.get('last_write', T0)))
            ok = args.expect_drop
        elif args.expect_drop:
            log('the link was expected to drop and did not')
            ok = False
    finally:
        if not events['disconnect'].is_set():
            rc = lib.ble_disconnect(handle, disconnect_cb, None)
            if rc == 0 and not events['disconnect'].wait(8):
                log('no disconnect callback')
                ok = False
    log('%d notification(s) received' % state['notifications'])
    log('RESULT: %s' % ('OK' if ok else 'FAILED'))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('scan')
    p.add_argument('--seconds', type=float, default=10)
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser('connect')
    p.add_argument('--name', default='FlySight')
    p.add_argument('--flag', choices=['00', '01'])
    p.add_argument('--id', help='connect to this identifier without scanning')
    p.add_argument('--scan', type=float, default=20)
    p.add_argument('--stay', type=float, default=0, help='seconds to stay connected, pinging like the app')
    p.add_argument('--connect-timeout', type=float, default=30)
    p.add_argument('--no-ping', action='store_true', help='do not ping while staying connected')
    p.add_argument('--expect-drop', action='store_true', help='the link is expected to end by itself')
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_connect)
    args = parser.parse_args()

    rc = lib.ble_init()
    if rc != 0:
        log('ble_init -> %s' % err(rc))
        return 2
    try:
        return args.func(args)
    finally:
        lib.ble_cleanup()


if __name__ == '__main__':
    sys.exit(main())
