#!/usr/bin/env python3
"""
Enable mass storage mode on the FlySight device.

Sets Competition_Mode: 0 in flysight.txt so the next USB replug will boot in mass storage mode.

Usage:
    python enable_msc.py [--port /dev/tty.usbmodemXXXX]
"""

import argparse

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Enable mass storage on next USB replug")
    add_port_argument(parser)
    args = parser.parse_args()

    cli = FlySightCLI(port=args.port)
    try:
        response = cli.send_command("msc")
    finally:
        cli.close()

    print(response.strip())


if __name__ == "__main__":
    main()
