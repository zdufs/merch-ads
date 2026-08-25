#!/usr/bin/env python3
"""Turn banked Marketing Stream messages into a per-market picture of TODAY.

`stream_drain.py` banks messages whole, on purpose: one SQS queue serves a whole
realm, so all five EU markets arrive mixed in one queue and the payload has no
country in it. This module is the other half — interpretation — kept separate so
that a mistake here can be fixed by re-reading the same banked rows rather than
by asking Amazon for an hour it will never resend.

WHY THIS EXISTS
The report pipeline is a day behind by design: Amazon builds yesterday's report
overnight, and the freshest day or two is under-attributed anyway. So the app's
newest number has always been yesterday. Stream is about an hour behind the hour
it describes, which makes "what has today cost me so far" answerable for the
first time.

WHAT IT CAN AND CANNOT SAY
`sp-traffic` carries impressions, clicks and cost. It carries NO sales and NO
orders — those live in `sp-conversion`, a separate dataset with its own
subscription. So a today panel built on traffic alone can report spend and
clicks honestly and must refuse to report ACOS, sales or conversion rate. It
does: `conversions.available` is false until conversion messages actually
arrive, and no zero is ever substituted for a number we do not have.

HOW A MESSAGE FINDS ITS MARKET
Not by marketplace id. Merch US and KDP US both advertise on ATVPDKIKX0DER —
verified against the profiles endpoint, which reports
`marketplaceStringId=ATVPDKIKX0DER` for "Sponsored ads - KDP" while live Merch
US traffic carries the same marketplace id under a different entity. So the
marketplace would merge two separate advertisers into one number.

Instead the payload's `advertiser_id` (the Amazon entity id) is resolved to a
market ONCE, by looking its campaign ids up in each market database and taking
the market that holds them. That mapping is then cached, which matters for a
second reason: a campaign created this morning is not in the nightly-pulled
`campaigns` table yet, so campaign-by-campaign matching would silently drop its
spend. Matching on the advertiser catches it. Anything still unresolved is
REPORTED, never dropped quietly.

THE DAY BOUNDARY IS AMAZON'S, NOT THE MAC'S
`time_window_start` arrives as marketplace-local time with its offset attached
("2026-08-21T07:00:00-07:00"). The date in that string is the advertising day
Amazon will bill and report against. So the day is read straight out of the
string, with no timezone conversion of our own to get wrong.
"""

import collections
import datetime
import json
import os
import sqlite3

import paths
import db
import markets
import stream_config
import stream_store

TRAFFIC = "sp-traffic"
CONVERSION = "sp-conversion"

# An advertiser is claimed by the market holding the most of its campaigns. One
# match is enough to be sure in practice (campaign ids are globally unique), but
# a tie means two databases claim the same advertiser, and that is a bug worth
# refusing rather than guessing at.
MIN_CAMPAIGN_MATCHES = 1


def market_db_path(market):
    return paths.repo("ads_data.sqlite" if market == "US" else f"ads_data_{market}.sqlite")


# ---------------------------------------------------------------- advertisers

def _campaign_ids(conn, dataset=TRAFFIC, limit=400):
    """A sample of campaign ids PER ADVERTISER, for resolution.

    The limit used to apply to the whole query, so it was a sample of the newest
    400 messages in the queue and not a sample per advertiser at all. One queue
    serves a whole realm, and the advertisers on it are wildly different sizes:
    Merch US fills that window on its own, so a quiet advertiser — USKDP, or an
    EU market on a slow day — never appeared, was never resolved, and its Stream
    panel reported unsupported with no advertiser listed as unresolved either.
    It looked like a market that had never been subscribed.

    A window function does the per-advertiser part in SQLite. The ORDER BY is
    still newest-first, because a campaign id from last week may belong to a
    campaign since archived.
    """
    out = collections.defaultdict(set)
    for adv, camp in conn.execute(
            """SELECT advertiser_id, campaign_id FROM (
                   SELECT json_extract(payload,'$.advertiser_id') AS advertiser_id,
                          json_extract(payload,'$.campaign_id')   AS campaign_id,
                          ROW_NUMBER() OVER (
                              PARTITION BY json_extract(payload,'$.advertiser_id')
                              ORDER BY received_at DESC) AS rn
                     FROM stream_message WHERE dataset = ?)
                WHERE rn <= ?""", (dataset, int(limit))):
        if adv and camp:
            out[adv].add(str(camp))
    return out


