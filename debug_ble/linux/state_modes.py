#!/usr/bin/env python3
"""Tests of the state file (/flysight.txt) outside start-up: active mode, start mode, config
mode and a USB unplug, with the FlySight's USB cable plugged into this PC and an ST-Link.

usage: state_modes.py <elf> <test> [count]
  active | start | config   enter the mode with the button, leave it, look at the state before,
                            during and after, then read the card from the PC
  usb                       let the firmware see the cable, force VBUS low, give it back
  timing                    time spent in FS_State_Write() at start-up and when active mode starts
  reset-active | reset-usb  reset just after the file to write is opened, when active mode
                            starts or after a USB unplug: are the BLE keys still there?
  boot                      a start, caught if it draws keys, recovers a file or gives one up
  config-select             with a /config folder holding three files on the card: enter config
                            mode, press while the second file is announced, then enter and
                            leave active mode, which writes the state from inside /config
  config-through            enter config mode and let it go through the files without a press
  reset-config              reset just after the file to write is opened, when a press during
                            the first file leaves config mode
  unplugged-start           a start that sees VBUS low, to run after flashing

The button (PC12) and VBUS_DIV (PA2) are driven over SWD the way Scripts/cli/vbus.py does it. Every
start is made to see VBUS low at the point where the firmware first reads it: develop faults
when it starts with the cable in. MODE_WAIT_MS sets how long a mode is given to settle (6000).

WARNING: on a firmware without the fix, reset-active, reset-usb and reset-config make the FlySight
draw new BLE keys for good. The mode tests add sessions to TEMP on the card and move Temp_Folder forward.
Keys are never printed, only the first characters of their SHA-1.
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import time

ELF, TEST = sys.argv[1], sys.argv[2]
COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 1
nm = subprocess.run(['arm-none-eabi-nm', '-S', ELF], capture_output=True, text=True).stdout
dis = subprocess.run(['arm-none-eabi-objdump', '-d', '--no-show-raw-insn', ELF], capture_output=True, text=True).stdout


def sym(name, size=None):
    for line in nm.splitlines():
        p = line.split()
        if len(p) == 4 and p[3] == name and (size is None or int(p[1], 16) == size):
            return int(p[0], 16)
    raise SystemExit('no symbol ' + name)


def calls(function, callee):
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


STATE, FS, MODE, TICK = sym('state', 0x70), sym('fs', 0x234), sym('mode_state'), sym('uwTick')
ERROR = sym('Error_Handler') & ~1
NEW_IRK = (calls('FS_State_Complete', 'FS_Common_GetRandomBytes') or calls('FS_State_Read', 'FS_Common_GetRandomBytes'))[0]
WRITE = sym('FS_State_Write')
MIDWRITE = calls('FS_State_Write', 'f_open')[0] + 4
FIXED = bool(calls('FS_State_Write', 'f_rename'))
RECOVER = (calls('FS_State_Read', 'f_rename') or [None])[0]
GIVE_UP = calls('FS_State_Write', 'f_unlink')[1] if FIXED else None
VBUS_INIT = sym('FS_Mode_Init')     # where the firmware first reads VBUS_DIV, before its interrupt is enabled
AFTER = {'start-up': calls('FS_State_Init', 'FS_State_Write')[0] + 4,
         'active mode': calls('FS_State_NextSession', 'FS_State_Write')[0] + 4}
MODES = ['sleep', 'active', 'config', 'USB', 'pairing', 'start']
ACTIVE_MODE = STATE + 109
# how long a mode is given to settle; a full card makes everything slower
SETTLE = int(os.environ.get('MODE_WAIT_MS', '6000'))

PRELUDE = '''
stm32wbx.cpu configure -event examine-end {}
init
proc rd8 {a} { return [lindex [read_memory $a 8 1] 0] }
proc pin_low {moder shift bsrr bit} {
    mww $bsrr [expr {$bit << 16}]
    mwb $moder [expr {([rd8 $moder] & ~(3 << $shift)) | (1 << $shift)}]
}
proc pin_free {moder shift} { mwb $moder [expr {[rd8 $moder] & ~(3 << $shift)}] }
proc button_down {} { pin_low 0x48000803 0 0x48000818 0x1000 }
proc button_up {} { pin_free 0x48000803 0 }
proc vbus_low {} { pin_low 0x48000000 4 0x48000018 0x4 }
proc vbus_free {} { pin_free 0x48000000 4 }
proc snap {tag} {
    halt
    sleep 150
    echo "@@${tag}state1"; echo [mdw 0x%x 28]
    sleep 80
    echo "@@${tag}state2"; echo [mdw 0x%x 28]
    echo "@@${tag}mode"; echo [mdb 0x%x 1]
    echo "@@${tag}tick"; echo [mdw 0x%x 1]
    echo "@@${tag}fs"; echo [mdw 0x%x 141]
    echo [format "${tag}PC=%%s" [reg pc]]
    echo "@@end"
    resume
}
''' % (STATE, STATE, MODE, TICK, FS)


def openocd(lines, timeout=180):
    with tempfile.NamedTemporaryFile('w', suffix='.tcl', delete=False) as f:
        f.write(PRELUDE + '\n'.join(lines) + '\nexit\n')
        path = f.name
    out = subprocess.run(['openocd', '-f', 'interface/stlink.cfg', '-c', 'transport select hla_swd',
                          '-f', 'target/stm32wbx.cfg', '-f', path], capture_output=True, text=True, timeout=timeout)
    os.unlink(path)
    return out.stdout + out.stderr


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


class Snap:
    """What the firmware held at one moment."""
    def __init__(self, text, tag):
        a, b = block(text, tag + 'state1'), block(text, tag + 'state2')
        self.ok = len(a) == 28 and a == b
        raw = struct.pack('<28I', *a) if self.ok else bytes(112)
        self.keys = hashlib.sha1(raw[77:109]).hexdigest()[:8] if self.ok else None
        self.session = raw[12:24].hex()
        self.config = raw[24:37].split(b'\0')[0].decode('ascii', 'replace')
        self.temp_folder = struct.unpack_from('<I', raw, 40)[0]
        self.active_mode = raw[109]
        mode = block(text, tag + 'mode')
        self.mode = MODES[mode[0]] if mode and mode[0] < len(MODES) else '?'
        tick = block(text, tag + 'tick')
        self.tick = tick[0] if tick else None
        self.pc = where(reg(text, tag + 'PC'))
        self.card = card(block(text, tag + 'fs'))


def card(words):
    if len(words) != 141:
        return 'card not read'
    raw = struct.pack('<141I', *words)
    dirbase, winsect, win = struct.unpack_from('<I', raw, 40)[0], struct.unpack_from('<I', raw, 48)[0], raw[52:564]
    if winsect != dirbase:
        return 'root directory not in the window'
    found = {}
    for o in range(0, 512, 32):
        entry = win[o:o + 32]
        if entry[0] in (0x00, 0xE5) or entry[11] == 0x0F:
            continue
        found[entry[0:11].decode('ascii', 'replace')] = struct.unpack_from('<I', entry, 28)[0]
    txt, tmp = found.get('FLYSIGHTTXT'), found.get('FLYSIGHTTMP')
    return 'FLYSIGHT.TXT %s, FLYSIGHT.TMP %s' % ('absent' if txt is None else '%d bytes' % txt, 'absent' if tmp is None else '%d bytes' % tmp)


def keys_word(before, after):
    if after.keys is None:
        return 'keys unreadable'
    return 'keys kept (%s)' % after.keys if after.keys == before.keys else 'KEYS CHANGED (%s -> %s)' % (before.keys, after.keys)


LONG_PRESS = ['button_down', 'sleep 1300', 'button_up']
SHORT_PRESS = ['button_down', 'sleep 150', 'button_up']
WATCH = [NEW_IRK, ERROR] + ([RECOVER, GIVE_UP] if FIXED else [])
# with the USB cable in, the firmware is in USB mode after a start: make it see VBUS low first
ASLEEP = ['vbus_low', 'sleep 4000']


def catches(text):
    events = []
    for i in (1, 2):
        pc = reg(text, 'C%dPC' % i)
        if pc is not None:
            events.append({NEW_IRK: 'CAUGHT drawing a NEW IRK', ERROR: 'CAUGHT in Error_Handler',
                           RECOVER: 'state file missing, new file put in its place',
                           GIVE_UP: 'second file given up, state rewritten in place'}.get(pc & ~1, where(pc)))
    return ', then '.join(events) if events else 'normal'


def watch_reset(reset='reset run'):
    """A start that is caught if it draws keys, recovers a file or fails, and that is made to see
    VBUS low when it first looks at it: with the cable in, develop faults in USB mode at start-up."""
    tcl = ['bp 0x%x 2 hw' % a for a in WATCH + [VBUS_INIT]] + [reset, 'set caught 0',
           'for {set i 0} {$i < 5} {incr i} {',
           '  if {[catch {wait_halt 3500}]} { break }',
           '  set pc [lindex [reg pc] 2]',
           '  if {[expr {$pc == 0x%x}]} { vbus_low; mww 0x5800080C 0x4; rbp 0x%x; resume; continue }' % (VBUS_INIT, VBUS_INIT),
           '  incr caught', '  echo "C${caught}PC=$pc"', '  resume', '}']
    return tcl + ['catch {rbp 0x%x}' % a for a in WATCH + [VBUS_INIT]]


def mode_test(kind):
    for n in range(1, COUNT + 1):
        tcl = ASLEEP + ['snap a']
        if kind == 'start':
            tcl += ['mwb 0x%x 1' % ACTIVE_MODE]
        if kind == 'config':
            tcl += SHORT_PRESS + ['sleep 200'] + LONG_PRESS
        else:
            tcl += LONG_PRESS
        tcl += ['sleep %d' % SETTLE, 'snap b']
        tcl += (SHORT_PRESS if kind == 'config' else LONG_PRESS) + ['sleep %d' % SETTLE]
        if kind == 'start':
            # the file now says "Active_Mode: 1": go through active mode once to write 0 back
            tcl += ['mwb 0x%x 0' % ACTIVE_MODE] + LONG_PRESS + ['sleep 5000'] + LONG_PRESS + ['sleep 5000']
        tcl += ['snap c', 'vbus_free']
        text = openocd(tcl)
        a, b, c = Snap(text, 'a'), Snap(text, 'b'), Snap(text, 'c')
        print('%s %d: before: mode %s, Temp_Folder %d | after the press: mode %s (%s), Temp_Folder %d, session %s, %s | after leaving: mode %s (%s), Temp_Folder %d, %s, %s | %s'
              % (kind, n, a.mode, a.temp_folder, b.mode, b.pc, b.temp_folder, 'new' if b.session != a.session else 'same', keys_word(a, b),
                 c.mode, c.pc, c.temp_folder, keys_word(a, c), c.card, wait_mount(20)), flush=True)
        time.sleep(2)


def timing():
    for n in range(1, COUNT + 1):
        text = openocd(['reset halt', 'bp 0x%x 2 hw' % WRITE, 'bp 0x%x 2 hw' % AFTER['start-up'], 'bp 0x%x 2 hw' % VBUS_INIT, 'resume',
                        'wait_halt 8000', 'echo "@@t1"; echo [mdw 0x%x 1]' % TICK, 'echo "@@end"', 'resume',
                        'wait_halt 8000', 'echo "@@t2"; echo [mdw 0x%x 1]' % TICK, 'echo "@@end"',
                        'rbp 0x%x' % WRITE, 'rbp 0x%x' % AFTER['start-up'], 'resume',
                        'wait_halt 8000', 'vbus_low', 'mww 0x5800080C 0x4', 'rbp 0x%x' % VBUS_INIT, 'resume', 'sleep 4000'])
        t1, t2 = block(text, 't1'), block(text, 't2')
        boot = t2[0] - t1[0] if t1 and t2 else None
        text = openocd(ASLEEP + ['bp 0x%x 2 hw' % WRITE, 'bp 0x%x 2 hw' % AFTER['active mode'], 'button_down',
                        'wait_halt 5000', 'echo "@@t1"; echo [mdw 0x%x 1]' % TICK, 'echo "@@end"', 'resume',
                        'wait_halt 8000', 'echo "@@t2"; echo [mdw 0x%x 1]' % TICK, 'echo "@@end"',
                        'rbp 0x%x' % WRITE, 'rbp 0x%x' % AFTER['active mode'], 'button_up', 'resume', 'sleep 4000']
                       + LONG_PRESS + ['sleep 5000', 'snap c'])
        t1, t2 = block(text, 't1'), block(text, 't2')
        active = t2[0] - t1[0] if t1 and t2 else None
        c = Snap(text, 'c')
        print('timing %d: FS_State_Write() took %s ms at start-up and %s ms when active mode started (back in mode %s)'
              % (n, boot, active, c.mode), flush=True)
        time.sleep(2)


def reset_test(kind):
    """A reset while the state file is being written outside start-up."""
    for n in range(1, COUNT + 1):
        trigger = 'button_down' if kind == 'active' else 'vbus_low'
        tcl = (ASLEEP if kind == 'active' else ['vbus_free', 'sleep 6000']) + ['snap a', 'bp 0x%x 2 hw' % MIDWRITE, trigger,
               'if {[catch {wait_halt 6000}]} { echo "@@NOWRITE" } else { echo [format "STOPPC=%s" [reg pc]] }',
               'rbp 0x%x' % MIDWRITE] + watch_reset() + ['sleep 4000', 'snap c']
        text = openocd(tcl)
        a, c = Snap(text, 'a'), Snap(text, 'c')
        stopped = 'no write seen' if '@@NOWRITE' in text else 'firmware stopped in %s' % where(reg(text, 'STOPPC'))
        print('reset during the write, %s %d: %s, reset; next start: %s; %s; mode %s; %s'
              % ('active mode' if kind == 'active' else 'USB unplug', n, stopped, catches(text), keys_word(a, c), c.mode, c.card), flush=True)
        time.sleep(3)


# Config mode starts one second after the second press goes down, and announces each file of
# /config for about half a second when its audio file is missing: 560 ms after a release made
# 200 ms into config mode, the second file is the current one; 100 ms after it, still the first
ENTER_CONFIG = SHORT_PRESS + ['sleep 200', 'button_down', 'sleep 1200', 'button_up']
FIRST_FILE, SECOND_FILE = ['sleep 100'], ['sleep 560']


def config_select():
    for n in range(1, COUNT + 1):
        text = openocd(ASLEEP + ['snap a'] + ENTER_CONFIG + SECOND_FILE + SHORT_PRESS + ['sleep %d' % SETTLE, 'snap b']
                       + LONG_PRESS + ['sleep %d' % SETTLE, 'snap c'] + LONG_PRESS + ['sleep %d' % SETTLE, 'snap d', 'vbus_free'])
        a, b, c, d = Snap(text, 'a'), Snap(text, 'b'), Snap(text, 'c'), Snap(text, 'd')
        print('config-select %d: before: Config_File "%s" | press in config mode: mode %s, Config_File "%s", %s, %s | active mode: mode %s (%s), Temp_Folder %d -> %d, %s | after leaving: mode %s, Config_File "%s", %s, %s | %s'
              % (n, a.config, b.mode, b.config, keys_word(a, b), b.card, c.mode, c.pc, a.temp_folder, c.temp_folder, keys_word(a, c),
                 d.mode, d.config, keys_word(a, d), d.card, wait_mount(20)), flush=True)
        time.sleep(2)


def config_through():
    for n in range(1, COUNT + 1):
        text = openocd(ASLEEP + ['snap a'] + ENTER_CONFIG + ['sleep 1000', 'snap b', 'sleep %d' % SETTLE, 'snap c', 'vbus_free'])
        a, b, c = Snap(text, 'a'), Snap(text, 'b'), Snap(text, 'c')
        print('config-through %d: before: Config_File "%s" | 1.2 s into config mode: mode %s | later: mode %s (%s), Config_File "%s", %s, %s | %s'
              % (n, a.config, b.mode, c.mode, c.pc, c.config, keys_word(a, c), c.card, wait_mount(20)), flush=True)
        time.sleep(2)


def reset_config():
    for n in range(1, COUNT + 1):
        text = openocd(ASLEEP + ['snap a'] + ENTER_CONFIG + FIRST_FILE + ['bp 0x%x 2 hw' % MIDWRITE, 'button_down',
                       'if {[catch {wait_halt 6000}]} { echo "@@NOWRITE" } else { echo [format "STOPPC=%s" [reg pc]] }',
                       'button_up', 'rbp 0x%x' % MIDWRITE] + watch_reset() + ['sleep 4000', 'snap c'])
        a, c = Snap(text, 'a'), Snap(text, 'c')
        stopped = 'no write seen' if '@@NOWRITE' in text else 'firmware stopped in %s' % where(reg(text, 'STOPPC'))
        print('reset during the write, leaving config mode %d: Config_File "%s" before; %s, reset; next start: %s; %s; mode %s; Config_File "%s"; %s'
              % (n, a.config, stopped, catches(text), keys_word(a, c), c.mode, c.config, c.card), flush=True)
        time.sleep(3)


def host_volume():
    """The card as the PC sees it once the firmware is back in USB mode."""
    for line in open('/proc/mounts'):
        dev, mount = line.split()[:2]
        mount = mount.replace('\\040', ' ')
        if '/media/' in mount and os.path.exists(os.path.join(mount, 'CONFIG.TXT')):
            path = os.path.join(mount, 'FLYSIGHT.TXT')
            if not os.path.exists(path):
                return 'PC: NO FLYSIGHT.TXT on the card'
            text = open(path, 'rb').read()
            fields = {k.decode(): v.decode().strip() for k, v in re.findall(rb'^([A-Za-z_]+):[ \t]*([^;\r\n]*)', text, re.M)}
            try:
                k = hashlib.sha1(bytes.fromhex(fields['BLE_IRK']) + bytes.fromhex(fields['BLE_ERK'])).hexdigest()[:8]
            except Exception:
                k = 'NO KEYS'
            st = os.statvfs(mount)
            folder = ''
            if os.path.isdir(os.path.join(mount, 'config')):
                names = sorted(os.listdir(os.path.join(mount, 'config')))
                stray = [n for n in names if n.upper().startswith('FLYSIGHT')]
                folder = ', Config_File "%s", /config holds %d files%s' % (
                    fields.get('Config_File', ''), len(names), ' AND A STATE FILE: ' + ' '.join(stray) if stray else '')
            return 'PC reads FLYSIGHT.TXT: %d bytes, keys %s, Temp_Folder %s, Active_Mode %s, FLYSIGHT.TMP %s, %d KB free%s' % (
                len(text), k, fields.get('Temp_Folder', '?'), fields.get('Active_Mode', '?'),
                'PRESENT' if os.path.exists(os.path.join(mount, 'FLYSIGHT.TMP')) else 'absent', st.f_bavail * st.f_frsize // 1024, folder)
    return 'card not mounted on the PC'


def wait_mount(seconds):
    end = time.time() + seconds
    while time.time() < end:
        v = host_volume()
        if not v.startswith('card not mounted'):
            return v
        time.sleep(0.5)
    return host_volume()


def boot_test():
    for n in range(1, COUNT + 1):
        text = openocd(['snap a'] + watch_reset() + ['sleep 4000', 'snap c', 'vbus_free'])
        a, c = Snap(text, 'a'), Snap(text, 'c')
        print('start %d: %s; %s; mode %s (%s); %s | %s' % (n, catches(text), keys_word(a, c), c.mode, c.pc, c.card, wait_mount(20)), flush=True)
        time.sleep(2)


def usb_test():
    for n in range(1, COUNT + 1):
        openocd(['vbus_free'])
        host = wait_mount(20)
        subprocess.run(['sync'])
        text = openocd(['snap a', 'vbus_low', 'sleep 4000', 'snap b', 'vbus_free', 'sleep 3000', 'snap c'])
        a, b, c = Snap(text, 'a'), Snap(text, 'b'), Snap(text, 'c')
        print('usb %d: plugged: mode %s; %s | VBUS forced low: mode %s (%s), %s, %s | VBUS given back: mode %s, %s'
              % (n, a.mode, host, b.mode, b.pc, keys_word(a, b), b.card, c.mode, wait_mount(15)), flush=True)
        time.sleep(2)


print('%s the fix' % ('WITH' if FIXED else 'WITHOUT'), flush=True)
def start_unplugged():
    text = openocd(watch_reset('reset run') + ['sleep 3000', 'snap c'])
    c = Snap(text, 'c')
    print('started with VBUS seen low: %s; mode %s (%s)' % (catches(text), c.mode, c.pc), flush=True)


{'config-select': config_select, 'config-through': config_through, 'reset-config': reset_config,
 'unplugged-start': start_unplugged, 'active': lambda: mode_test('active'), 'start': lambda: mode_test('start'), 'config': lambda: mode_test('config'),
 'timing': timing, 'boot': boot_test, 'reset-active': lambda: reset_test('active'), 'usb': usb_test, 'reset-usb': lambda: reset_test('usb')}[TEST]()
