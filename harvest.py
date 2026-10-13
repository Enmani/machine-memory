#!/usr/bin/env python3
"""Entry point: python harvest.py stats | distill | assemble | nap | sync | show <ref>"""
import io
import sys

from harvest.cli import main

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    main()
