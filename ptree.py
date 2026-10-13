#!/usr/bin/env python3
"""Entry point: python ptree.py projects | wake <project> | find <project> "<question>" | ..."""
import sys

from ptree.cli import main

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    main()
