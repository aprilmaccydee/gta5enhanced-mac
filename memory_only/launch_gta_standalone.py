#!/usr/bin/env python3
"""Directly launch the pinned standalone build and apply the graphics/core profile.

Usage: python3 memory_only/launch_gta_standalone.py [--lml]
Uses the existing CrossOver bottle and fixed game path, without launching
the Rockstar UI or accepting arbitrary game arguments. The two tested Story
Mode flags are fixed. --lml opts into the pinned development bootstrap.
No persistent settings are edited.
"""
import sys
import watch_gta_standalone


if __name__ == "__main__":
    try:
        raise SystemExit(watch_gta_standalone.main(direct_launch=True))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
