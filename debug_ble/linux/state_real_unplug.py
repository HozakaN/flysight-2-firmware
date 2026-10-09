#!/usr/bin/env python3
"""The state file (/flysight.txt) when the USB cable of the FlySight is really pulled out.

usage: state_real_unplug.py [--running <elf>] <elf>[:reset] [<elf>[:reset] ...]
       state_real_unplug.py --check <elf> ...     only reads the symbols of the builds

The FlySight is on its battery, with an ST-Link, and its USB cable goes to this PC. The script
says when to pull the cable out and when to plug it back in, and sees by itself that it was
done. For each build, in turn:
  1. cable out: the build is flashed and starts on the battery;
  2. cable in: the PC reads the card;
  3. cable out while the card is mounted on the PC, with nothing stopping the firmware; four
     seconds later the state is read over SWD;
  4. cable in: the PC reads the card again, and the two readings are compared;
  5. the PC ejects the card, then cable out with a breakpoint on FS_State_Write(): how many
     times it runs and how long it takes;
  6. with ":reset" after the name of the build: cable in, then out with a breakpoint just after
     the file to write is opened, and a reset there. Are the BLE keys still there?
--running names the build that is in the FlySight when the script starts: steps 2 and 3 are run
on it before the first flash, which needs the cable out anyway.

WARNING: step 6 on a firmware without the fix makes the FlySight draw new BLE keys for good.
Keys are never printed, only the first characters of their SHA-1.
"""
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time

OPENOCD = ['openocd', '-f', 'interface/stlink.cfg', '-c', 'transport select hla_swd', '-f', 'target/stm32wbx.cfg']
FLYSIGHT, STLINK = '16d0:0569', '0483:'
MODES = ['sleep', 'active', 'config', 'USB', 'pairing', 'start']
SETTLE = 4          # seconds given to the firmware once the cable has moved
PATIENCE = 1800     # seconds given to the person who holds the cable


def log(text):
    print('%s  %s' % (time.strftime('%H:%M:%S'), text), flush=True)


