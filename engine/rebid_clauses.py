#!/usr/bin/env python3
"""
CLAUSE RE-BID — repair the close-match / loose-match bid swap (2026-08-20).

Until today `lottery.EXPRESSION_TYPE` had Amazon's two query clauses the wrong
way round: the name "close-match" was wired to QUERY_BROAD_REL_MATCHES, which
Amazon calls LOOSE match, and "loose-match" to QUERY_HIGH_REL_MATCHES, which
Amazon calls CLOSE match. So every lottery ad group launched paying the HIGH
bid for the widest, least intentful clause and the LOW bid for the tightest one.

lottery.py is fixed, so new ad groups launch correctly. This script repairs the
ones already live.

WHAT IT TOUCHES — deliberately narrow:
  Only an ENABLED close/loose clause whose bid is STILL EXACTLY the old, wrong
  launch value for its market. That bid has never been tuned, so nothing is
  lost by correcting it. A clause the optimizer has already moved is LEFT ALONE:
  its bid came from that clause's own sales data, which is better evidence than
  any launch default, and the label swap never reached the optimizer (phase 3
  and the rules read Amazon's own report labels, not our map).
  Substitutes and complements are unaffected — their mapping was always right.

It is also skipped if the target has a bid_change logged since the mirror was
last refreshed, so a re-bid can never undo a tuning move made in between.

Re-runnable: applied bids are written through to the targets mirror, so a
second run finds only what the first one missed.

SAFETY: preview by default. Writes only with --apply. KILL file honoured, every
change logged to writes_log as `clause_rebid` with its old bid for undo.

Usage:
  ADS_MARKET=DE python3 engine/rebid_clauses.py                # preview
  ADS_MARKET=DE python3 engine/rebid_clauses.py --apply --auto # write
  ADS_MARKET=DE python3 engine/rebid_clauses.py --limit 500    # a cautious slice
"""

import argparse
import sys

import ads_client
import db
import killswitch
import lottery
import markets
from ads_client import AdsClient

CLOSE = "QUERY_HIGH_REL_MATCHES"   # Amazon: close match
LOOSE = "QUERY_BROAD_REL_MATCHES"  # Amazon: loose match
TOLERANCE = 0.005                  # bids are 2dp; anything closer is the same bid


def wrong_launch_bids(clause_bids=None):
    """(expression, the bid it was WRONGLY launched at, the bid it should carry).

    The old map sent the "close-match" money to the BROAD expression, so the
    swap is simply: whatever each name was paying, the other name should pay.
    """
    cb = clause_bids or lottery.clause_bids()
    return [
        (CLOSE, cb["loose-match"], cb["close-match"]),
        (LOOSE, cb["close-match"], cb["loose-match"]),
    ]


def recently_tuned(conn, since):
    """target_ids with a bid write logged at/after `since` — never overwrite one."""
    if not since:
        return set()
    return {str(r[0]) for r in conn.execute(
        """SELECT DISTINCT entity_id FROM writes_log
           WHERE entity_type='target' AND action IN ('bid_change','clause_rebid')
             AND applied_at >= ?""", (since,))}


def mirror_as_of(conn):
    row = conn.execute("SELECT MAX(updated_at) FROM targets").fetchone()
    return row[0] if row else None


def build_proposals(conn, limit=0, clause_bids=None):
    """Untuned clauses still carrying the swapped launch bid."""
    skip = recently_tuned(conn, mirror_as_of(conn))
    out = []
    for expr, wrong, right in wrong_launch_bids(clause_bids):
        if abs(wrong - right) < TOLERANCE:
            continue        # market's two bids are equal — nothing to swap
        for tid, bid, agid, cid in conn.execute(
                """SELECT target_id,bid,ad_group_id,campaign_id FROM targets
                   WHERE kind='target' AND text=? AND state='ENABLED'
                     AND bid IS NOT NULL AND ABS(bid-?)<?""",
                (expr, wrong, TOLERANCE)):
            if str(tid) in skip:
                continue
            out.append(dict(targetId=str(tid), adGroupId=agid, campaignId=cid,
                            expr=expr, name=lottery.EXPRESSION_NAME.get(expr, expr),
                            old=round(float(bid), 2), new=right))
            if limit and len(out) >= limit:
                return out
    return out


