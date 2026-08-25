#!/usr/bin/env python3
"""The three directories the engine resolves everything else against.

Modules used to derive these from their own ``__file__``, which meant every file
that touched data carried its own copy of "where is the repo". When the modules
moved into this package those copies would each have shifted by one level —
silently, since a wrong path does not raise, it just reads an empty database or
writes an output nobody looks at.

  ENGINE_DIR  this package — the Python modules
  REPO_ROOT   the data: .env, the SQLite databases, KILL, outputs/,
              seasonal.json and the other operator config
  POD_ROOT    the folder holding the Merch catalogue exports and the dated
              SALES_REPORT files

Import these rather than recomputing. `tests/conftest`-style path juggling and a
future move both stay correct if there is exactly one definition.

CODE AND DATA ARE NOT THE SAME FOLDER ANY MORE.
-----------------------------------------------
Deriving the data location from ``__file__`` assumes the modules sit inside the
data folder. That is true for a checkout and false for the Mac app, which ships
this package at ``Merch Ads.app/Contents/Resources/engine`` — where the folder
above the modules is ``Contents/Resources``: real, readable, and holding no
databases whatsoever.

Nothing raised on that. ``appctl metrics`` answered ``{"ok": true, "empty":
true}`` for every market, which is exactly what a brand-new account looks like.
So the caller can name the folders instead:

  MERCHADS_DATA_DIR   overrides REPO_ROOT
  MERCHADS_POD_DIR    overrides POD_ROOT (defaults to the folder above the data)

Unset, both fall back to the old ``__file__`` derivation, so a checkout, the
test suite and the nightly all behave exactly as they did. SET BUT WRONG stops
the process. A typo in a path is the one mistake that reads as "no data yet"
forever, and every guard in this engine that fails closed exists because a
silent empty read once froze real money in place.
"""

import os

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))


def _inside_app_bundle(path):
    """True when path sits inside a macOS .app.

    A bundle is the one place the __file__ fallback is guaranteed wrong AND
    guaranteed not to raise: `Contents/Resources` exists, is readable, and holds
    no databases. That combination is the whole problem — a caller who forgot to
    name the data folder gets a cheerful, empty answer instead of an error. It
    happened the same afternoon the bundling was written: the one-shot spawn
    passed MERCHADS_DATA_DIR and the persistent worker did not, so every screen
    fed by a worker read an empty database while the command line was fine.
    """
    return any(part.endswith(".app") for part in os.path.abspath(path).split(os.sep))


def _rooted(env_name, fallback):
    """Resolve one root from the environment, falling back to the checkout layout.

    Returns a real absolute path. Raises SystemExit when the variable names a
    folder that is not there, and when there is no variable but the fallback
    would land inside an app bundle — never a fallback to a readable-but-empty
    folder, because that is the failure this whole module exists to prevent.
    """
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        if _inside_app_bundle(fallback):
            raise SystemExit(
                f"{env_name} is not set, and the engine is running from inside an app "
                f"bundle ({fallback}), which holds no data. The caller must name the "
                f"folder that holds ads_data.sqlite."
            )
        return fallback              # byte-identical to the old behaviour
    root = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(root):
        raise SystemExit(
            f"{env_name}={raw} is not a folder. Point it at the folder that holds "
            f"ads_data.sqlite, or unset it to use the folder above the engine."
        )
    return root


REPO_ROOT = _rooted("MERCHADS_DATA_DIR", os.path.dirname(ENGINE_DIR))
POD_ROOT = _rooted("MERCHADS_POD_DIR", os.path.dirname(REPO_ROOT))


def repo(*parts):
    """Path inside the data folder, e.g. repo("outputs", "halo.csv")."""
    return os.path.join(REPO_ROOT, *parts)


def pod(*parts):
    """Path in the catalogue folder (Snap exports, dated sales reports)."""
    return os.path.join(POD_ROOT, *parts)