def _market_holding(campaign_ids):
    """{market: how many of these campaign ids that market's database holds}."""
    ids = list(campaign_ids)
    if not ids:
        return {}
    counts = {}
    for market in markets.MARKETS:
        path = market_db_path(market)
        if not os.path.exists(path):
            continue
        try:
            c = sqlite3.connect(db.file_uri(path, "ro"), uri=True)
        except sqlite3.Error:
            continue
        try:
            n = 0
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                q = ",".join("?" * len(chunk))
                n += c.execute(
                    f"SELECT COUNT(*) FROM campaigns WHERE campaign_id IN ({q})",
                    chunk).fetchone()[0]
            if n:
                counts[market] = n
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return counts


def learn_advertisers(conn):
    """Resolve every advertiser seen in the banked messages to a market.

    Returns {advertiser_id: {"market": str|None, "matched": int, "sampled": int,
                             "reason": str|None}}.

    A market of None is an honest "we do not know yet", and the caller reports
    it. It happens for a genuinely new advertiser, and for one whose campaigns
    have not been pulled into a market database yet.
    """
    resolved = {}
    for adv, camps in _campaign_ids(conn).items():
        counts = _market_holding(camps)
        entry = {"advertiser_id": adv, "sampled": len(camps),
                 "matched": 0, "market": None, "reason": None}
        if not counts:
            entry["reason"] = "none of its campaigns are in any market database yet"
        else:
            best = max(counts.values())
            winners = [m for m, n in counts.items() if n == best]
            if len(winners) > 1:
                entry["reason"] = ("more than one market claims these campaigns: "
                                   + ", ".join(sorted(winners)))
            elif best < MIN_CAMPAIGN_MATCHES:
                entry["reason"] = "too few campaign matches to be sure"
            else:
                entry["market"] = winners[0]
                entry["matched"] = best
        resolved[adv] = entry
    return resolved


def cached_advertisers(conn):
    """The stored advertiser->market map. {} when the table is absent."""
    try:
        rows = conn.execute(
            "SELECT advertiser_id, market, matched, sampled, learned_at "
            "FROM stream_advertiser").fetchall()
    except sqlite3.Error:
        return {}
    return {r[0]: {"advertiser_id": r[0], "market": r[1], "matched": r[2],
                   "sampled": r[3], "learned_at": r[4], "reason": None}
            for r in rows}


def store_advertisers(conn, resolved):
    """Persist the resolved map. Only rows that actually resolved are stored —
    caching "unknown" would stop us retrying once the campaigns get pulled."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [(a, e["market"], e["matched"], e["sampled"], now)
            for a, e in resolved.items() if e.get("market")]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO stream_advertiser (advertiser_id, market, matched, sampled, learned_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(advertiser_id) DO UPDATE SET "
        "market=excluded.market, matched=excluded.matched, "
        "sampled=excluded.sampled, learned_at=excluded.learned_at", rows)
    conn.commit()
    return len(rows)


def advertiser_map(conn, refresh=False):
    """{advertiser_id: entry}, from cache where possible.

    An advertiser that is in the banked messages but not in the cache is always
    resolved live, so a new profile appears without anyone running anything.
    """
    known = {} if refresh else cached_advertisers(conn)
    seen = set(_campaign_ids(conn))
    missing = seen - set(known)
    if missing or refresh:
        fresh = learn_advertisers(conn)
        known.update({a: e for a, e in fresh.items() if refresh or a in missing})
        try:
            store_advertisers(conn, fresh)
        except sqlite3.Error:
            pass  # read-only connection; resolution still works for this call
    return known


# --------------------------------------------------------------------- rows

def _day_and_hour(window):
    """('2026-08-21', 7) from '2026-08-21T07:00:00-07:00'. Amazon's own day."""
    if not window or len(window) < 13:
        return None, None
    try:
        return window[:10], int(window[11:13])
    except ValueError:
        return None, None


