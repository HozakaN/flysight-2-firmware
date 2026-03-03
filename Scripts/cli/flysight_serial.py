"""
Shared serial communication helper for FlySight CLI.

Provides the FlySightCLI class for connecting to a FlySight device
over USB CDC serial and sending/receiving CLI commands.
"""

import glob
import sys
import time

import serial


def find_flysight_port():
    """Auto-detect the FlySight serial port."""
    if sys.platform == "darwin":
        ports = glob.glob("/dev/tty.usbmodem*")
    elif sys.platform.startswith("linux"):
        ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    elif sys.platform == "win32":
        ports = [f"COM{i}" for i in range(1, 20)]
    else:
        ports = []

    for port in ports:
        try:
            s = serial.Serial(port, 115200, timeout=0.5)
            s.close()
            return port
        except (serial.SerialException, OSError):
            continue

    return None


class FlySightCLI:
    """Interface to the FlySight CLI over USB CDC serial."""

    PROMPT = "> "
    EOF = b"\x04"

    def __init__(self, port=None, baudrate=115200, timeout=2):
        if port is None:
            port = find_flysight_port()
            if port is None:
                raise RuntimeError("No FlySight serial port found. Use --port to specify.")
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        # Drain any pending data
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def _read_until_prompt(self, timeout=10):
        """Read serial data until we see the '> ' prompt."""
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if buf.endswith(b"> "):
                    return buf[:-2]  # Strip the prompt
            else:
                time.sleep(0.01)
        return buf

    def send_command(self, cmd):
        """Send a command and return the full response text."""
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r\n").encode())

        # Read back echo of the command + response
        raw = self._read_until_prompt()
        text = raw.decode("utf-8", errors="replace")

        # Strip echo: first line is the echoed command
        lines = text.split("\r\n", 1)
        if len(lines) > 1:
            return lines[1]
        return text

    def read_file_command(self, cmd):
        """
        Send a read command and return the file content.

        Expects firmware response in format:
            === /path ===
            <content>
            === END ===
        """
        response = self.send_command(cmd)

        # Find content between === markers
        lines = response.split("\n")
        content_lines = []
        capturing = False

        for line in lines:
            stripped = line.rstrip("\r")
            if stripped.startswith("=== ") and stripped.endswith(" ===") and "END" not in stripped:
                capturing = True
                continue
            if stripped == "=== END ===":
                capturing = False
                continue
            if capturing:
                content_lines.append(stripped)

        return "\n".join(content_lines)

    def read_track_latest(self):
        """
        Send 'track latest' and parse the multi-file response.

        Returns:
            tuple: (track_path, files_dict)
                track_path: str like "/25-02-28/14-30-00"
                files_dict: dict of {filename: content}
        """
        response = self.send_command("track latest")
        lines = response.split("\n")

        track_path = None
        files = {}
        current_file = None
        current_lines = []

        for line in lines:
            stripped = line.rstrip("\r")

            # Parse "Latest track: /25-02-28/14-30-00"
            if stripped.startswith("Latest track: "):
                track_path = stripped[len("Latest track: "):]
                continue

            # Parse "=== /path/file.csv ==="
            if stripped.startswith("=== ") and stripped.endswith(" ==="):
                # Save previous file
                if current_file is not None:
                    files[current_file] = "\n".join(current_lines)

                if "END" in stripped:
                    current_file = None
                    current_lines = []
                    continue

                # Check for "(binary, skipped)"
                if "(binary, skipped)" in stripped:
                    continue

                # Extract path
                path = stripped[4:-4].strip()
                # Get just the filename
                current_file = path.rsplit("/", 1)[-1] if "/" in path else path
                current_lines = []
                continue

            if current_file is not None:
                current_lines.append(stripped)

        # Save last file
        if current_file is not None:
            files[current_file] = "\n".join(current_lines)

        return track_path, files

    def write_file_command(self, cmd, content):
        """
        Send a write command and transfer file content.

        Protocol:
            1. Send command
            2. Wait for "READY"
            3. Send file content
            4. Send EOF (Ctrl-D)
            5. Wait for "OK: Written N bytes"

        Returns the firmware response string.
        """
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r\n").encode())

        # Wait for READY
        deadline = time.time() + 5
        buf = b""
        while time.time() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if b"READY" in buf:
                    break
            else:
                time.sleep(0.01)

        if b"READY" not in buf:
            text = buf.decode("utf-8", errors="replace")
            raise RuntimeError(f"Did not receive READY response: {text}")

        # Small delay to let firmware settle
        time.sleep(0.05)

        # Send file content in chunks
        data = content.encode("utf-8") if isinstance(content, str) else content
        chunk_size = 64
        for i in range(0, len(data), chunk_size):
            self.ser.write(data[i:i + chunk_size])
            time.sleep(0.01)  # Pace to avoid overflowing ring buffer

        # Send EOF
        self.ser.write(self.EOF)

        # Read response
        raw = self._read_until_prompt()
        return raw.decode("utf-8", errors="replace")


def add_port_argument(parser):
    """Add the --port argument to an argparse parser."""
    parser.add_argument(
        "--port", "-p",
        default=None,
        help="Serial port (e.g., /dev/tty.usbmodemXXXX). Auto-detected if omitted.",
    )