def preview(proposals, market):
    ups = [p for p in proposals if p["new"] > p["old"]]
    downs = [p for p in proposals if p["new"] < p["old"]]
    print(f"\n{market}: {len(proposals):,} clauses still carry the swapped launch bid.")
    for name in ("close-match", "loose-match"):
        rows = [p for p in proposals if p["name"] == name]
        if rows:
            p = rows[0]
            print(f"  {name:12} {len(rows):>8,} clauses   ${p['old']:.2f} -> ${p['new']:.2f}")
    print(f"  {len(ups):,} up, {len(downs):,} down "
          f"(~{len(proposals) // 100 + 1:,} API batches)")


def apply(client, conn, proposals, market):
    total = len(proposals)
    print(f"Writing {total:,} bids in batches of 100…")
    done = failed = 0
    for i in range(0, total, 2000):          # commit progress every 2k so a stop is resumable
        window = proposals[i:i + 2000]
        res = client.update_target_bids(
            [{"targetId": p["targetId"], "bid": p["new"]} for p in window])
        accepted = ads_client.certain_ids(res, [p["targetId"] for p in window])
        rejected = {str(p["targetId"]) for p in window
                    if str(p["targetId"]) not in accepted}
        # The client clamps each bid to the market ceiling, so `p["new"]` is what
        # we ASKED for and not necessarily what Amazon received. Logging and
        # mirroring the request made the audit trail and the local bid column
        # both disagree with the live account — and silently, because a clamp is
        # a success. Read the clamps back and record what was actually written.
        caps = {c["id"]: c["cap"]
                for c in (getattr(client, "last_clamps", None) or [])}
        for p in window:
            tid = str(p["targetId"])
            bad = tid in rejected
            written = caps.get(tid, p["new"])
            db.log_write(conn, "clause_rebid", "target", p["targetId"],
                         f"{p['name']} swap-fix {p['old']}->{written}"
                         + (" [adjusted]" if tid in caps else ""),
                         str(p["old"]), "failed" if bad else "submitted")
        db.set_local_target_bids(
            conn, [(p["targetId"], caps.get(str(p["targetId"]), p["new"]))
                   for p in window if str(p["targetId"]) not in rejected])
        done += len(window) - len(rejected)
        failed += len(rejected)
        print(f"  {min(i + 2000, total):,}/{total:,} — {done:,} accepted, {failed:,} rejected")
    print(f"{market}: done. {done:,} bids corrected"
          + (f", {failed:,} rejected" if failed else "") + ". See writes_log.")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Repair the close/loose match bid swap.")
    p.add_argument("--apply", action="store_true", help="write to Amazon (default: preview)")
    p.add_argument("--auto", action="store_true", help="skip the typed confirmation")
    p.add_argument("--limit", type=int, default=0, help="cap the number of clauses")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    market = markets.current()
    conn = db.connect()
    proposals = build_proposals(conn, limit=args.limit)
    if not proposals:
        print(f"{market}: no clause still carries the swapped launch bid. Nothing to do.")
        return 0
    preview(proposals, market)
    if not args.apply:
        print("\nPREVIEW ONLY. Re-run with --apply to execute.")
        return 0
    killswitch.check()
    if not args.auto:
        if input("\nType APPLY to write these bids (anything else cancels): ").strip() != "APPLY":
            print("Cancelled — nothing written.")
            return 0
    apply(AdsClient(), conn, proposals, market)
    return 0


if __name__ == "__main__":
    sys.exit(main())