def _offset(window):
    """'-07:00' — the marketplace's UTC offset, straight off the message."""
    if not window:
        return None
    tail = window[-6:]
    return tail if len(tail) == 6 and tail[0] in "+-" and tail[3] == ":" else None


def account_today(conn, market, advertisers, now=None):
    """Today's date IN THE ADVERTISING ACCOUNT'S TIMEZONE.

    Derived from the offset on the newest message for this market rather than
    from a hardcoded table, so it stays right through a daylight-saving change
    without anyone maintaining it. Falls back to the Mac's date when no message
    has ever arrived — there is nothing better to say, and the reply carries the
    offset so a reader can see which it was.
    """
    ids = [a for a, e in advertisers.items() if e.get("market") == market]
    window = None
    if ids:
        q = ",".join("?" * len(ids))
        row = conn.execute(
            f"SELECT MAX(time_window_start) FROM stream_message "
            f"WHERE json_extract(payload,'$.advertiser_id') IN ({q})", ids).fetchone()
        window = row[0] if row else None
    off = _offset(window)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if off:
        sign = 1 if off[0] == "+" else -1
        delta = datetime.timedelta(hours=int(off[1:3]), minutes=int(off[4:6]))
        return (now.astimezone(datetime.timezone.utc) + sign * delta).date().isoformat(), off
    return now.date().isoformat(), None


# The rest of the engine reads Amazon's THIRTY-DAY attribution columns
# (`sales30d`, `purchases30d`, `unitsSoldClicks30d` — see phase0_pull.py and
# daily_metrics.py). Stream offers 1/7/14/30-day windows side by side, so
# picking a different one here would put two numbers in the app that disagree
# for no reason a reader could see.
ATTRIBUTION = "30d"


def _grain(payload):
    """The natural key of one Stream row: the hour, the ad, the target and the
    placement it was shown in.

    Amazon's documented dedupe key is `idempotency_id`, and that is used first.
    The grain is the fallback, and it is the safer of the two for a RESTATEMENT:
    if Amazon ever re-sends an hour under a fresh idempotency_id, keying on the
    id alone would count that hour twice. Verified against 306 real sp-traffic
    rows: the grain is unique, so this changes nothing today and protects
    against that tomorrow.
    """
    return (payload.get("time_window_start"), payload.get("ad_id"),
            payload.get("keyword_id"), payload.get("placement"),
            payload.get("match_type"))


def _rows(conn, dataset, market, advertisers, day):
    """Deduped payloads for one dataset, one market and one advertising day.

    **The two datasets are deduped differently, because they mean different
    things.** Reading real payloads is what settled it; both rules used to be
    one rule, and one of them was wrong.

    `sp-traffic` carries DELTAS. `impressions` is 1 or 2, and a correction
    arrives as -1. Many messages therefore share the same hour, ad, keyword and
    placement on purpose, and collapsing on that shape would throw most of an
    hour away. So traffic is keyed on `idempotency_id` alone, which is unique
    per delta. A message without one is KEPT and counted, never collapsed: an
    overcount announces itself the moment `stream-verify` compares a day, while
    an undercount is the failure that hides all the way to the screen.

    `sp-conversion` carries RESTATED SNAPSHOTS. One row is the attribution for
    one ad, keyword, placement and click-hour, and Amazon sends it again as the
    figure grows — a 1d/7d/14d/30d ladder on every message. Summing two of
    those would invent sales, which is the more dangerous error here: inflated
    sales flatter ACOS and could drive a bid up. So conversions are keyed on the
    row's natural grain and the newest wins, whatever id it arrives under.

    SQS redelivery is handled before any of this, by the message-id primary key.
    """
    collapse_on_grain = (dataset == CONVERSION)
    ids = [a for a, e in advertisers.items() if e.get("market") == market]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    # rowid breaks the tie: two restatements arriving in the same second would
    # otherwise leave "newest wins" up to whatever order SQLite happened to
    # return, and the answer would change between runs.
    rows = conn.execute(
        f"""SELECT payload FROM stream_message
             WHERE dataset = ?
               AND substr(time_window_start, 1, 10) = ?
               AND json_extract(payload,'$.advertiser_id') IN ({q})
             ORDER BY received_at ASC, rowid ASC""",
        [dataset, day] + ids).fetchall()

    newest = {}
    unkeyed = 0
    for i, (payload,) in enumerate(rows):
        try:
            p = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if collapse_on_grain:
            key = _grain(p)
        else:
            key = p.get("idempotency_id")
            if not key:
                unkeyed += 1
                key = ("unkeyed", i)      # unique — never collapses a real delta
        newest[key] = p                   # later row wins
    return list(newest.values()), unkeyed


