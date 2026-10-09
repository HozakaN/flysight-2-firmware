#!/usr/bin/env python3
"""Reads over SWD, from a firmware stopped in Error_Handler, what tells why the mount failed.

usage: postmortem.py <elf> [output file]
"""
import re
import struct
import subprocess
import sys

ELF = sys.argv[1]
OUT = open(sys.argv[2], 'w') if len(sys.argv) > 2 else None


def say(text):
    print(text)
    if OUT:
        OUT.write(text + '\n')


def symbols():
    out = subprocess.run(['arm-none-eabi-nm', '-S', ELF], capture_output=True, text=True).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4:
            table.setdefault(parts[3], (int(parts[0], 16), int(parts[1], 16)))
    return table


SYM = symbols()
READS = []   # (label, address, word count, byte offset in the first word, size in bytes)


def want(label, addr, words, shift=0, size=None):
    READS.append((label, addr, words, shift, size))


for name in ('Stat', 'resource_counts', 'fs', 'flag_SDHC', 'SdStatus', 'hspi2', 'main_transfer_state',
             'SystemCoreClock', 'uwTick', 'state', 'rx_read_index', 'rx_write_index', 'tx_read_index',
             'tx_write_index', 'mode_state', 'sd_diag_stage', 'fs_diag'):
    if name in SYM:
        addr, size = SYM[name]
        want(name, addr & ~3, (size + (addr & 3) + 3) // 4, addr & 3, size)
want('SPI2 CR1 CR2 SR', 0x40003800, 3)
want('RCC CR', 0x58000000, 1)
want('RCC CFGR', 0x58000008, 1)
want('GPIOH MODER', 0x48001C00, 1)
want('GPIOH ODR', 0x48001C14, 1)
want('GPIOD MODER', 0x48000C00, 1)
want('GPIOD IDR ODR', 0x48000C10, 2)
want('GPIOB MODER', 0x48000400, 1)
want('GPIOB IDR', 0x48000410, 1)

cmd = ['openocd', '-f', 'interface/stlink.cfg', '-c', 'transport select hla_swd', '-f', 'target/stm32wbx.cfg',
       '-c', 'stm32wbx.cpu configure -event examine-end {}', '-c', 'init', '-c', 'halt',
       '-c', 'echo [format PC=%s [reg pc]]', '-c', 'echo [format SP=%s [reg sp]]', '-c', 'echo [format LR=%s [reg lr]]']
for i, (label, addr, words, shift, size) in enumerate(READS):
    cmd += ['-c', 'echo "@@%d"' % i, '-c', 'mdw 0x%08x %d' % (addr, words)]
cmd += ['-c', 'echo "@@stack"', '-c', 'mdw [expr {[lindex [reg sp] 2] - 0x300}] 0x140', '-c', 'exit']
out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
text = out.stdout + out.stderr

regs = dict(re.findall(r'(PC|SP|LR)=.*?(0x[0-9a-fA-F]+)', text))
blocks = {}
current = None
for line in text.splitlines():
    line = line.strip()
    if line.startswith('@@'):
        current = line[2:]
        blocks[current] = []
        continue
    m = re.match(r'^0x([0-9a-f]+): (.*)$', line)
    if m and current is not None:
        blocks[current].append((int(m.group(1), 16), [int(w, 16) for w in m.group(2).split()]))


def words_of(key):
    result = []
    for _, ws in blocks.get(key, []):
        result += ws
    return result


def where(addr):
    r = subprocess.run(['arm-none-eabi-addr2line', '-f', '-e', ELF, '0x%x' % addr], capture_output=True, text=True)
    lines = r.stdout.splitlines()
    return '%s (%s)' % (lines[0], lines[1].split('/')[-1]) if len(lines) >= 2 else '?'


say('PC=%s %s   SP=%s' % (regs.get('PC'), where(int(regs.get('PC', '0'), 16)), regs.get('SP')))
for i, (label, addr, words, shift, size) in enumerate(READS):
    ws = words_of(str(i))
    raw = struct.pack('<%dI' % len(ws), *ws)[shift:]
    if size is not None:
        raw = raw[:size]
    if label == 'fs':
        say('fs: fs_type=%d drv=%d n_fats=%d   first 16 bytes: %s' % (raw[0], raw[1], raw[2], raw[:16].hex()))
        # the sector window is the last 512 bytes of the structure (_MAX_SS == 512)
        win = raw[len(raw) - 512:len(raw)] if len(raw) >= 512 else b''
        if win:
            say('fs.win (last sector read): %s ... %s   signature bytes 510-511: %s'
                % (win[:32].hex(), win[480:510].hex(), win[510:512].hex()))
            say('fs.win: %d bytes 0x00, %d bytes 0xff' % (win.count(0), win.count(0xff)))
    elif label == 'fs_diag':
        continue
    elif size is not None and size <= 3:
        say('%-22s = %s' % (label, ' '.join('%02x' % b for b in raw)))
    else:
        say('%-22s @0x%08x: %s' % (label, addr, ' '.join('%08x' % w for w in ws[:8])))

stack = []
base = None
for addr, ws in blocks.get('stack', []):
    if base is None:
        base = addr
    stack += ws
say('return addresses left on the stack below and above SP (deepest first):')
seen = 0
for n, w in enumerate(stack):
    if 0x08000000 <= w < 0x08100000 and (w & 1):
        name = where(w - 1)
        if not name.startswith('??'):
            say('  [SP%+5d] 0x%08x %s' % (base + 4 * n - int(regs.get('SP', '0'), 16), w, name))
            seen += 1
say('%d code addresses found' % seen)