class Build:
    def __init__(self, spec):
        self.elf, _, option = spec.partition(':')
        self.reset = option == 'reset'
        self.name = os.path.basename(self.elf).rsplit('.', 1)[0]
        self.nm = subprocess.run(['arm-none-eabi-nm', '-S', self.elf], capture_output=True, text=True).stdout
        self.dis = subprocess.run(['arm-none-eabi-objdump', '-d', '--no-show-raw-insn', self.elf], capture_output=True, text=True).stdout
        self.state, self.fs = self.sym('state', 0x70), self.sym('fs', 0x234)
        self.mode, self.tick = self.sym('mode_state'), self.sym('uwTick')
        self.error = self.sym('Error_Handler') & ~1
        self.new_irk = (self.calls('FS_State_Complete', 'FS_Common_GetRandomBytes') or self.calls('FS_State_Read', 'FS_Common_GetRandomBytes'))[0]
        self.write = self.sym('FS_State_Write') & ~1
        self.midwrite = self.calls('FS_State_Write', 'f_open')[0] + 4
        self.fixed = bool(self.calls('FS_State_Write', 'f_rename'))
        self.recover = (self.calls('FS_State_Read', 'f_rename') or [None])[0]
        self.give_up = self.calls('FS_State_Write', 'f_unlink')[1] if self.fixed else None
        self.watch = [self.new_irk, self.error] + ([self.recover, self.give_up] if self.fixed else [])
        self.prelude = '''
stm32wbx.cpu configure -event examine-end {}
init
proc snap {tag} {
    halt
    sleep 150
    echo "@@${tag}state1"; echo [mdw 0x%x 28]
    sleep 80
    echo "@@${tag}state2"; echo [mdw 0x%x 28]
    echo "@@${tag}mode"; echo [mdb 0x%x 1]
    echo "@@${tag}fs"; echo [mdw 0x%x 141]
    echo [format "${tag}PC=%%s" [reg pc]]
    echo "@@end"
    resume
}
''' % (self.state, self.state, self.mode, self.fs)

    def sym(self, name, size=None):
        for line in self.nm.splitlines():
            p = line.split()
            if len(p) == 4 and p[3] == name and (size is None or int(p[1], 16) == size):
                return int(p[0], 16)
        raise SystemExit('%s: no symbol %s' % (self.elf, name))

    def calls(self, function, callee):
        inside, found = False, []
        for line in self.dis.splitlines():
            if re.match(r'^[0-9a-f]+ <%s>:' % re.escape(function), line):
                inside = True
                continue
            if inside and re.match(r'^[0-9a-f]+ <', line):
                break
            m = re.match(r'^\s*([0-9a-f]+):\s+bl\s+[0-9a-f]+ <%s>' % re.escape(callee), line)
            if inside and m:
                found.append(int(m.group(1), 16))
        return found

    def where(self, pc):
        if pc is None:
            return '?'
        return subprocess.run(['arm-none-eabi-addr2line', '-f', '-e', self.elf, '0x%x' % pc], capture_output=True, text=True).stdout.splitlines()[0]

    def script(self, lines):
        with tempfile.NamedTemporaryFile('w', suffix='.tcl', delete=False) as f:
            f.write(self.prelude + '\n'.join(lines) + '\nexit\n')
        return f.name

    def openocd(self, lines, timeout=120):
        path = self.script(lines)
        out = subprocess.run(OPENOCD + ['-f', path], capture_output=True, text=True, timeout=timeout)
        os.unlink(path)
        return out.stdout + out.stderr

    def armed(self, lines):
        """Starts OpenOCD on a script that prints @@ARMED once its breakpoints are set, and
        returns when it has. finish() gives what it printed once it is over."""
        path = self.script(lines)
        process = subprocess.Popen(OPENOCD + ['-f', path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out, ready = [], threading.Event()

        def reader():
            for line in process.stdout:
                out.append(line)
                if '@@ARMED' in line:
                    ready.set()
            ready.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        ready.wait(30)

        def finish():
            try:
                process.wait(timeout=PATIENCE + 120)
            except subprocess.TimeoutExpired:
                process.kill()
            thread.join(5)
            os.unlink(path)
            return ''.join(out)

        return ('@@ARMED' in ''.join(out)), finish

    def watch_start(self):
        """A reset, and what the start that follows is caught doing."""
        return (['bp 0x%x 2 hw' % a for a in self.watch] + ['reset run', 'set caught 0',
                'for {set i 0} {$i < 4} {incr i} {', '  if {[catch {wait_halt 3500}]} { break }',
                '  incr caught', '  echo "C${caught}PC=[lindex [reg pc] 2]"', '  resume', '}']
                + ['catch {rbp 0x%x}' % a for a in self.watch])

    def catches(self, text):
        events = []
        for i in (1, 2, 3):
            pc = reg(text, 'C%dPC' % i)
            if pc is not None:
                events.append({self.new_irk: 'CAUGHT drawing a NEW IRK', self.error: 'CAUGHT in Error_Handler',
                               self.recover: 'state file missing, new file put in its place',
                               self.give_up: 'second file given up, state rewritten in place'}.get(pc & ~1, self.where(pc)))
        return ', then '.join(events) if events else 'normal'

    def snap(self, tag='s'):
        return Snap(self, self.openocd(['snap ' + tag]), tag)


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


class Snap:
    """What the firmware held at one moment."""
    def __init__(self, build, text, tag):
        a, b = block(text, tag + 'state1'), block(text, tag + 'state2')
        self.ok = len(a) == 28 and a == b
        raw = struct.pack('<28I', *a) if self.ok else bytes(112)
        self.keys = hashlib.sha1(raw[77:109]).hexdigest()[:8] if self.ok else None
        self.temp_folder = struct.unpack_from('<I', raw, 40)[0]
        mode = block(text, tag + 'mode')
        self.mode = MODES[mode[0]] if mode and mode[0] < len(MODES) else '?'
        self.pc = build.where(reg(text, tag + 'PC'))
        self.card = card(block(text, tag + 'fs'))

    def __str__(self):
        return 'mode %s (%s), Temp_Folder %d, %s' % (self.mode, self.pc, self.temp_folder, self.card)


def card(words):
    """The root directory as FatFs last saw it."""
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


def keys_word(reference, keys):
    if keys is None:
        return 'keys unreadable'
    if reference is None:
        return 'keys %s' % keys
    return 'keys kept (%s)' % keys if keys == reference else 'KEYS CHANGED (%s -> %s)' % (reference, keys)


def present(usb_id):
    return subprocess.run(['lsusb', '-d', usb_id], capture_output=True).returncode == 0


def say(action):
    line = '>>>>>>   %s   <<<<<<' % action
    print('\n%s\n%s\n%s\a\n' % ('=' * len(line), line, '=' * len(line)), flush=True)
    subprocess.run(['notify-send', '-t', '5000', 'FlySight test', action], capture_output=True)


def cable(plugged):
    """Asks for the cable to be moved, unless it is already where it has to be, and waits for it."""
    if present(FLYSIGHT) == plugged:
        return
    say('PLUG the USB cable of the FlySight back IN' if plugged else 'PULL the USB cable OUT of the FlySight')
    start = time.time()
    while present(FLYSIGHT) != plugged:
        if time.time() - start > PATIENCE:
            raise SystemExit('nobody moved the cable in %d s: stopping' % PATIENCE)
        time.sleep(0.1)
    log('cable %s, seen %.0f s after the request' % ('in' if plugged else 'out', time.time() - start))
    if not present(STLINK):
        say('The ST-LINK is gone from the USB bus: plug it back in')
        while not present(STLINK):
            time.sleep(0.5)
        time.sleep(2)


def volume():
    """The card as the PC sees it: (device, mount point), or None."""
    found = None
    for line in open('/proc/mounts'):
        device, mount = line.split()[:2]
        mount = mount.replace('\\040', ' ')
        try:
            if '/media/' in mount and os.path.exists(os.path.join(mount, 'CONFIG.TXT')):
                found = (device, mount)
        except OSError:
            pass
    return found


def host(seconds=40):
    """What the PC reads on the card, once it has mounted it."""
    end = time.time() + seconds
    while time.time() < end:
        v = volume()
        if v:
            try:
                return read_card(v[1])
            except OSError:
                pass
        time.sleep(0.5)
    return {'text': 'card not mounted on the PC'}


def read_card(mount):
    path = os.path.join(mount, 'FLYSIGHT.TXT')
    if not os.path.exists(path):
        return {'text': 'NO FLYSIGHT.TXT on the card'}
    data = open(path, 'rb').read()
    fields = {k.decode(): v.decode().strip() for k, v in re.findall(rb'^([A-Za-z_]+):[ \t]*([^;\r\n]*)', data, re.M)}
    try:
        keys = hashlib.sha1(bytes.fromhex(fields['BLE_IRK']) + bytes.fromhex(fields['BLE_ERK'])).hexdigest()[:8]
    except Exception:
        keys = None
    st = os.statvfs(mount)
    result = {'bytes': len(data), 'keys': keys, 'temp': fields.get('Temp_Folder', '?'), 'active': fields.get('Active_Mode', '?'),
              'tmp': os.path.exists(os.path.join(mount, 'FLYSIGHT.TMP')), 'digest': hashlib.sha1(data).hexdigest()[:8], 'fields': fields}
    result['text'] = 'FLYSIGHT.TXT of %d bytes (contents %s), %s, Temp_Folder %s, Active_Mode %s, FLYSIGHT.TMP %s, %d KB free' % (
        len(data), result['digest'], 'keys %s' % keys if keys else 'NO KEYS', result['temp'], result['active'],
        'PRESENT' if result['tmp'] else 'absent', st.f_bavail * st.f_frsize // 1024)
    return result


def flash(build):
    out = subprocess.run(OPENOCD + ['-c', 'program {%s} verify reset exit' % build.elf], capture_output=True, text=True, timeout=240)
    text = out.stdout + out.stderr
    return 'flashed and verified' if 'Verified OK' in text else 'FLASH FAILED: ' + ' | '.join(l for l in text.splitlines() if 'Error' in l)[:300]


def plugged_in(build, reference):
    """Cable in: what the PC reads, and what the firmware holds."""
    cable(True)
    pc = host()
    time.sleep(2)
    s = build.snap()
    log('%s | cable in: the PC reads %s | firmware: %s, %s' % (build.name, pc['text'], s, keys_word(reference, s.keys)))
    return pc, s


def free_pull(build, reference):
    """Steps 2 to 4: a pull that nothing slows down, then what the PC reads once plugged back in."""
    before, s = plugged_in(build, reference)
    reference = reference or s.keys
    subprocess.run(['sync'])
    time.sleep(1)
    cable(False)
    time.sleep(SETTLE)
    back = present(FLYSIGHT)
    s = build.snap()
    log('%s | PULL 1, card mounted on the PC, firmware left alone: %d s later %s, %s%s' % (build.name, SETTLE, s, keys_word(reference, s.keys),
        ' | NOT A VALID READING: the cable was back in before it' if back else ''))
    return before, reference


def compare(build, before, after):
    if 'fields' not in before or 'fields' not in after:
        log('%s | plugged back in: the PC reads %s -> NOTHING TO COMPARE' % (build.name, after['text']))
        return
    a, b = before['fields'], after['fields']
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    # the two versions of the radio firmware read 8.0.0 until the second processor has started,
    # so a file written at start-up and a file written later differ there; their values are no secret
    versions = ['%s %s -> %s' % (k, a.get(k), b.get(k)) for k in changed if k in ('FUS_Ver', 'Stack_Ver')]
    others = [k for k in changed if k not in ('FUS_Ver', 'Stack_Ver')]
    verdict = 'THE SAME FILE as before the pull' if not changed else \
              'the same as before the pull but for %s' % ', '.join(versions) if not others else \
              'NOT THE SAME as before the pull: %s differ' % ', '.join(others + versions)
    log('%s | plugged back in: the PC reads %s -> %s%s' % (build.name, after['text'], verdict,
        '' if before['tmp'] == after['tmp'] else ' | FLYSIGHT.TMP appeared or went'))


def timed_pull(build, reference):
    """Step 5: the PC ejects the card, then a pull with FS_State_Write() timed."""
    v = volume()
    if v:
        subprocess.run(['sync'])
        out = subprocess.run(['udisksctl', 'unmount', '-b', v[0]], capture_output=True, text=True)
        log('%s | the PC ejects the card: %s' % (build.name, (out.stdout + out.stderr).strip()[:120]))
    ready, finish = build.armed(['bp 0x%x 2 hw' % build.write, 'echo "@@ARMED"', 'set n 0',
        'if {[catch {wait_halt %d}]} { echo "@@NOWRITE" } else {' % (PATIENCE * 1000),
        '  while {1} {', '    incr n', '    set lr [expr {[lindex [reg lr] 2] & ~1}]',
        '    echo "@@w${n}a"; echo [mdw 0x%x 1]; echo "@@end"' % build.tick,
        '    bp $lr 2 hw', '    resume',
        '    if {[catch {wait_halt 60000}]} { echo "@@STUCK"; break }',
        '    echo "@@w${n}b"; echo [mdw 0x%x 1]; echo "@@end"' % build.tick,
        '    rbp $lr', '    resume',
        '    if {[catch {wait_halt 4000}]} { break }', '  }', '}',
        'catch {rbp 0x%x}' % build.write, 'snap b'])
    if not ready:
        log('%s | PULL 2 NOT RUN: OpenOCD did not start: %s' % (build.name, finish()[-300:]))
        return
    cable(False)
    text = finish()
    times = []
    for n in range(1, 9):
        a, b = block(text, 'w%da' % n), block(text, 'w%db' % n)
        if a and b:
            times.append('%d ms' % (b[0] - a[0]))
        elif a:
            times.append('not finished')
    s = Snap(build, text, 'b')
    what = 'no write seen' if '@@NOWRITE' in text else '%d write%s of the state file: %s' % (len(times), '' if len(times) == 1 else 's', ', '.join(times))
    log('%s | PULL 2, card ejected first, FS_State_Write() timed: %s; then %s, %s' % (build.name, what, s, keys_word(reference, s.keys)))


def reset_pull(build, reference):
    """Step 6: a pull, and a reset just after the file to write is opened."""
    before, s = plugged_in(build, reference)
    subprocess.run(['sync'])
    ready, finish = build.armed(['bp 0x%x 2 hw' % build.midwrite, 'echo "@@ARMED"',
        'if {[catch {wait_halt %d}]} { echo "@@NOWRITE"; rbp 0x%x; exit }' % (PATIENCE * 1000, build.midwrite),
        'echo "STOPPC=[lindex [reg pc] 2]"', 'rbp 0x%x' % build.midwrite]
        + build.watch_start() + ['sleep 4000', 'snap c'])
    if not ready:
        log('%s | PULL 3 NOT RUN: OpenOCD did not start: %s' % (build.name, finish()[-300:]))
        return reference
    cable(False)
    text = finish()
    if '@@NOWRITE' in text:
        log('%s | PULL 3: no write seen, no reset sent' % build.name)
        return reference
    c = Snap(build, text, 'c')
    log('%s | PULL 3, reset during the write: firmware stopped in %s, reset; next start: %s; %s; %s'
        % (build.name, build.where(reg(text, 'STOPPC')), build.catches(text), keys_word(reference, c.keys), c))
    return c.keys or reference


def main():
    args = sys.argv[1:]
    if args and args[0] == '--check':
        for spec in args[1:]:
            b = Build(spec)
            print('%s: %s the fix, FS_State_Write 0x%x, reset point 0x%x, state 0x%x, fs 0x%x%s'
                  % (b.name, 'WITH' if b.fixed else 'WITHOUT', b.write, b.midwrite, b.state, b.fs, ', with a reset' if b.reset else ''))
        return
    running = None
    if args and args[0] == '--running':
        running, args = Build(args[1]), args[2:]
    builds = [Build(spec) for spec in args]
    reference = None
    if running:
        log('######## %s, the build that is running (%s the fix)' % (running.name, 'with' if running.fixed else 'without'))
        _, reference = free_pull(running, reference)
    for b in builds:
        log('######## %s (%s the fix)' % (b.name, 'with' if b.fixed else 'without'))
        cable(False)
        time.sleep(SETTLE)
        result = flash(b)
        time.sleep(5)
        s = b.snap()
        log('%s | %s on the battery; after its start: %s, %s' % (b.name, result, s, keys_word(reference, s.keys)))
        reference = s.keys or reference
        before, reference = free_pull(b, reference)
        cable(True)
        compare(b, before, host())
        time.sleep(2)
        timed_pull(b, reference)
        if b.reset:
            reference = reset_pull(b, reference)
    last = builds[-1] if builds else running
    log('######## the end: %s stays in the FlySight' % last.name)
    pc, s = plugged_in(last, reference)
    log('REAL_UNPLUG_DONE')


main()
