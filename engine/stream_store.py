#!/usr/bin/env python3
"""Where drained Stream messages land: stream_data.sqlite, beside the market DBs.

WHY ITS OWN DATABASE, and not a table in ads_data[_M].sqlite. One SQS queue
serves a whole realm, so a single EU queue carries UK, DE, FR, ES and IT rows
mixed together. Deciding which market DB a message belongs in means reading a
field of the payload — and this engine does not guess at fields it has never
seen. So arrival is separated from interpretation: everything lands here
verbatim, and mapping into per-market rows is a later, testable step against
real messages rather than against a documentation page.

WHY THE PAYLOAD IS STORED WHOLE. The first hour of real data is the only chance
to learn what Amazon actually sends. A schema written in advance would quietly
drop every column we failed to predict, and there is no way to ask Stream for
that hour again — it does not replay.

Deduplication is on the SNS MessageId. SQS guarantees at-least-once delivery, so
the same message WILL arrive twice sooner or later; INSERT OR IGNORE makes the
second one free.
"""

import datetime
import json
import os
import sqlite3

import paths

DB_NAME = "stream_data.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_message (
    message_id        TEXT PRIMARY KEY,
    dataset           TEXT,
    realm             TEXT,
    topic_arn         TEXT,
    published_at      TEXT,
    time_window_start TEXT,
    profile_id        TEXT,
    payload           TEXT NOT NULL,
    received_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_stream_message_window
    ON stream_message(dataset, time_window_start);
CREATE INDEX IF NOT EXISTS idx_stream_message_received
    ON stream_message(received_at);

-- Which advertising account (Amazon entity id) a message belongs to.
-- NOT derivable from marketplace_id: Merch US and KDP US both advertise on
-- ATVPDKIKX0DER, so the marketplace would merge two separate advertisers into
-- one number. Resolved once by looking the advertiser's campaign ids up in each
-- market database, then cached here. See stream_map.py.
CREATE TABLE IF NOT EXISTS stream_advertiser (
    advertiser_id TEXT PRIMARY KEY,
    market        TEXT,
    matched       INTEGER,
    sampled       INTEGER,
    learned_at    TEXT
);

CREATE TABLE IF NOT EXISTS stream_drain_log (
    at            TEXT,
    realm         TEXT,
    dataset       TEXT,
    received      INTEGER,
    banked        INTEGER,
    duplicates    INTEGER,
    confirmations INTEGER,
    note          TEXT
);
"""


def db_path():
    return paths.repo(DB_NAME)


def connect(ro=False):
    """Read-write by default. ro=True never creates the file, so a screen asking
    "has anything arrived?" before setup answers honestly instead of leaving an
    empty database behind that looks like a configured-but-silent Stream."""
    path = db_path()
    if ro:
        if not os.path.exists(path):
            return None
        import db as _db
        return _db.open_readonly(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def store_messages(conn, rows):
    """Bank drained messages. Returns (banked, duplicates).

    rows: dicts with message_id, dataset, realm, topic_arn, published_at,
    time_window_start, profile_id, payload.
    """
    if not rows:
        return 0, 0
    before = conn.execute("SELECT COUNT(*) FROM stream_message").fetchone()[0]
    conn.executemany(
        """INSERT OR IGNORE INTO stream_message
           (message_id,dataset,realm,topic_arn,published_at,time_window_start,
            profile_id,payload,received_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        [(r["message_id"], r.get("dataset"), r.get("realm"), r.get("topic_arn"),
          r.get("published_at"), r.get("time_window_start"), r.get("profile_id"),
          r["payload"], _now()) for r in rows])
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM stream_message").fetchone()[0]
    banked = after - before
    return banked, len(rows) - banked


def log_drain(conn, realm, dataset, received, banked, duplicates, confirmations, note=""):
    conn.execute(
        """INSERT INTO stream_drain_log
           (at,realm,dataset,received,banked,duplicates,confirmations,note)
           VALUES(?,?,?,?,?,?,?,?)""",
        (_now(), realm, dataset, received, banked, duplicates, confirmations, note))
    conn.commit()


