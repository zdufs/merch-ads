#!/usr/bin/env python3
"""The merged product catalogue, banked into a table so it is read once.

The catalogue is not one file. Snap for MOD exports at most 100k rows per file
and the account has ~1.3M live listings, so it arrives in chunks and
`export_reader.catalog_rows()` merges them at read time — newest file wins for
any listing that appears twice.

That merge costs about twenty seconds over 2.0 million listings, and the nightly
performs it roughly twenty times (derive_econ, map_products and demand_feed,
once per market). Seven minutes a night re-parsing bytes that did not change.

This banks the merge instead.

THE RULE THAT MAKES IT SAFE
---------------------------
It is a PURE OPTIMISATION. The table is keyed by a signature over the export
files — name, mtime AND size — and `read()` returns None whenever that
signature does not match what is on disk. The reader then falls back to the
CSVs. So a cache that is stale, missing, or corrupt makes a read SLOW. It can
never make a read WRONG.

That property is not decoration. This engine has already lost a whole cohort to
a stale file quietly standing in for a fresh one: the 2026-08-22 Drinkware
import built zero ads and reported "Complete". A cache is exactly that shape of
risk, so it is built to be unable to take it.

Nothing here calls Amazon and nothing here is authoritative. Delete
`catalog_cache.sqlite` at any time; the next read is merely slower.

Usage:
  python3 catalog_cache.py              # status
  python3 catalog_cache.py --auto       # build only when the files changed
  python3 catalog_cache.py --rebuild    # build unconditionally
"""

import os
import sqlite3
import sys

import paths

DB_NAME = "catalog_cache.sqlite"

# Every field a catalogue row carries — exactly what export_reader._snap_row()
# produces, plus the two the merge itself adds. The column names ARE the row
# keys, so a round trip is dict(zip(FIELDS, row)) and there is no mapping table
# to get wrong.
#
# All seven callers of catalog_rows() read only from this set, and
# `tests/catalog_cache_tests.py` fails if one of them starts reading a field
# that is not banked here. A field that is missing would read as None rather
# than raise, which is how an economics rule silently stops seeing a price.
#
# It also fails on a read that names its field with a VARIABLE, because such a
# read cannot be judged at all — and being unjudgeable, it used to be skipped.
FIELDS = ("marketplace", "asin", "status", "productType", "productTypeLabel",
          "productTitle", "adAsins", "brandName", "listPrice",
          "salesTotal", "royaltyTotal", "salesLast30", "royaltyLast30",
          "createdDate", "designId", "firstSaleDate", "lastSaleDate",
          "returnedTotal", "_source", "_as_of")

# `_source` and `_as_of` name the chunk a listing came from, so across two
# million rows they are four filenames and four dates repeated two million
# times. Stored inline they cost 103 MB of the database on the live catalogue —
# more than any real per-listing field. They live in `catalog_file` instead and
# each row keeps a small integer, which the read expands back. The rows a caller
# receives are unchanged; only the storage differs.
_STORED = tuple(f for f in FIELDS if not f.startswith("_"))

# `_stream` rebuilds those two by hand. Any OTHER underscored field would be
# dropped from the table by the line above AND never put back by the read, so it
# would simply not exist on a cached row — no column, no error, no None even,
# just a key that is there when the CSVs are read and gone when they are not.
# That is a bug whose appearance depends on whether the cache happens to be
# fresh, which is the hardest kind to be handed. Fail at import instead.
_EXPANDED = {"_source", "_as_of"}
_underscored = {f for f in FIELDS if f.startswith("_")}
if _underscored != _EXPANDED:
    raise RuntimeError(
        f"catalog_cache.FIELDS has underscored fields {sorted(_underscored)} but "
        f"_stream only rebuilds {sorted(_EXPANDED)}. Either store the new field "
        f"under a plain name, or teach _stream and catalog_file about it.")

_COLS = ", ".join(f'"{f}" TEXT' for f in _STORED)
_NAMES = ", ".join(f'"{f}"' for f in _STORED)
_MARKS = ", ".join("?" for _ in _STORED) + ", ?"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS catalog ({_COLS}, file_id INTEGER);
-- The merge already emits each listing once. This makes that a promise the
-- database keeps rather than one the caller remembers: a build that would
-- double a listing fails instead of quietly serving it twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_key ON catalog(marketplace, asin);
CREATE INDEX IF NOT EXISTS idx_catalog_mkt ON catalog(marketplace);
CREATE TABLE IF NOT EXISTS catalog_file (
    id INTEGER PRIMARY KEY,
    source TEXT,
    as_of TEXT
);
CREATE TABLE IF NOT EXISTS cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

BULK_CHUNK = 20000