def traffic_rows(conn, market, advertisers, day):
    """Impressions, clicks and cost for one advertising day."""
    return _rows(conn, TRAFFIC, market, advertisers, day)[0]


def traffic_rows_keyed(conn, market, advertisers, day):
    """`(payloads, how_many_carried_no_idempotency_id)` — see `_rows`."""
    return _rows(conn, TRAFFIC, market, advertisers, day)


def conversion_rows(conn, market, advertisers, day):
    """Sales, orders and units ATTRIBUTED TO THE AD INTERACTIONS OF THAT DAY.

    `time_window_start` on a conversion message is the hour of the click, not
    the hour of the purchase. A message that arrived this evening carrying a
    window six days old is normal and correct: somebody clicked six days ago and
    bought today. So this is keyed on the ad day, exactly like `campaign_daily`,
    and the two are directly comparable.

    The consequence for a day in progress is that this figure only ever GROWS.
    The spend for an hour is final about an hour later; its sales are not.
    """
    return _rows(conn, CONVERSION, market, advertisers, day)[0]


def conversion_totals(rows):
    """{sales, orders, units} over the 30-day attribution columns."""
    def total(field):
        return sum(_num(p.get(f"{field}_{ATTRIBUTION}")) for p in rows)
    return {"sales": round(total("sales"), 2),
            "orders": int(total("purchases")),
            "units": int(total("units_sold"))}


# ------------------------------------------------------------------ summary

def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _bucket(rows, key):
    """Sum impressions / clicks / cost grouped by one payload field."""
    out = collections.defaultdict(lambda: {"impressions": 0, "clicks": 0, "cost": 0.0})
    for p in rows:
        b = out[p.get(key) or "—"]
        b["impressions"] += int(_num(p.get("impressions")))
        b["clicks"] += int(_num(p.get("clicks")))
        b["cost"] += _num(p.get("cost"))
    return out


def delivered_hours(conn, market, advertisers, day):
    """Which hours of a day arrived, counted in SQL.

    `hours_for` decodes every payload to answer the same question, which is
    fine for one day on a screen and far too slow for a nightly scan across a
    fortnight. Coverage only needs the hour numbers, so this reads them without
    ever building a Python dict per message.
    """
    ids = [a for a, e in advertisers.items() if e.get("market") == market]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT DISTINCT substr(time_window_start, 12, 2)
              FROM stream_message
             WHERE dataset = ? AND substr(time_window_start, 1, 10) = ?
               AND json_extract(payload,'$.advertiser_id') IN ({q})""",
        [TRAFFIC, day] + ids).fetchall()
    out = []
    for (hh,) in rows:
        try:
            out.append({"hour": int(hh)})
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda h: h["hour"])


def hours_from_rows(rows):
    """Delivered hours, shaped for `_coverage()` and for the app's hour strip."""
    buckets = _bucket(rows, "time_window_start")
    out = []
    for window in sorted(buckets):
        _d, hour = _day_and_hour(window)
        b = buckets[window]
        out.append({"hour": hour, "window": window,
                    "impressions": b["impressions"], "clicks": b["clicks"],
                    "cost": round(b["cost"], 2)})
    return out


def hours_for(conn, market, advertisers, day):
    """The same, read straight out of the store for one day."""
    return hours_from_rows(traffic_rows(conn, market, advertisers, day))


