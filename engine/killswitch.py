#!/usr/bin/env python3
"""
Global kill switch. If a file named KILL exists in the Ads folder, every apply
path refuses to write to Amazon (previews still work). Pure safety brake.

  Activate :  touch KILL      (run from this folder)
  Release  :  rm KILL
"""

import os

import paths
import sys

KILL_FILE = os.path.join(paths.REPO_ROOT, "KILL")


def active():
    return os.path.exists(KILL_FILE)


def check():
    """Call right before writing. Exits the script if the kill switch is on."""
    if active():
        print("\n⛔ KILL switch is ON (KILL file present in Ads/). No changes were written.")
        print(f"   Remove it to re-enable:  rm {KILL_FILE}")
        sys.exit(3)
