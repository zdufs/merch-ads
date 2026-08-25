"""Compare what Stream delivered against what Amazon's own report banked.

Why this exists
---------------
Every other check on the Stream pipeline proves it reads faithfully what
ARRIVED. None of them can prove Amazon SENT everything, and that is the failure
that matters: the totals stay internally consistent, the queues stay empty, the
drain log stays green, and the number is simply low.

The only honest test is a settled day measured twice — once by Stream, once by
the nightly report — and a comparison of the two. `campaign_daily` is the right
counterpart: it holds TRUE per-day rows grouped by campaign, banked from
Amazon's own daily report, so a per-campaign difference points straight at
whatever is missing instead of at a single unexplained total.

What this refuses to do
-----------------------
- **It will not compare a day Stream could not have seen whole.** A day with
  missing or partial hours is expected to read low, and calling that a
  discrepancy would train the reader to ignore the check. Those days return
  `comparable: false` with the reason.
- **It will not compare a day the report has not banked.** Amazon attributes
  for days after the fact, so `daily_metrics.py` banks a day the following
  night, and the Monday true-up restates ~90 days. A day the report does not
  hold yet is not a Stream failure.
- **It reports sales separately and never gates on them.** Conversions are
  dated to the CLICK hour and Amazon restates them for days, so the two sides
  settle at different speeds. Spend, impressions and clicks are final about an
  hour after the fact and are what a verdict is allowed to rest on.
"""

import sqlite3

import db
import markets
import stream_map
import stream_store

# How far the two sides may differ before it is called a problem, as a fraction
# of the report's figure. Stream and the report are built by different Amazon
# pipelines, so a small difference is expected; an eighth is not.
TOLERANCE = 0.02
# Below this, a percentage is noise. A campaign that spent two cents can differ
# by 50% and mean nothing.
MIN_COST_FOR_PERCENT = 0.50


def _report_day(conn, day):
    """Per-campaign report rows for one day, plus the totals."""
    rows = {}
    for cid, name, cost, impressions, clicks, sales, orders in conn.execute(
            """SELECT campaign_id, campaign_name, cost, impressions, clicks,
                      sales, orders
                 FROM campaign_daily WHERE date = ?""", (day,)):
        rows[str(cid)] = {"campaign_id": str(cid), "campaign": name,
                          "cost": float(cost or 0),
                          "impressions": int(impressions or 0),
                          "clicks": int(clicks or 0),
                          "sales": float(sales or 0),
                          "orders": int(orders or 0)}
    return rows


def _stream_day(conn, market, advertisers, day):
    """Per-campaign Stream rows for one day, plus the totals."""
    rows = {}
    for payload in stream_map.traffic_rows(conn, market, advertisers, day):
        cid = str(payload.get("campaign_id") or "")
        if not cid:
            continue
        r = rows.setdefault(cid, {"campaign_id": cid, "cost": 0.0,
                                  "impressions": 0, "clicks": 0})
        r["cost"] += stream_map._num(payload.get("cost"))
        r["impressions"] += int(stream_map._num(payload.get("impressions")))
        r["clicks"] += int(stream_map._num(payload.get("clicks")))
    for r in rows.values():
        r["cost"] = round(r["cost"], 4)
    return rows


def _pct(stream, report):
    """Stream as a fraction of the report, or None when the base is too small."""
    if report is None or report <= 0:
        return None
    return round(stream / report, 4)


