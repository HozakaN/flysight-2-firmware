#!/usr/bin/env python3
"""
List the contents of the /temp folder on the FlySight device.

Usage:
    python temp_list.py [--port /dev/tty.usbmodemXXXX]
"""

import argparse

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="List /temp folder contents on FlySight")
    add_port_argument(parser)
    args = parser.parse_args()

    cli = FlySightCLI(port=args.port)
    try:
        response = cli.send_command("temp list")
    finally:
        cli.close()

    print(response.strip())


if __name__ == "__main__":
    main()