def db_path():
    """Where the cache lives: the DATA folder, beside the market databases.

    Not the POD folder. The exports there are the operator's source files and
    this is derived state, which belongs with the rest of the derived state.
    Not inside ads_data.sqlite either: the catalogue is global — one grid
    holding seven marketplaces — so filing it under the US market database
    would make US own every other market's listings. Marketing Stream keeps its
    own file for the same reason.
    """
    return paths.repo(DB_NAME)


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def signature(folder=None):
    """What the banked catalogue is a copy OF: every export's name, mtime AND
    size. None when the folder holds no export at all.

    This is deliberately NOT `export_reader.catalog_signature`, which carries
    name and mtime only. That is right for the economics gate, which asks
    whether the mapping is out of date, and it is not enough here.

    Both mtime and the timestamp inside an export's filename have one-second
    resolution. So a chunk being COPIED into the folder while this reads it
    banks a truncated catalogue — and if the copy then finishes inside the same
    second, the mtime never moves and the signature still matches. The cache
    would serve a partial catalogue and report itself current. Nothing would ask
    for the fallback, because nothing would know anything was wrong. A file that
    grew is a different file, and size says so for free.
    """
    import export_reader
    parts = []
    for path in sorted(export_reader.catalog_files(folder)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        parts.append(f"{os.path.basename(path)}|{int(st.st_mtime)}|{st.st_size}")
    return ";".join(parts) or None


def _connect(path=None, create=True):
    path = path or db_path()
    if not create and not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _meta(conn, key):
    row = conn.execute("SELECT value FROM cache_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def build(folder=None, path=None, verbose=False):
    """Bank the merged catalogue. Returns the number of listings stored.

    Rebuilds from scratch every time. An incremental update would have to know
    which listings a new chunk supersedes, which is the merge itself — and
    getting it subtly wrong is how a cache starts serving a price that is no
    longer on any file.
    """
    import export_reader
    folder = folder or paths.POD_ROOT
    sig = signature(folder)
    # Banking an empty catalogue saves nothing — with no exports the CSV path
    # returns nothing either — and it invents a state worth avoiding: a cache
    # reporting `matches: true` while holding zero listings, which reads as an
    # account with no products. Worse, it would REPLACE a perfectly good cache
    # because the exports were moved aside for a moment. Leave what is there;
    # the signature no longer matches, so every read falls back on its own.
    if sig is None:
        if verbose:
            print("no product export in the folder — catalogue cache left alone",
                  file=sys.stderr)
        return 0
    conn = _connect(path)
    insert = f"INSERT INTO catalog ({_NAMES}, file_id) VALUES ({_MARKS})"
    try:
        conn.execute("DELETE FROM catalog")
        conn.execute("DELETE FROM catalog_file")
        files, stored, batch = {}, 0, []
        # `_catalog_rows_csv`, never `catalog_rows`. The public reader consults
        # this cache, so building through it would read our own table and bank
        # it straight back whenever the signature still matched — the cache
        # rebuilt FROM the cache. Every value would be identical, so nothing
        # would look wrong, and `--rebuild` would silently lose the ability to
        # repair anything. It is the one escape hatch; it has to reach the files.
        # A chunk that cannot be read is SKIPPED by the reader, and this build
        # used to pass no collector, so it never learned that one had been. It
        # then banked a signature covering the unreadable file, `read()` matched
        # it, and map_products stamped a complete mapping over a catalogue that
        # was missing a chunk. Those listings lose their price and royalty
        # silently, which exempts them from every economics rule.
        skipped = []
        for row in export_reader._catalog_rows_csv(folder, skipped=skipped):
            origin = (row.get("_source"), row.get("_as_of"))
            if origin not in files:
                files[origin] = len(files) + 1
            batch.append(tuple(row.get(f) for f in _STORED) + (files[origin],))
            if len(batch) >= BULK_CHUNK:
                conn.executemany(insert, batch)
                stored += len(batch)
                batch = []
        if batch:
            conn.executemany(insert, batch)
            stored += len(batch)
        if skipped:
            # Bank nothing. The cache is a PURE optimisation, so refusing to
            # build costs seconds and never costs an answer: every reader falls
            # back to the CSVs, which skip the same chunk but say so through
            # their own collector. Banking a partial catalogue under a matching
            # signature is the one outcome that hides it.
            conn.rollback()
            print(f"catalogue NOT cached: {len(skipped)} chunk(s) could not be "
                  f"read — falling back to the CSVs so the gap stays visible",
                  file=sys.stderr)
            for chunk in skipped:
                print(f"  skipped: {chunk}", file=sys.stderr)
            return 0
        conn.executemany("INSERT INTO catalog_file (id, source, as_of) VALUES (?,?,?)",
                         [(i, src, as_of) for (src, as_of), i in files.items()])
        conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('signature', ?)",
                     (sig,))
        conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('built_at', ?)",
                     (_now(),))
        conn.execute("INSERT OR REPLACE INTO cache_meta VALUES ('rows', ?)",
                     (str(stored),))
        conn.commit()
    finally:
        conn.close()
    if verbose:
        print(f"catalogue cached: {stored:,} listings", file=sys.stderr)
    return stored