def verify(market=None, day=None):
    market = market or markets.current()
    out = {"market": market, "day": day, "comparable": False,
           "reason": None, "stream": None, "report": None,
           "delta": None, "campaigns": [], "verdict": None}

    sconn = stream_store.connect(ro=True)
    if sconn is None:
        out["reason"] = "Marketing Stream has never run — no stream database yet."
        return out
    try:
        advertisers = stream_map.advertiser_map(sconn)
        if not any(e.get("market") == market for e in advertisers.values()):
            out["reason"] = (f"Nothing banked for {market}. Stream has delivered "
                             "no message that resolves to this market.")
            return out

        if not day:
            day = _newest_whole_day(sconn, market, advertisers)
            out["day"] = day
            if not day:
                out["reason"] = ("No day has been delivered whole yet. Stream sends "
                                 "nothing about the past, so the first complete day "
                                 "is the first full day after subscribing.")
                return out

        hours = stream_map.delivered_hours(sconn, market, advertisers, day)
        cov = stream_map._coverage(hours, day=day,
                                   offset=stream_map._offset_for(sconn, market,
                                                                 advertisers, day),
                                   since=stream_map.listening_since(sconn))
        out["coverage"] = cov
        stream_rows = _stream_day(sconn, market, advertisers, day)
    finally:
        sconn.close()

    path = stream_map.market_db_path(market)
    try:
        mconn = db.open_readonly(path)
    except Exception as e:                              # pragma: no cover
        out["reason"] = f"Could not open {path}: {e}"
        return out
    try:
        report_rows = _report_day(mconn, day)
    finally:
        mconn.close()

    def total(rows, field):
        return round(sum(r.get(field, 0) for r in rows.values()), 2)

    out["stream"] = {"cost": total(stream_rows, "cost"),
                     "impressions": int(total(stream_rows, "impressions")),
                     "clicks": int(total(stream_rows, "clicks")),
                     "campaigns": len(stream_rows)}
    out["report"] = {"cost": total(report_rows, "cost"),
                     "impressions": int(total(report_rows, "impressions")),
                     "clicks": int(total(report_rows, "clicks")),
                     "sales": total(report_rows, "sales"),
                     "orders": int(total(report_rows, "orders")),
                     "campaigns": len(report_rows)}

    # Refusals, in the order that makes the reason most useful.
    if not report_rows:
        out["reason"] = (f"The nightly report has not banked {day} yet. Amazon "
                         "attributes a day after the fact, so it lands the "
                         "following night. Nothing to compare against.")
        return out
    if cov.get("missing_hours") or cov.get("partial_hours"):
        out["reason"] = ("This day is not whole in Stream — "
                         f"{len(cov.get('missing_hours') or [])} hours never arrived "
                         f"and {len(cov.get('partial_hours') or [])} began before "
                         "Stream was switched on. It is EXPECTED to read low, so "
                         "comparing it would prove nothing.")
        return out
    if len(hours) < 24:
        out["reason"] = (f"Only {len(hours)} of 24 hours are present for {day}. A "
                         "part-day cannot be compared with a whole-day report.")
        return out

    out["comparable"] = True
    out["delta"] = {
        field: {"stream": out["stream"][field], "report": out["report"][field],
                "diff": round(out["stream"][field] - out["report"][field], 2),
                "ratio": _pct(out["stream"][field], out["report"][field])}
        for field in ("cost", "impressions", "clicks")}

    for cid, rep in sorted(report_rows.items(),
                           key=lambda kv: -kv[1]["cost"]):
        st = stream_rows.get(cid)
        row = {"campaign_id": cid, "campaign": rep["campaign"],
               "report_cost": round(rep["cost"], 2),
               "stream_cost": round(st["cost"], 2) if st else 0.0,
               "in_stream": st is not None}
        row["diff"] = round(row["stream_cost"] - row["report_cost"], 2)
        row["ratio"] = (_pct(row["stream_cost"], row["report_cost"])
                        if rep["cost"] >= MIN_COST_FOR_PERCENT else None)
        out["campaigns"].append(row)

    only_stream = [c for c in stream_rows if c not in report_rows]
    out["only_in_stream"] = only_stream

    ratio = out["delta"]["cost"]["ratio"]
    if ratio is None:
        out["verdict"] = "The report banked no spend for this day, so there is nothing to check."
    elif abs(1 - ratio) <= TOLERANCE:
        out["verdict"] = (f"MATCH. Stream saw {ratio:.1%} of the spend the report "
                          "banked — inside the 2% the two pipelines are allowed "
                          "to differ by.")
    else:
        missing = [c for c in out["campaigns"] if not c["in_stream"]]
        detail = (f" {len(missing)} campaigns in the report never appeared in "
                  "Stream." if missing else "")
        out["verdict"] = (f"MISMATCH. Stream saw {ratio:.1%} of the spend the "
                          f"report banked.{detail} Something is being dropped "
                          "between Amazon and this database.")
    return out


def _newest_whole_day(conn, market, advertisers, look_back=14):
    """The newest day Stream holds with all 24 hours and none of them partial."""
    days = [r[0] for r in conn.execute(
        """SELECT DISTINCT substr(time_window_start, 1, 10) AS d
             FROM stream_message WHERE dataset = ?
            ORDER BY d DESC LIMIT ?""", (stream_map.TRAFFIC, look_back))]
    since = stream_map.listening_since(conn)
    for day in days:
        # Counted in SQL, not by decoding every payload — this runs nightly
        # across a fortnight and the store holds tens of thousands of messages.
        hours = stream_map.delivered_hours(conn, market, advertisers, day)
        if len(hours) < 24:
            continue
        offset = stream_map._offset_for(conn, market, advertisers, day)
        cov = stream_map._coverage(hours, day=day, offset=offset, since=since)
        if cov["complete"]:
            return day
    return None
