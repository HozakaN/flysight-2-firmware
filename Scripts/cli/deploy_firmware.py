#!/usr/bin/env python3
"""
Deploy firmware (.sfb) to a FlySight device via USB serial CLI.

Usage:
    python deploy_firmware.py firmware.sfb
    python deploy_firmware.py --port /dev/tty.usbmodemXXXX firmware.sfb
"""

import argparse
import os
import sys

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Deploy firmware to FlySight")
    parser.add_argument("firmware", help="Path to .sfb firmware file")
    add_port_argument(parser)
    args = parser.parse_args()

    if not args.firmware.lower().endswith(".sfb"):
        print("Warning: File does not have .sfb extension.", file=sys.stderr)

    with open(args.firmware, "rb") as f:
        data = f.read()

    size = len(data)
    print(f"Uploading {os.path.basename(args.firmware)} ({size} bytes)...")

    cli = FlySightCLI(port=args.port)
    try:
        def progress(sent, total):
            pct = sent * 100 // total
            print(f"\r  {sent}/{total} bytes ({pct}%)", end="", flush=True)

        response = cli.write_binary_command(
            f"fw upload {size}", data, progress_cb=progress
        )
    finally:
        cli.close()

    print()  # newline after progress
    print(response.strip())


if __name__ == "__main__":
    main()