def drain_backlog(conn, realm=None):
    """Queues whose newest drain could not empty them, as "realm/dataset".

    A drain that RAN but could not empty its queue looks perfectly healthy from
    every other angle: recent, and with plenty of messages banked. The note is
    the only place that says the queue was still full, and a backlog compounds
    until SQS drops the oldest messages for good.

    Per QUEUE, not at one timestamp. Queues are drained one after another, so a
    single global MAX(at) is the moment the LAST queue finished — and every
    earlier queue's note carries a different `at` and was never looked at. With
    sp-traffic backlogged at 10:00:01 and a clean sp-conversion drain at
    10:00:42, this reported no backlog at all.

    `realm` narrows it to one realm, which is what a per-market reader needs:
    one queue serves a whole realm, so EU's backlog says nothing about US.
    """
    behind = [f"{r}/{dataset}" for r, dataset, note in conn.execute(
        """SELECT l.realm, l.dataset, l.note
             FROM stream_drain_log AS l
             JOIN (SELECT realm, dataset, MAX(at) AS at
                     FROM stream_drain_log GROUP BY realm, dataset) AS newest
               ON l.realm = newest.realm
              AND l.dataset = newest.dataset
              AND l.at = newest.at
            WHERE l.note IS NOT NULL AND l.note != ''""")
        if realm is None or r == realm]
    return sorted(set(behind)) or None


def coverage(conn):
    """Per REALM and dataset: how many messages, and which hours they cover.

    One queue serves a whole realm and the realms are independent — separate
    queues, separate regions, separate AWS endpoints. Grouping on dataset alone
    merged them, so NA's healthy message count and fresh timestamp were reported
    for EU as well. A dead EU queue then read exactly like a live one, and the
    five EU markets are the ones with the least traffic to notice it by.
    """
    out = []
    for row in conn.execute(
            """SELECT realm, dataset, COUNT(*),
                      MIN(time_window_start), MAX(time_window_start),
                      MIN(received_at), MAX(received_at)
               FROM stream_message GROUP BY realm, dataset ORDER BY realm, dataset"""):
        out.append({"realm": row[0], "dataset": row[1], "messages": row[2],
                    "first_window": row[3], "last_window": row[4],
                    "first_received": row[5], "last_received": row[6]})
    return out


# The drain runs hourly. Two missed hours is a fault, not a quiet patch: Stream
# never resends, so a stopped drain loses data the moment SQS retention expires.
DRAIN_STALE_AFTER_MINUTES = 150


