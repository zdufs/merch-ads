#!/usr/bin/env python3
"""The three directories the engine resolves everything else against.

Modules used to derive these from their own ``__file__``, which meant every file
that touched data carried its own copy of "where is the repo". When the modules
moved into this package those copies would each have shifted by one level —
silently, since a wrong path does not raise, it just reads an empty database or
writes an output nobody looks at.

  ENGINE_DIR  this package — the Python modules
  REPO_ROOT   the repository: .env, the SQLite databases, KILL, outputs/,
              seasonal.json and the other operator config
  POD_ROOT    the folder ABOVE the repository, where the Merch catalogue exports
              and the dated SALES_REPORT files are dropped

Import these rather than recomputing. `tests/conftest`-style path juggling and a
future move both stay correct if there is exactly one definition.
"""

import os

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(ENGINE_DIR)
POD_ROOT = os.path.dirname(REPO_ROOT)


def repo(*parts):
    """Path inside the repository, e.g. repo("outputs", "halo.csv")."""
    return os.path.join(REPO_ROOT, *parts)


def pod(*parts):
    """Path in the folder above the repository (catalogue exports, sales reports)."""
    return os.path.join(POD_ROOT, *parts)
