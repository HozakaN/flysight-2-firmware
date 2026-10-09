#!/usr/bin/env python3
"""Reads the result of the power-gap experiment of the sweep firmware. usage: sd_power_gap.py <elf>"""
import re, struct, subprocess, sys
ELF = sys.argv[1]
FR = {0: 'OK', 1: 'DISK_ERR', 2: 'INT_ERR', 3: 'NOT_READY', 13: 'NO_FILESYSTEM'}
out = subprocess.run(['arm-none-eabi-nm', '-S', ELF], capture_output=True, text=True).stdout
addr, size = next((int(p[0], 16), int(p[1], 16)) for p in (l.split() for l in out.splitlines()) if len(p) == 4 and p[3] == 'fs_sweep')
words = size // 4
r = subprocess.run(['openocd', '-f', 'interface/stlink.cfg', '-c', 'transport select hla_swd', '-f', 'target/stm32wbx.cfg',
                    '-c', 'stm32wbx.cpu configure -event examine-end {}', '-c', 'init', '-c', 'halt',
                    '-c', 'mdw 0x%08x %d' % (addr, words), '-c', 'resume', '-c', 'exit'], capture_output=True, text=True, timeout=40)
data = []
for line in (r.stdout + r.stderr).splitlines():
    m = re.match(r'^0x[0-9a-f]+: (.*)$', line.strip())
    if m:
        data += [int(w, 16) for w in m.group(1).split()]
raw = struct.pack('<%dI' % len(data), *data)
done, count = struct.unpack_from('<II', raw, 0)
print('experiment finished: %s, %d measurements' % (bool(done), count))
table = {}
for n in range(count):
    gap, before, after, stage, tries, r1, sdhc, hs, sdhc_before = struct.unpack_from('<IBBBBBBBB', raw, 8 + 12 * n)
    table.setdefault(gap, []).append((before, after, stage, tries, r1, sdhc, hs, sdhc_before))
print('%10s  %s' % ('gap', 'second mount of each pass: result/card type seen (1 = SDHC)/high-speed switch (0 = ok)/CMD0 tries'))
for gap in sorted(table):
    cells = []
    for before, after, stage, tries, r1, sdhc, hs, sdhc_before in table[gap]:
        cell = '%s/%d/%d/%d' % (FR.get(after, after), sdhc, hs, tries)
        if before != 0 or sdhc_before != 1:
            cell += ' (first mount: %s, type %d)' % (FR.get(before, before), sdhc_before)
        cells.append(cell)
    print('%7.2f ms  %s' % (gap / 1000.0, '   '.join(cells)))