def read(folder=None, marketplace=None, path=None):
    """The banked catalogue, or None when the cache cannot be trusted.

    None is the whole safety mechanism: every caller falls back to the CSVs on
    it, so "cannot be trusted" costs twenty seconds and never an answer.
    """
    import export_reader
    folder = folder or paths.POD_ROOT
    path = path or db_path()
    if not os.path.exists(path):
        return None
    try:
        conn = _open_ro(path)
        try:
            banked = _meta(conn, "signature")
        finally:
            conn.close()
    except sqlite3.Error:
        # Missing tables, a malformed file, a database being rebuilt underneath
        # us — all the same answer. The CSVs are still there.
        return None
    if banked != signature(folder):
        return None
    return _stream(path, marketplace)


def _open_ro(path):
    """Read-only, through the engine's own helper.

    This file is in WAL mode, and SQLite deletes the `-wal` and `-shm` sidecars
    when the last connection closes — so it sits sidecar-less most of the day. A
    WAL database with no `-shm` cannot always be opened `mode=ro`: SQLite has to
    create that shared-memory index first and a read-only connection may not.
    The open SUCCEEDS and the first query fails.

    Here that failure would be invisible. `read()` catches sqlite3.Error and
    returns None, which is the fallback path, so the cache would simply never be
    used and every read would quietly go back to parsing 1.1 GB — correct
    answers, none of the speed, and nothing to see. `db.open_readonly` already
    solves this for the market databases: try read-only, and on that failure
    open read-write and set `query_only`, after which SQLite refuses every
    write on the handle.
    """
    import db
    conn = db.open_readonly(path, busy_timeout=5000)
    return conn


def _stream(path, marketplace):
    """Rows in the order catalog_rows would have yielded them.

    rowid is insertion order and the build inserts in merge order, so this
    reproduces newest-file-first with each file's own row order inside it. A
    caller that takes the first row it sees for an ASIN behaves identically.

    The connection is opened HERE rather than in `read()`, so a caller that asks
    whether a cache exists and never iterates leaves no open handle behind. It
    also means an open that fails RAISES rather than yielding an empty
    catalogue: by this point `read()` has already decided the cache is good, and
    a silent zero-row catalogue is the one answer that must never be possible —
    every economics rule would read it as an account with no products.
    """
    sql = f"SELECT {_NAMES}, file_id FROM catalog"
    args = ()
    if marketplace:
        sql += " WHERE marketplace=?"
        args = (marketplace,)
    sql += " ORDER BY rowid"
    conn = _open_ro(path)
    try:
        # Four rows. Read once and expand in Python rather than joining two
        # million times.
        origin = {r[0]: (r[1], r[2]) for r in
                  conn.execute("SELECT id, source, as_of FROM catalog_file")}
        blank = (None, None)
        for row in conn.execute(sql, args):
            out_row = dict(zip(_STORED, row))
            out_row["_source"], out_row["_as_of"] = origin.get(row[-1], blank)
            yield out_row
    finally:
        conn.close()


def status(folder=None, path=None):
    """What the cache holds and whether it still matches the files on disk."""
    import export_reader
    folder = folder or paths.POD_ROOT
    path = path or db_path()
    live = signature(folder)
    files = [os.path.basename(p) for p in export_reader.catalog_files(folder)]
    if not os.path.exists(path):
        return {"available": False, "matches": False, "rows": 0,
                "built_at": None, "files": files,
                "note": "no cache yet — reads parse the CSVs"}
    try:
        conn = _open_ro(path)
        try:
            banked = _meta(conn, "signature")
            rows = int(_meta(conn, "rows") or 0)
            built = _meta(conn, "built_at")
        finally:
            conn.close()
    except (sqlite3.Error, ValueError) as exc:
        return {"available": False, "matches": False, "rows": 0,
                "built_at": None, "files": files,
                "note": f"cache unreadable ({exc}) — reads parse the CSVs"}
    matches = banked == live
    return {"available": True, "matches": matches, "rows": rows,
            "built_at": built, "files": files,
            "note": None if matches
                    else "the exports changed — reads parse the CSVs until rebuilt"}


def main(argv):
    folder = paths.POD_ROOT
    if "--rebuild" in argv:
        print(f"building from {folder}…")
        print(f"cached {build(folder):,} listings")
        return 0
    if "--auto" in argv:
        st = status(folder)
        if st["available"] and st["matches"]:
            print(f"catalogue cache is current ({st['rows']:,} listings, "
                  f"built {st['built_at']})")
            return 0
        print(f"building from {folder}…")
        print(f"cached {build(folder):,} listings")
        return 0
    st = status(folder)
    for key in ("available", "matches", "rows", "built_at", "note"):
        print(f"  {key:10} {st[key]}")
    print(f"  files      {len(st['files'])}")
    for name in st["files"]:
        print(f"    {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
