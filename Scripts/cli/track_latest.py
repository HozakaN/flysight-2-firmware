#!/usr/bin/env python3
"""
Download the latest track files from a FlySight device.

Usage:
    python track_latest.py [--port /dev/tty.usbmodemXXXX] [--output-dir ./tracks]
"""

import argparse
import os
import sys

from flysight_serial import FlySightCLI, add_port_argument


def main():
    parser = argparse.ArgumentParser(description="Download latest track from FlySight")
    add_port_argument(parser)
    parser.add_argument(
        "--output-dir", "-o",
        default=".",
        help="Output directory (default: current directory)",
    )
    args = parser.parse_args()

    cli = FlySightCLI(port=args.port)
    try:
        track_path, files = cli.read_track_latest()
    finally:
        cli.close()

    if track_path is None:
        print("No track found on device.", file=sys.stderr)
        sys.exit(1)

    # Create output directory: <output-dir>/<date>/<time>/
    # track_path is like "/25-02-28/14-30-00"
    parts = track_path.strip("/").split("/")
    out_dir = os.path.join(args.output_dir, *parts)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Track: {track_path}")
    print(f"Output: {out_dir}")

    for filename, content in files.items():
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "w") as f:
            f.write(content)
        print(f"  Saved: {filename} ({len(content)} bytes)")

    if not files:
        print("  No files found in track.", file=sys.stderr)


if __name__ == "__main__":
    main()
