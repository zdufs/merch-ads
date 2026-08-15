#!/usr/bin/env python3
"""
Set the daily budget on all LOTTO campaigns in the active market to a value
(default = lottery.DEFAULT_BUDGET). Fast — no export scan. Only touches our
"LOTTO - " campaigns (your US MerchFlow "Lotto N" campaigns are untouched).

SAFETY: preview by default; writes only with --apply + typed APPLY (or --auto); KILL aware.

Usage:
  ADS_MARKET=DE python3 set_lottery_budget.py            # preview (uses lottery.DEFAULT_BUDGET)
  ADS_MARKET=DE python3 set_lottery_budget.py --apply
  ADS_MARKET=DE python3 set_lottery_budget.py 5 --apply  # explicit budget
"""

import sys
import db
import killswitch
import lottery
from ads_client import AdsClient

SP_CAMP = "application/vnd.spCampaign.v3+json"


def main():
    args = sys.argv[1:]
    budget = lottery.DEFAULT_BUDGET
    for a in args:
        try:
            budget = float(a); break
        except ValueError:
            continue

    client = AdsClient()
    conn = db.connect()
    lottos = [c for c in client.list_all("/sp/campaigns/list", SP_CAMP, "campaigns")
              if lottery.is_lottery(c.get("name"))]
    fixes = []
    for c in lottos:
        cur = (c.get("budget") or {}).get("budget")
        if cur is None or abs(cur - budget) >= 0.01:
            fixes.append({"campaignId": c["campaignId"], "budget": budget})

    print(f"[{client.market}] LOTTO campaigns: {len(lottos)} | to set to ${budget:.2f}/day: {len(fixes)}")
    if not fixes:
        print("  all already at target budget."); return
    if "--apply" not in args:
        print("\nPREVIEW ONLY. Re-run with --apply."); return
    killswitch.check()
    if "--auto" not in args and input("\nType APPLY to update budgets: ").strip() != "APPLY":
        print("Cancelled."); return

    res = client.update_campaign_budgets(fixes)
    ok = sum(b["count"] for b in res if b["http"] in (200, 207))
    for f in fixes:
        db.log_write(conn, "lotto_budget", "campaign", f["campaignId"], f"-> ${budget}", "", "submitted")
    print(f"  updated ~{ok} LOTTO campaigns to ${budget:.2f}/day.")


if __name__ == "__main__":
    main()
