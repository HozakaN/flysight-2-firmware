#!/usr/bin/env python3
"""Resets the firmware at a chosen point of its start-up, lets the next start run without stopping
it, and says whether the BLE keys kept in /flysight.txt survived.

usage: state_rewrite_reset.py <elf> <count> <mode> <dump directory>

Each mode stops the firmware somewhere during a start-up, then sends the reset:
  idle       nowhere: an ordinary reset, microSD card off
  preread    just after FS_State_Read() has opened the state file
  midwrite   just after FS_State_Write() has opened the file it writes
             (without the fix: /flysight.txt, emptied; with it: /flysight.tmp)
  preunlink  the new file is complete, the previous one is about to be removed   (fix only)
  gap        the previous file is removed and the new one is NOT renamed: the call
             to f_rename() is skipped, the start-up finishes, then an ordinary reset   (fix only)
  full       no reset during the write: the card is made to look full while the new file is
             written (every cluster allocation is refused by the debugger), the start-up
             finishes, then an ordinary reset                                          (fix only)
  powered    in FatFS_DeInit(): everything written and closed, card still powered

WARNING: on a firmware without the fix, midwrite makes the FlySight draw new BLE keys, for good.
Every host bonded with it has to pair again afterwards.

The next start is stopped only if it is about to draw a new IRK, reaches Error_Handler(), or\n(with the fix) finds /flysight.txt missing and puts the new file in its place.
Keys are never printed, only the first characters of their SHA-1, read twice. The dumps hold raw
memory of the firmware, keys included: keep them out of the repository.
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile

ELF, COUNT, MODE, DUMPS = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
os.makedirs(DUMPS, exist_ok=True)
nm = subprocess.run(['arm-none-eabi-nm', '-S', ELF], capture_output=True, text=True).stdout
dis = subprocess.run(['arm-none-eabi-objdump', '-d', '--no-show-raw-insn', ELF], capture_output=True, text=True).stdout


def sym(name, size=None, optional=False):
    for line in nm.splitlines():
        p = line.split()
        if len(p) == 4 and p[3] == name and (size is None or int(p[1], 16) == size):
            return int(p[0], 16)
    if optional:
        return None
    raise SystemExit('no symbol ' + name)


def calls(function, callee):
    """Addresses of the calls to callee in function."""
    inside, found = False, []
    for line in dis.splitlines():
        if re.match(r'^[0-9a-f]+ <%s>:' % re.escape(function), line):
            inside = True
            continue
        if inside and re.match(r'^[0-9a-f]+ <', line):
            break
        m = re.match(r'^\s*([0-9a-f]+):\s+bl\s+[0-9a-f]+ <%s>' % re.escape(callee), line)
        if inside and m:
            found.append(int(m.group(1), 16))
    return found


STATE, FS = sym('state', 0x70), sym('fs', 0x234)
TICK = sym('uwTick')
ERROR = sym('Error_Handler') & ~1
# the keys are drawn in FS_State_Complete() on the branch, in FS_State_Read() upstream
NEW_IRK = (calls('FS_State_Complete', 'FS_Common_GetRandomBytes') or calls('FS_State_Read', 'FS_Common_GetRandomBytes'))[0]
UNLINK, RENAME = calls('FS_State_Write', 'f_unlink'), calls('FS_State_Write', 'f_rename')
FIXED = bool(UNLINK and RENAME)
# with the fix: reached only when /flysight.txt is missing at start-up and the new file is put in its place
RECOVER = (calls('FS_State_Read', 'f_rename') or [None])[0]
STOPS = {'preread': calls('FS_State_Read', 'f_open')[0] + 4,
         'midwrite': calls('FS_State_Write', 'f_open')[0] + 4,
         'powered': sym('FatFS_DeInit')}
if FIXED:
    STOPS['preunlink'] = UNLINK[0]
    STOPS['gap'] = RENAME[0]
    STOPS['full'] = sym('FS_State_Write')
    # where f_write() learns whether it got a cluster, and where the firmware gives up the second file
    ALLOCATED = [a + 4 for a in calls('f_write', 'create_chain')]
    GIVE_UP = UNLINK[1]
if MODE != 'idle' and MODE not in STOPS:
    raise SystemExit('mode %s does not exist for this firmware (%s the fix)' % (MODE, 'with' if FIXED else 'without'))


def read_card(tag):
    return ['echo "@@%sstate1"' % tag, 'echo [mdw 0x%x 28]' % STATE, 'echo "@@end"', 'sleep 100',
            'echo "@@%sstate2"' % tag, 'echo [mdw 0x%x 28]' % STATE, 'echo "@@%sfs"' % tag, 'echo [mdw 0x%x 141]' % FS, 'echo "@@end"']


def openocd(tcl):
    with tempfile.NamedTemporaryFile('w', suffix='.tcl', delete=False) as f:
        f.write('\n'.join(['stm32wbx.cpu configure -event examine-end {}', 'init'] + tcl + ['exit']) + '\n')
        path = f.name
    out = subprocess.run(['openocd', '-f', 'interface/stlink.cfg', '-c', 'transport select hla_swd',
                          '-f', 'target/stm32wbx.cfg', '-f', path], capture_output=True, text=True, timeout=150)
    os.unlink(path)
    return out.stdout + out.stderr


def trial():
    tcl = []
    if MODE in STOPS:
        tcl += ['reset halt', 'bp 0x%x 2 hw' % STOPS[MODE], 'resume', 'wait_halt 8000',
                'echo [format "STOPPC=%s" [reg pc]]', 'echo "@@stoptick"', 'echo [mdw 0x%x 1]' % TICK, 'echo "@@end"',
                'rbp 0x%x' % STOPS[MODE]]
    if MODE == 'gap':
        # skip the renaming, let this start-up finish, and look at what is on the card
        tcl += ['reg pc 0x%x' % (STOPS[MODE] + 4), 'resume', 'sleep 3000', 'halt', 'sleep 200'] + read_card('gap') + ['resume']
    if MODE == 'full':
        # refuse every cluster to the new file, until the firmware gives it up; then leave it alone
        tcl += ['bp 0x%x 2 hw' % a for a in ALLOCATED] + ['bp 0x%x 2 hw' % GIVE_UP, 'resume', 'set refused 0',
                'while {$refused < 400} {',
                '  if {[catch {wait_halt 4000}]} { echo "@@FULLTIMEOUT"; break }',
                '  if {[expr {[lindex [reg pc] 2] == 0x%x}]} {' % GIVE_UP]
        tcl += ['    rbp 0x%x' % a for a in ALLOCATED + [GIVE_UP]]
        tcl += ['    echo "@@GAVEUP"', '    resume', '    break', '  }',
                '  reg r0 0', '  resume', '  incr refused', '}',
                'echo "REFUSED=0x[format %x $refused]"',
                'sleep 3000', 'halt', 'sleep 200'] + read_card('full') + ['resume']
    watched = [NEW_IRK, ERROR] + ([RECOVER] if RECOVER else [])
    tcl += ['bp 0x%x 2 hw' % a for a in watched] + ['reset run']
    for i in (1, 2):      # at most two of them in one start: the recovery, then new keys
        tcl += ['if {[catch {wait_halt 3500}]} { echo "@@QUIET%d" } else {' % i,
                '  echo [format "C%dPC=%%s" [reg pc]]' % i, '  echo "@@c%dtick"' % i, '  echo [mdw 0x%x 1]' % TICK, '  echo "@@end"', '  resume', '}']
    tcl += ['catch {rbp 0x%x}' % a for a in watched]
    tcl += ['halt', 'sleep 200', 'echo [format "ENDPC=%s" [reg pc]]'] + read_card('end') + ['resume']
    return openocd(tcl)


def block(text, key):
    data, on = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('@@'):
            on = line == '@@' + key
            continue
        m = re.match(r'^0x[0-9a-f]+: (.*)$', line)
        if on and m:
            data += [int(w, 16) for w in m.group(1).split()]
    return data


def reg(text, key):
    m = re.search(key + r'=.*?(0x[0-9a-fA-F]+)', text)
    return int(m.group(1), 16) if m else None


def where(pc):
    if pc is None:
        return '?'
    return subprocess.run(['arm-none-eabi-addr2line', '-f', '-e', ELF, '0x%x' % pc], capture_output=True, text=True).stdout.splitlines()[0]


def keys(text, tag):
    """SHA-1 of the two keys, if two reads of the state agree."""
    a, b = block(text, tag + 'state1'), block(text, tag + 'state2')
    if len(a) != 28 or a != b:
        return None
    return hashlib.sha1(struct.pack('<28I', *a)[77:109]).hexdigest()[:8]


def card(text, tag):
    """The two state files in the root directory, from the sector FatFs last had in its window."""
    words = block(text, tag + 'fs')
    if len(words) != 141:
        return 'card: not read'
    raw = struct.pack('<141I', *words)
    dirbase, winsect, win = struct.unpack_from('<I', raw, 40)[0], struct.unpack_from('<I', raw, 48)[0], raw[52:564]
    if winsect != dirbase:
        return 'card: the root directory is not the last sector FatFs touched'
    found = {}
    for o in range(0, 512, 32):
        entry = win[o:o + 32]
        if entry[0] in (0x00, 0xE5) or entry[11] == 0x0F:
            continue
        found[entry[0:11].decode('ascii', 'replace')] = struct.unpack_from('<I', entry, 28)[0]
    txt, tmp = found.get('FLYSIGHTTXT'), found.get('FLYSIGHTTMP')
    return 'card: FLYSIGHT.TXT %s, FLYSIGHT.TMP %s' % ('absent' if txt is None else '%d bytes' % txt,
                                                      'absent' if tmp is None else '%d bytes' % tmp)


text = openocd(['halt', 'sleep 200'] + read_card('end') + ['resume'])
previous = keys(text, 'end')
print('%s the fix. Caught only if a new IRK is drawn (0x%x) or in Error_Handler (0x%x)' % ('WITH' if FIXED else 'WITHOUT', NEW_IRK, ERROR))
print('before: keys %s; %s' % (previous, card(text, 'end')), flush=True)
changed = 0
for n in range(1, COUNT + 1):
    text = trial()
    open(os.path.join(DUMPS, 'last_openocd.txt'), 'w').write(text)
    final = keys(text, 'end')
    line = 'reset %2d (%s): ' % (n, MODE)
    if MODE in STOPS:
        tick = block(text, 'stoptick')
        line += 'firmware stopped in %s at tick %s ms' % (where(reg(text, 'STOPPC')), tick[0] if tick else '?')
        if MODE == 'gap':
            line += ', renaming skipped, start-up finished, then reset'
        elif MODE == 'full':
            line += ', %s cluster allocations refused, %s; that start: keys %s, %s; then reset' % (
                reg(text, 'REFUSED'), 'the firmware gave up the second file and rewrote in place' if '@@GAVEUP' in text else 'THE FIRMWARE DID NOT FALL BACK',
                'kept' if keys(text, 'full') == previous else 'CHANGED (%s)' % keys(text, 'full'), card(text, 'full'))
        else:
            line += ', reset'
        line += '; next start: '
    events = []
    for i in (1, 2):
        pc = reg(text, 'C%dPC' % i)
        if pc is None:
            continue
        tick = block(text, 'c%dtick' % i)
        what = {NEW_IRK: 'CAUGHT drawing a NEW IRK', ERROR: 'CAUGHT in Error_Handler',
                RECOVER: 'state file missing, new file put in its place'}.get(pc & ~1, where(pc))
        events.append('%s at tick %s ms' % (what, tick[0] if tick else '?'))
    if events:
        line += ', then '.join(events)
    elif '@@QUIET1' in text:
        line += 'normal'
    else:
        line += 'no answer from the debugger'
    if final is None:
        line += '; keys: unreadable'
    else:
        line += '; keys %s (%s)' % ('CHANGED' if final != previous else 'kept', final)
        changed += final != previous
        previous = final
    line += '; %s; firmware in %s' % (card(text, 'end'), where(reg(text, 'ENDPC')))
    print(line, flush=True)
print('%s: %d of %d resets changed the keys' % (MODE, changed, COUNT))
