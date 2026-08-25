#!/usr/bin/env python3
"""
Pause ORPHANED scavenger campaigns — any 'SCAVENGER - <series> N' whose series is
no longer a configured cohort (e.g. Drinkware/Hats, which Amazon rejects as
AD_INELIGIBLE). Pausing is reversible; nothing is deleted.

SAFETY: preview by default; pauses only with --apply (+ typed APPLY, or --auto); honors KILL.

Usage:
  python3 scav_cleanup.py            # preview
  python3 scav_cleanup.py --apply    # pause orphaned scavenger campaigns
"""

import sys
import db
import killswitch
import scavenger
from ads_client import AdsClient

SP_CAMP = "application/vnd.spCampaign.v3+json"


def main():
    args = sys.argv[1:]
    conn = db.connect()
    client = AdsClient()
    # Compare against the names this market actually BUILDS, not the raw cohort
    # labels. The tee series is stored as "US Tees" and built as "Tees" outside
    # US, so every legitimate EU 'SCAVENGER - Tees N' read as an orphan — and an
    # orphan is something this script PAUSES.
    valid = ({scavenger.series_name(c["series"]) for c in scavenger.COHORTS}
             | {scavenger.series_name(scavenger.NEW_SERIES)}
             | {c["series"] for c in scavenger.COHORTS} | {scavenger.NEW_SERIES})

    orphans = []
    for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns"):
        name = c.get("name") or ""
        if (scavenger.is_scavenger(name)
                and scavenger.series_of(name) not in valid
                and c.get("state") != "PAUSED"):
            orphans.append((c.get("campaignId"), name))

    print(f"Orphaned scavenger campaigns to pause: {len(orphans)}")
    for cid, name in orphans:
        print(f"  {name}  ({cid})")
    if not orphans:
        print("Nothing to clean up."); return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and input("\nType APPLY to pause these (anything else cancels): ").strip() != "APPLY":
        print("Cancelled."); return

    ocids = [cid for cid, _ in orphans]
    client.set_campaigns_state(ocids, "PAUSED")
    for cid, name in orphans:
        db.log_write(conn, "scav_pause_orphan", "campaign", cid, name, "ENABLED", "submitted")
    db.set_local_campaign_state(conn, ocids, "PAUSED")   # keep local mirror in sync
    print(f"  paused {len(orphans)} orphaned scavenger campaign(s).")


if __name__ == "__main__":
    main()
