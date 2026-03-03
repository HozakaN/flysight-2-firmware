#!/usr/bin/env python3
"""
Delete all contents of the /temp folder on the FlySight device.

Usage:
    python temp_delete.py [--port /dev/tty.usbmodemXXXX]
"""

import argparse

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Delete /temp folder contents on FlySight")
    add_port_argument(parser)
    args = parser.parse_args()

    cli = FlySightCLI(port=args.port)
    try:
        response = cli.send_command("temp delete")
    finally:
        cli.close()

    print(response.strip())


if __name__ == "__main__":
    main()
