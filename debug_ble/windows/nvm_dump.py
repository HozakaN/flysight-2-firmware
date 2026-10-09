#!/usr/bin/env python3
"""Read the non-volatile memory of the BLE stack of a FlySight built with nvm-mirror.patch.

usage: nvm_dump.py --elf BUILD.elf [--raw] [--save FILE] [--attributes]
       nvm_dump.py --file FILE [--raw] [--attributes]
       nvm_dump.py --elf BUILD.elf --restore FILE
       nvm_dump.py --elf BUILD.elf --rename OLD NEW

That build makes the stack keep its bonds and GATT records in RAM (symbol ble_nvm_mirror, 2028
bytes), where the ST-Link can read them while the firmware runs. A reset erases that RAM:
--restore resets the board and puts a saved content back before the stack starts.

The memory is a list of records, each a 4-byte header (length of the data, kind, state) followed
by the data, padded to a multiple of 4 bytes:
  kind 0  security record: the keys of one bonded device (80 bytes)
  kind 1  GATT record of one bonded device. With SHCI_C2_BLE_INIT_OPTIONS_FULL_GATTDB_NVM, every
          attribute of the GATT database (handle and UUID) and the value of each Client
          Characteristic Configuration descriptor. With ..._REDUC_GATTDB_NVM, a 16-byte hash of
          the database and the value of each of those descriptors.
The keys are not printed. --raw prints every byte, keys included.

--rename does the same with the address OLD replaced by NEW in the records of that device. To
the stack it is then another bonded device, and the host that was bonded can pair again as a new
one: one host stands for several.
"""
import argparse
import os
import struct
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Scripts', 'cli'))
from ble_trace import elf_symbols  # noqa: E402
from vbus import connect  # noqa: E402

SIZE = 507 * 4      # BLE_NVM_SRAM_SIZE words
HEADER = 4
KINDS = {0: 'security', 1: 'GATT'}
CCCD = b'\x02\x29'


def target():
    return connect(SimpleNamespace(openocd=os.environ.get('OPENOCD'), programmer=None, sn=None, swd_timeout=30))


def mirror_address(elf):
    return elf_symbols(elf, prefix='ble_nvm_mirror')['ble_nvm_mirror']


def text(address):
    return ':'.join('%02X' % b for b in reversed(address))


def parse_address(value):
    data = bytes(int(part, 16) for part in value.split(':'))
    if len(data) != 6:
        raise SystemExit('not an address: %s' % value)
    return bytes(reversed(data))


def records(data):
    """(offset, length, kind, state, data) of each record, up to the first empty header."""
    offset = 0
    while offset + HEADER <= len(data):
        length, kind, state = struct.unpack_from('<HBB', data, offset)
        if length == 0 or length == 0xFFFF or offset + HEADER + length > len(data):
            break
        yield offset, length, kind, state, data[offset + HEADER:offset + HEADER + length]
        offset += HEADER + (length + 3) // 4 * 4


def attributes(body):
    """(handle, uuid, cccd value or None) of each attribute of a full GATT record; an empty list
    for a record that is not a full one: the handles must grow, up to the padding at the end."""
    size, = struct.unpack_from('<H', body, 8)
    position, end = 12, min(12 + size, len(body))
    found = []
    while position + 5 <= end:
        handle, kind = struct.unpack_from('<HB', body, position)
        width = {1: 2, 2: 16}.get(kind)
        if width is None or position + 3 + width > end or handle <= (found[-1][0] if found else 0):
            return []
        uuid = body[position + 3:position + 3 + width]
        position += 3 + width
        value = None
        if uuid == CCCD:
            value, = struct.unpack_from('<H', body, position)
            position += 2
        found.append((handle, uuid, value))
    return found if end - position < 4 else []


def descriptors(body):
    """(handle, value) of each descriptor of a reduced GATT record, after the 16-byte hash."""
    size, = struct.unpack_from('<H', body, 8)
    for position in range(12 + 16, min(12 + size, len(body)) - 2, 3):
        yield struct.unpack_from('<HB', body, position)


