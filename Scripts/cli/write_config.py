#!/usr/bin/env python3
"""
Write config.txt (logging configuration) to a FlySight device.

Usage:
    python write_config.py [--port /dev/tty.usbmodemXXXX] <input-file>
"""

import argparse
import sys

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Write config.txt to FlySight")
    add_port_argument(parser)
    parser.add_argument(
        "input_file",
        help="Path to the config.txt file to upload",
    )
    args = parser.parse_args()

    with open(args.input_file, "r") as f:
        content = f.read()

    print(f"Uploading {args.input_file} ({len(content)} bytes)...")

    cli = FlySightCLI(port=args.port)
    try:
        response = cli.write_file_command("config write", content)
    finally:
        cli.close()

    print(response.strip())


if __name__ == "__main__":
    main()