def _age_minutes(stamp, now=None):
    """Whole minutes between `stamp` (local-naive ISO) and now. None if unusable."""
    if not stamp:
        return None
    try:
        then = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is not None:
        then = then.astimezone().replace(tzinfo=None)
    now = now or datetime.datetime.now()
    return max(int((now - then).total_seconds() // 60), 0)


def _integrity(conn):
    """(corrupt?, first line of the reason) for this database.

    `quick_check` rather than `integrity_check`: it caught the real corruption
    of 2026-08-22 and took a millisecond doing it, against nine for the full
    check. Both were measured on that exact file.

    A caveat worth writing down, because it cost a wrong conclusion during that
    incident: a copy of the database taken WITHOUT its `-wal` sidecar, or with
    the sidecar renamed, reads as perfectly healthy. The corruption lived in the
    WAL. So a backup of `foo.sqlite` is only checkable beside a `foo.sqlite-wal`
    — anything else is testing a different database and will cheerfully say ok.

    Returns (None, None) when the pragma itself cannot run, because "unknown"
    and "fine" are different answers and only one of them is safe to show.
    """
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        return None, str(exc)
    if not row or not row[0]:
        return None, None
    text = str(row[0])
    if text.strip().lower() == "ok":
        return False, None
    # Skip SQLite's "*** in database main ***" banner: it names the database,
    # never the fault, and it is what the reader would be shown otherwise.
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    detail = next((ln for ln in lines if not ln.startswith("***")), lines[0])
    return True, detail[:200]


def health(env=None, now=None):
    """Stream health for the System Health screen, from LOCAL state only.

    No AWS call. System Health opens seven databases already and must stay fast
    and work offline; `stream-status` is the command that talks to AWS.

    The alarm that matters is the DRAIN, not the queues. Stream delivers nothing
    in an hour where nothing happened, so a quiet dataset is normal. A drain that
    has not run is not: SQS drops messages at the end of its retention and Stream
    will not resend them. So `drain_stale` is driven by the drain log, and a
    dataset that has never delivered anything reports `waiting`, never `stale` —
    sp-conversion legitimately sat empty on its first day.
    """
    import stream_config as sc

    env = env or {}
    configured = []
    for realm in sorted(sc.REALM_REGION):
        for dataset in (sc.TRAFFIC, sc.CONVERSION):
            if sc.queue_url(env, realm, dataset):
                configured.append((realm, dataset))

    info = {"configured": bool(configured) and sc.aws_keys(env) is not None,
            "queues_configured": len(configured),
            "database": False,
            "datasets": [],
            "last_drain": None,
            "drain_age_minutes": None,
            "drain_stale": False,
            "drain_backlog": None,
            "corrupt": None,
            "corrupt_detail": None}
    if not configured:
        return info

    conn = connect(ro=True)
    if conn is None:
        # Configured but nothing has ever run. Say that, rather than reporting
        # a healthy-looking zero.
        info["datasets"] = [{"dataset": d, "realm": r, "messages": 0,
                             "state": "waiting"} for r, d in configured]
        info["drain_stale"] = True
        return info

    info["corrupt"], info["corrupt_detail"] = _integrity(conn)

    info["database"] = True
    try:
        cov = {(c["realm"], c["dataset"]): c for c in coverage(conn)}
        # Per realm, because the queues are independent. The account-wide
        # MAX(at) meant one live realm kept the other's freshness green.
        drains = dict(conn.execute(
            "SELECT realm, MAX(at) FROM stream_drain_log GROUP BY realm"))
        row = conn.execute("SELECT MAX(at) FROM stream_drain_log").fetchone()
        last_drain = row[0] if row else None
        info["last_drain"] = last_drain
        info["drain_age_minutes"] = _age_minutes(last_drain, now)
        # The ACCOUNT is stale when ANY configured realm is stale. Reporting the
        # newest drain across all of them is what hid a dead queue.
        realm_ages = {r: _age_minutes(drains.get(r), now)
                      for r in {r for r, _ in configured}}
        info["drain_by_realm"] = {
            r: {"last_drain": drains.get(r), "age_minutes": a,
                "stale": a is None or a > DRAIN_STALE_AFTER_MINUTES}
            for r, a in realm_ages.items()}
        info["drain_stale"] = any(v["stale"] for v in info["drain_by_realm"].values())
        info["drain_stale_realms"] = sorted(
            r for r, v in info["drain_by_realm"].items() if v["stale"]) or None

        info["drain_backlog"] = drain_backlog(conn)

        for realm, dataset in configured:
            c = cov.get((realm, dataset)) or {}
            messages = c.get("messages", 0)
            age = _age_minutes(c.get("last_received"), now)
            info["datasets"].append({
                "dataset": dataset,
                "realm": realm,
                "messages": messages,
                "first_window": c.get("first_window"),
                "last_window": c.get("last_window"),
                "last_received": c.get("last_received"),
                "age_minutes": age,
                # waiting  = subscribed, nothing has ever arrived (not an error)
                # flowing  = arrived recently
                # quiet    = arrived before, nothing lately (usually a quiet hour)
                "state": "waiting" if not messages
                         else ("flowing" if age is not None
                               and age <= DRAIN_STALE_AFTER_MINUTES else "quiet"),
            })
    finally:
        conn.close()
    return info


def field_census(conn, dataset, limit=500):
    """Which keys the stored payloads actually carry, and how often.

    This is the point of banking raw. When the first real messages land, this
    answers "what does sp-traffic contain" from evidence rather than from a
    documentation page that may be a version behind.
    """
    counts = {}
    seen = 0
    for (payload,) in conn.execute(
            "SELECT payload FROM stream_message WHERE dataset=? "
            "ORDER BY received_at DESC LIMIT ?", (dataset, int(limit))):
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        records = obj if isinstance(obj, list) else [obj]
        for rec in records:
            if not isinstance(rec, dict):
                continue
            seen += 1
            for k in rec:
                counts[k] = counts.get(k, 0) + 1
    return {"records_sampled": seen,
            "fields": sorted(({"field": k, "count": v} for k, v in counts.items()),
                             key=lambda d: (-d["count"], d["field"]))}
