#!/usr/bin/env python3
"""daily_metrics banks campaign_daily every night.

The nightly daily report is already grouped by campaign, so the per-campaign
rows are banked into campaign_daily for free. Before this (added 2026-08-09) the
table refreshed only on Mondays via backfill_daily, and campaign rolling-window
rules (`FOR EACH campaign IN LAST n DAYS`) went silent for most of the week.

This guards the row-shaping helper: it must produce exactly what
db.store_campaign_daily expects — (date, campaign_id, campaign_name, cost, sales,
orders, impressions, clicks, units) — sum rows that share a campaign_id, and skip
rows with no id.

Run from the Ads folder:  python3 -m unittest tests.daily_metrics_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import daily_metrics as dm  # noqa: E402


class CampaignRows(unittest.TestCase):

    def test_shape_matches_store_campaign_daily(self):
        rows = dm._campaign_rows(
            [{"campaignId": "111", "campaignName": "Alpha", "cost": 3.5,
              "sales30d": 10.0, "purchases30d": 2, "impressions": 900,
              "clicks": 40, "unitsSoldClicks30d": 2}],
            "2026-08-08")
        # (date, campaign_id, campaign_name, cost, sales, orders, impr, clicks, units)
        self.assertEqual(
            rows, [("2026-08-08", "111", "Alpha", 3.5, 10.0, 2, 900, 40, 2)])

    def test_rows_sharing_a_campaign_id_are_summed(self):
        rows = dm._campaign_rows(
            [{"campaignId": "111", "campaignName": "Alpha", "cost": 3.5,
              "sales30d": 10.0, "purchases30d": 2, "impressions": 900,
              "clicks": 40, "unitsSoldClicks30d": 2},
             {"campaignId": "111", "campaignName": "Alpha", "cost": 0.5,
              "sales30d": 4.0, "purchases30d": 1, "impressions": 50,
              "clicks": 3, "unitsSoldClicks30d": 1}],
            "2026-08-08")
        self.assertEqual(
            rows, [("2026-08-08", "111", "Alpha", 4.0, 14.0, 3, 950, 43, 3)])

    def test_rows_without_a_campaign_id_are_skipped(self):
        rows = dm._campaign_rows(
            [{"campaignId": None, "cost": 9.9},
             {"cost": 1.0}],
            "2026-08-08")
        self.assertEqual(rows, [])

    def test_missing_metric_fields_read_as_zero(self):
        rows = dm._campaign_rows(
            [{"campaignId": "222", "campaignName": "Beta"}], "2026-08-08")
        self.assertEqual(
            rows, [("2026-08-08", "222", "Beta", 0.0, 0.0, 0, 0, 0, 0)])


if __name__ == "__main__":
    unittest.main()