def _offset_for(conn, market, advertisers, day):
    """The marketplace offset carried by that day's OWN messages.

    Read from the payload rather than from a table of timezones, so a market
    that shifts for daylight saving needs no code change and cannot be wrong.
    """
    for payload in traffic_rows(conn, market, advertisers, day):
        offset = _offset(payload.get("time_window_start") or "")
        if offset:
            return offset
    return None


def _campaign_names(market, campaign_ids):
    """{campaign_id: name} from the market database. Missing ids are simply
    absent — a campaign created this morning has no row yet, and its id is a
    better label than a blank."""
    ids = [str(c) for c in campaign_ids if c]
    if not ids:
        return {}
    path = market_db_path(market)
    if not os.path.exists(path):
        return {}
    out = {}
    try:
        c = sqlite3.connect(db.file_uri(path, "ro"), uri=True)
    except sqlite3.Error:
        return {}
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = ",".join("?" * len(chunk))
            for cid, name in c.execute(
                    f"SELECT campaign_id, name FROM campaigns WHERE campaign_id IN ({q})",
                    chunk):
                out[str(cid)] = name
    except sqlite3.Error:
        pass
    finally:
        c.close()
    return out


def listening_since(conn):
    """The moment this installation first heard anything from Stream, as an
    aware datetime — or None if nothing has ever arrived.

    `received_at` is written as naive LOCAL time, so it is made aware with the
    machine's own offset before it can be compared against an advertising hour,
    which carries the marketplace's offset.
    """
    row = conn.execute("SELECT MIN(received_at) FROM stream_message").fetchone()
    if not row or not row[0]:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None
    return stamp.astimezone() if stamp.tzinfo is None else stamp


def _partial_hours(day, offset, hours, since):
    """Which of these hours had already STARTED before we were listening.

    Pass the whole EXPECTED range, not just the hours that carry data. An hour
    that predates the subscription and delivered nothing is not a delivery
    failure — it is an hour nobody was listening to. Calling it "never arrived"
    accuses Amazon of dropping data it was never asked for, and that is the
    same class of lie as calling a fragment whole.

    Amazon sends a short catch-up when a subscription is created and promises
    nothing about how far back it reaches. So an hour that began before the
    first message ever arrived holds whatever fragment that catch-up happened to
    include — which on the first day was two hours of real data, two hours of
    nothing but corrections, and five hours of silence.

    Counting those fragments as "delivered" is what made the first day read as a
    90% collapse in spend instead of as a pipe that had just been switched on.
    An hour that is here but cannot be whole has to say so.
    """
    if since is None or not day or not offset:
        return []
    out = []
    for hour in hours:
        try:
            start = datetime.datetime.fromisoformat(
                f"{day}T{hour:02d}:00:00{offset}")
        except (TypeError, ValueError):
            continue
        if start < since:
            out.append(hour)
    return out


