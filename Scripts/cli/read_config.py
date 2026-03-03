#!/usr/bin/env python3
"""
Read config.txt (logging configuration) from a FlySight device.

Usage:
    python read_config.py [--port /dev/tty.usbmodemXXXX] [--output config.txt]
"""

import argparse
import sys

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Read config.txt from FlySight")
    add_port_argument(parser)
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file (default: print to stdout)",
    )
    args = parser.parse_args()

    cli = FlySightCLI(port=args.port)
    try:
        content = cli.read_file_command("config read")
    finally:
        cli.close()

    if args.output:
        with open(args.output, "w") as f:
            f.write(content)
        print(f"Saved to {args.output} ({len(content)} bytes)")
    else:
        print(content)


if __name__ == "__main__":
    main()