def describe(data, show_attributes=False):
    used = 0
    bonds = {}
    for offset, length, kind, state, body in records(data):
        used = offset + HEADER + (length + 3) // 4 * 4
        name = KINDS.get(kind, 'kind %d' % kind)
        line = '%4d  %-8s %4d + %d bytes  state %d' % (offset, name, length, HEADER, state)
        if kind == 0:
            address = body[62:68]
            line += '  device %s' % text(address)
            if state:
                bonds.setdefault(address, [0, 0])[0] += 1
        elif kind == 1:
            address = body[1:7]
            found = list(attributes(body))
            if found:
                wide = sum(1 for _, uuid, _ in found if len(uuid) == 16)
                written = ['%04x=%d' % (handle, value) for handle, _, value in found if value]
                line += '  device %s  full: %d attributes (%d with a 128-bit UUID), descriptors set: %s' % (
                    text(address), len(found), wide, ' '.join(written) or 'none')
            else:
                found = list(descriptors(body))
                written = ['%04x=%d' % (handle, value) for handle, value in found if value]
                line += '  device %s  reduced: a hash and %d descriptors, set: %s' % (
                    text(address), len(found), ' '.join(written) or 'none')
            if state:
                bonds.setdefault(address, [0, 0])[1] += 1
        print(line)
        if show_attributes and kind == 1:
            for handle, uuid, value in attributes(body):
                print('        %04x  %s%s' % (handle, uuid[::-1].hex(), '' if value is None else '  value %d' % value))
    print('%d of %d bytes used, %d free; %d device(s) with a security record in state 1' % (
        used, len(data), len(data) - used, sum(1 for counts in bonds.values() if counts[0])))
    return used


def hexdump(data):
    last, skipped = None, False
    for offset in range(0, len(data), 16):
        row = data[offset:offset + 16]
        if row == last:
            skipped = True
            continue
        if skipped:
            print('*')
            skipped = False
        print('%04x  %-47s' % (offset, ' '.join('%02x' % b for b in row)))
        last = row


def restore(elf, data, probe=None):
    """Reset the board and put data in the memory before the firmware gives it to the stack."""
    config = elf_symbols(elf, prefix='SHCI_C2_Config')['SHCI_C2_Config'] & ~1
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'nvm.bin')
        with open(path, 'wb') as f:
            f.write(data)
        (probe or target())._run(
            'reset halt', 'bp 0x%08X 2 hw' % config, 'resume', 'wait_halt 10000',
            'load_image {%s} 0x%08X bin' % (path.replace(os.sep, '/'), mirror_address(elf)),
            'rbp 0x%08X' % config, 'resume')


def rename(elf, old, new):
    probe = target()
    data = bytearray(probe.read_bytes(mirror_address(elf), SIZE))
    count = 0
    for offset, length, kind, state, body in records(bytes(data)):
        start = {0: 62, 1: 1}.get(kind)
        if start is not None and body[start:start + 6] == old:
            data[offset + HEADER + start:offset + HEADER + start + 6] = new
            count += 1
    if not count:
        raise SystemExit('no record of %s' % text(old))
    restore(elf, bytes(data), probe)
    print('%s is now %s in %d record(s); the board was reset' % (text(old), text(new), count))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--elf')
    parser.add_argument('--file')
    parser.add_argument('--raw', action='store_true')
    parser.add_argument('--save')
    parser.add_argument('--attributes', action='store_true')
    parser.add_argument('--rename', nargs=2, metavar=('OLD', 'NEW'))
    parser.add_argument('--restore', metavar='FILE')
    args = parser.parse_args()
    if args.restore:
        with open(args.restore, 'rb') as f:
            restore(args.elf, f.read())
        print('memory restored; the board was reset')
        return 0
    if args.rename:
        rename(args.elf, parse_address(args.rename[0]), parse_address(args.rename[1]))
        return 0
    if args.file:
        with open(args.file, 'rb') as f:
            data = f.read()
    else:
        data = target().read_bytes(mirror_address(args.elf), SIZE)
    if args.save:
        with open(args.save, 'wb') as f:
            f.write(data)
    describe(data, args.attributes)
    if args.raw:
        hexdump(data)
    return 0


if __name__ == '__main__':
    sys.exit(main())