def _coverage(by_hour, day=None, offset=None, since=None, backlog=None):
    """Which of the day's elapsed hours are actually here, and which are whole.

    A total summed over a day with holes in it is an UNDERCOUNT, and the reader
    has no way to tell. On the day Stream was first subscribed, Amazon delivered
    hours 07 and 08 and nothing for 00-06 — a panel saying "$0.07 spent today"
    without saying that would be worse than no panel.

    An hour is counted three ways, because there are three different states and
    the first version of this collapsed them into two:

    - **missing**  — we were listening and nothing arrived. Gone for good;
      Stream does not resend.
    - **partial**  — the hour began before we were listening, so what is here is
      whatever Amazon's catch-up happened to include. Not a hole, not a total.
      An hour that predates the subscription is partial even when it carried
      NOTHING: nobody was listening, so nothing was dropped. Counting those as
      missing accuses Amazon of losing data it was never asked to send, and on
      day one it turns a switch-on into what reads like an outage.
    - delivered whole.

    The expected range stops at the newest hour delivered, never at the current
    clock hour. Stream runs about an hour behind, so treating the current hour as
    missing would raise a false alarm every single hour.

    `backlog` is the fourth state, and it is not about hours at all. Every count
    above is a claim about what was BANKED, and messages still sitting in SQS
    were never banked. They belong to hours that already look delivered, so a
    day can read complete while part of its traffic is queued at Amazon — which
    is exactly what the Dashboard showed on 2026-08-24: coverage complete, and
    958 undrained messages growing by the hour. Completeness has to be a claim
    about the pipeline, not only about the hours it managed to read.
    """
    backlog = sorted(backlog or []) or None
    hours = sorted(h["hour"] for h in by_hour if h["hour"] is not None)
    if not hours:
        return {"delivered_hours": 0, "expected_hours": 0, "missing_hours": [],
                "partial_hours": [], "backlog_pending": backlog, "complete": False,
                "note": "No hours delivered for this day yet."}
    expected = list(range(0, hours[-1] + 1))
    # Pre-subscription hours are judged over the WHOLE expected range, so an
    # hour that began before we were listening is named as such whether or not
    # Amazon's catch-up happened to carry anything for it. Only an hour we were
    # actually listening for can be MISSING.
    partial = _partial_hours(day, offset, expected, since)
    missing = [h for h in expected
               if h not in set(hours) and h not in set(partial)]
    note = None
    whole = len(expected) - len(missing) - len(partial)
    if missing and partial:
        note = (f"Only {whole} of the {len(expected)} hours up to "
                f"{hours[-1]:02d}:00 {'is' if whole == 1 else 'are'} complete. "
                f"{len(missing)} never arrived and {len(partial)} began before "
                f"Stream was switched on, so they hold only what Amazon's "
                f"catch-up included. These totals are a large UNDERCOUNT — the "
                f"nightly report is the source of truth for the day.")
    elif missing:
        note = (f"{len(missing)} of the {len(expected)} hours up to "
                f"{hours[-1]:02d}:00 were never delivered, so these totals are an "
                f"UNDERCOUNT. Stream does not resend, so those hours will stay "
                f"missing; the nightly report is the source of truth for the day.")
    elif partial:
        note = (f"{len(partial)} of the {len(expected)} hours up to "
                f"{hours[-1]:02d}:00 began before Stream was switched on, so they "
                f"hold only part of what they should. These totals are an "
                f"UNDERCOUNT; the nightly report is the source of truth for the day.")
    if backlog:
        queued = ", ".join(backlog)
        drain = (f"The hourly drain did not empty {queued}, so part of this "
                 f"day's traffic is still queued at Amazon and has not been "
                 f"counted here. These totals are an UNDERCOUNT, and it grows "
                 f"until the drain catches up.")
        note = f"{note} {drain}" if note else drain
    return {"delivered_hours": len(hours), "expected_hours": len(expected),
            "missing_hours": missing, "partial_hours": partial,
            "backlog_pending": backlog,
            "complete": not missing and not partial and not backlog, "note": note}


def summary(market=None, day=None, now=None, top=0):
    """What Stream knows about one market's day.

    Returns a dict shaped for the app. `supported:false` when Stream is not set
    up or nothing has been banked for this market — an honest "no", never an
    empty set of zeroes that reads like a day with no spend.
    """
    market = market or markets.current()
    conn = stream_store.connect(ro=True)
    if conn is None:
        return {"market": market, "supported": False,
                "note": "Marketing Stream has never run — no stream database yet."}
    try:
        advertisers = advertiser_map(conn)
        mine = {a: e for a, e in advertisers.items() if e.get("market") == market}
        unresolved = [e for e in advertisers.values() if not e.get("market")]
        if not mine:
            return {"market": market, "supported": False,
                    "unresolved_advertisers": unresolved,
                    "note": ("Nothing banked for this market yet. Stream is "
                             "subscribed per market — check stream-status.")}

        today, offset = account_today(conn, market, advertisers, now=now)
        day = day or today
        rows, unkeyed = traffic_rows_keyed(conn, market, mine, day)

        by_hour = hours_from_rows(rows)

        placements = []
        total_cost = sum(_num(p.get("cost")) for p in rows)
        total_impressions = sum(int(_num(p.get("impressions"))) for p in rows)
        # Sorted by IMPRESSIONS, not cost. Early in a day almost every placement
        # has spent nothing, so a cost sort puts the whole list in an arbitrary
        # order and the one fact worth seeing — where the ads are actually being
        # shown — is buried.
        for name, b in sorted(_bucket(rows, "placement").items(),
                              key=lambda kv: (-kv[1]["impressions"], -kv[1]["cost"])):
            placements.append({
                "placement": name,
                "impressions": b["impressions"], "clicks": b["clicks"],
                "cost": round(b["cost"], 2),
                "share": round(b["cost"] / total_cost, 4) if total_cost else 0.0,
                # The share that is always meaningful. Cost share is degenerate
                # for most of a day because most placements have spent nothing.
                "impression_share": (round(b["impressions"] / total_impressions, 4)
                                     if total_impressions else 0.0),
                "ctr": round(b["clicks"] / b["impressions"], 4) if b["impressions"] else None,
            })

        camp_buckets = _bucket(rows, "campaign_id")
        names = _campaign_names(market, camp_buckets.keys())
        # Sorted by IMPRESSIONS, for the same reason the placements above are:
        # early in a day almost nothing has spent, so a cost sort ranks a
        # campaign that has served 89 impressions above one that has served 900
        # and buries the fact that the second is running at all.
        ranked = sorted(camp_buckets.items(),
                        key=lambda kv: (-kv[1]["impressions"], -kv[1]["cost"]))
        # top=0 means every campaign. A US day is ~50 rows, so the cap that used
        # to be here bought nothing and cost 45% of the day's impressions: the
        # twelve biggest SPENDERS held 2,478 of 4,465 impressions on 2026-08-21
        # and the reply said nothing about the other 39 campaigns. A cap that a
        # caller does ask for is reported, never silent.
        shown = ranked[:top] if top else ranked
        campaigns = []
        for cid, b in shown:
            campaigns.append({"campaign_id": cid, "campaign": names.get(str(cid)) or str(cid),
                              "impressions": b["impressions"], "clicks": b["clicks"],
                              "cost": round(b["cost"], 2)})
        campaign_count = len(ranked)

        impressions = sum(int(_num(p.get("impressions"))) for p in rows)
        clicks = sum(int(_num(p.get("clicks"))) for p in rows)
        currency = (next((p.get("currency") for p in rows if p.get("currency")), None)
                    or markets.cfg(market).get("currency"))

        # THIS market's conversion messages. The count was account-wide, and one
        # queue serves a whole realm: Merch US delivering conversions made every
        # other market on that queue report available:true with sales of zero —
        # which reads as "sold nothing" rather than "cannot see sales yet". That
        # is the exact confusion the available flag exists to prevent.
        _q = ",".join("?" * len(mine))
        conv = conn.execute(
            f"SELECT COUNT(*) FROM stream_message WHERE dataset = ? "
            f"AND json_extract(payload,'$.advertiser_id') IN ({_q})",
            (CONVERSION, *mine)).fetchone()[0] if mine else 0
        conv_rows = conversion_rows(conn, market, mine, day) if conv else []
        conv_totals = conversion_totals(conv_rows)
        # AVAILABLE IS ABOUT THIS DAY, not about the dataset. It used to be
        # `conv > 0` — has this market ever received a conversion message — so a
        # day with no conversion rows still reported available:true with sales
        # 0, which is exactly the shape the standing rule forbids: a zero reads
        # as "sold nothing" rather than "cannot see sales yet". `messages` still
        # carries the account-wide count, so the two states stay distinguishable.
        day_conv = bool(conv_rows)
        # The queues this market's realm is served by. One queue serves a whole
        # realm, so EU's backlog says nothing about US and must not be reported
        # here. A drain that fell behind is the one shortfall the hour counts
        # cannot see: those messages were never banked, and the hours they
        # belong to already read as delivered.
        realm = stream_config.realm_for_endpoint(markets.cfg(market)["endpoint"])
        cov = _coverage(by_hour, day=day, offset=offset,
                        since=listening_since(conn),
                        backlog=stream_store.drain_backlog(conn, realm=realm))

        # Why there is no ACOS. Two different reasons, and the sharper one wins.
        # Spend and sales are BOTH partial on a day in progress, but they are
        # partial in opposite directions: an hour of spend that was never
        # delivered is missing for good, while sales keep arriving. Side by side
        # that reads far better than the day really is, so when the day also has
        # holes the sentence has to name them.
        short = len(cov["missing_hours"]) + len(cov["partial_hours"])
        if not day_conv:
            acos_withheld = None
        elif short:
            acos_withheld = (
                f"ACOS is not shown: {short} hours of spend are missing or "
                "incomplete and the sales are still arriving. The two numbers "
                "above are not comparable today.")
        elif cov["backlog_pending"]:
            acos_withheld = (
                "ACOS is not shown: part of today's traffic is still queued at "
                "Amazon and the sales are still arriving. The two numbers above "
                "are not comparable today.")
        else:
            acos_withheld = (
                "ACOS is not shown for a day still in progress: the spend is "
                "complete and the sales are not.")

        row = conn.execute("SELECT MAX(received_at) FROM stream_message").fetchone()
        return {
            "market": market,
            "supported": True,
            "currency": currency,
            "day": day,
            "is_today": day == today,
            "account_offset": offset,
            "as_of": row[0] if row else None,
            "hours_delivered": len(by_hour),
            "latest_hour": by_hour[-1]["window"] if by_hour else None,
            # A day with holes sums to an undercount and nothing else says so.
            "coverage": cov,
            # Messages that carried no idempotency_id. Always 0 so far. They are
            # KEPT rather than deduped, because these payloads are deltas and
            # collapsing them on shape would throw most of an hour away — so a
            # non-zero here means this day may double-count, which stream-verify
            # will show, rather than silently undercount, which nothing would.
            "unkeyed_messages": unkeyed,
            "totals": {
                "impressions": impressions,
                "clicks": clicks,
                "cost": round(total_cost, 2),
                "ctr": round(clicks / impressions, 4) if impressions else None,
                "cpc": round(total_cost / clicks, 4) if clicks else None,
            },
            "hours": by_hour,
            "placements": placements,
            "campaigns": campaigns,
            # The TRUE number of campaigns that served today, and whether the
            # list above is all of them. Conflating the two is what let a
            # truncated list read as a complete one.
            "campaign_count": campaign_count,
            "campaigns_truncated": len(campaigns) < campaign_count,
            # Sales and orders are in the OTHER dataset, with its own
            # subscription. Until ANY of it arrives there is nothing to report
            # and a zero would read as "sold nothing" rather than "cannot see".
            "conversions": {
                "available": day_conv,
                "messages": conv,
                "rows": len(conv_rows),
                "attribution": ATTRIBUTION,
                "sales": conv_totals["sales"] if day_conv else None,
                "orders": conv_totals["orders"] if day_conv else None,
                "units": conv_totals["units"] if day_conv else None,
                "note": ("Attributed to the ad clicks of this day, "
                         f"{ATTRIBUTION} window — the same one the nightly report "
                         "uses. Conversions arrive hours or days after the click "
                         "and Amazon restates them, so this figure only grows."
                         ) if day_conv else
                        ("No conversion has been attributed to this day's clicks "
                         "yet. They arrive hours or days later, so sales, ACOS "
                         "and conversion rate cannot be shown for it."
                         ) if conv else
                        ("sp-conversion has delivered nothing yet, so sales, ACOS "
                         "and conversion rate cannot be shown for today."),
                # Deliberately no ACOS on a day in progress. The spend for an
                # hour is final about an hour later; its sales are not. Dividing
                # a complete cost by an incomplete return produces a number that
                # always looks alarming and is always wrong.
                "acos_withheld": acos_withheld,
            },
            "unresolved_advertisers": unresolved,
        }
    finally:
        conn.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="What Marketing Stream knows about today.")
    ap.add_argument("--market", default=None)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD in the account's timezone")
    args = ap.parse_args()
    print(json.dumps(summary(args.market, args.day), indent=1))


if __name__ == "__main__":
    main()
