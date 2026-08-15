#!/usr/bin/env python3
"""Pipeline-health surfacing: data_stale alerts and per-table health freshness.

The perf tables are filled by independent report jobs that fail independently.
These tests pin the alert threshold (fires at 4+ days, exactly when
db.snapshot_gate freezes writes — 3 days behind is a NORMAL EU morning before
the 10:00 pull) and that health reports the WORST table, not just
campaign_perf — campaign_perf alone stayed green through both freezes this
engine has had.

Run from the Ads folder:  python3 -m unittest tests.pipeline_health_tests -v"""

import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db        # noqa: E402
import appctl    # noqa: E402

TODAY = datetime.date(2026, 8, 4)


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    real = db.DB_PATH
    db.DB_PATH = path
    conn = db.connect()
    db.DB_PATH = real
    return conn, path


def seed_perf(conn, campaign="2026-08-03", targeting="2026-08-03",
              searchterm="2026-08-03"):
    if campaign:
        conn.execute("""INSERT INTO campaign_perf(date,campaign_id,cost,sales)
                        VALUES (?,?,?,?)""", (campaign, "c1", 1.0, 5.0))
    if targeting:
        conn.execute("""INSERT INTO targeting_perf(date,campaign_id,ad_group_id,
                        targeting,target_id,cost,sales)
                        VALUES (?,?,?,?,?,?,?)""",
                     (targeting, "c1", "g1", "auto", "t1", 1.0, 5.0))
    if searchterm:
        conn.execute("""INSERT INTO search_term_perf(date,campaign_id,ad_group_id,
                        search_term,cost,sales)
                        VALUES (?,?,?,?,?,?)""",
                     (searchterm, "c1", "g1", "tee", 1.0, 5.0))
    conn.commit()


class StalenessAlerts(unittest.TestCase):
    def test_fresh_tables_stay_silent(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)   # 1 day old = Amazon's normal lag
            self.assertEqual(appctl._staleness_alerts(conn, "US", today=TODAY), [])
        finally:
            conn.close(); os.unlink(path)

    def test_three_days_old_is_a_normal_eu_morning(self):
        conn, path = temp_conn()
        try:
            # structural 2-day Amazon lag + pre-pull morning = 3 days behind.
            # A 3-day alarm false-fired every morning once before; never again.
            seed_perf(conn, campaign="2026-08-01", targeting="2026-08-01",
                      searchterm="2026-08-01")
            self.assertEqual(appctl._staleness_alerts(conn, "US", today=TODAY), [])
        finally:
            conn.close(); os.unlink(path)

    def test_four_days_old_alerts_per_table_with_date_in_key(self):
        conn, path = temp_conn()
        try:
            # only targeting_perf is stuck — the exact drift health used to miss
            seed_perf(conn, targeting="2026-07-31")
            alerts = appctl._staleness_alerts(conn, "US", today=TODAY)
            self.assertEqual(len(alerts), 1)
            a = alerts[0]
            self.assertEqual(a["kind"], "data_stale")
            self.assertEqual(a["key"], "stale:US:targeting_perf:2026-07-31")
            self.assertIn("targeting_perf", a["message"])
            self.assertIn("4d old", a["message"])
        finally:
            conn.close(); os.unlink(path)

    def test_empty_table_is_not_an_incident(self):
        conn, path = temp_conn()
        try:
            # never-pulled market (e.g. unconfigured KDP): no data, no alarm
            self.assertEqual(appctl._staleness_alerts(conn, "USKDP", today=TODAY), [])
        finally:
            conn.close(); os.unlink(path)


class TableFreshness(unittest.TestCase):
    def test_latest_data_is_the_worst_table(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn, campaign="2026-08-03", targeting="2026-07-31",
                      searchterm="2026-08-03")
            tables, stale, latest = appctl._table_freshness(conn, today=TODAY)
            self.assertEqual(latest, "2026-07-31")          # not campaign_perf's date
            self.assertEqual(stale, ["targeting_perf"])
            self.assertEqual(tables["campaign_perf"], "2026-08-03")
        finally:
            conn.close(); os.unlink(path)

    def test_all_fresh_reports_no_stale_tables(self):
        conn, path = temp_conn()
        try:
            seed_perf(conn)
            tables, stale, latest = appctl._table_freshness(conn, today=TODAY)
            self.assertEqual(stale, [])
            self.assertEqual(latest, "2026-08-03")
        finally:
            conn.close(); os.unlink(path)

    def test_empty_db_reports_none_not_crash(self):
        conn, path = temp_conn()
        try:
            tables, stale, latest = appctl._table_freshness(conn, today=TODAY)
            self.assertIsNone(latest)
            self.assertEqual(stale, [])
        finally:
            conn.close(); os.unlink(path)


if __name__ == "__main__":
    unittest.main()
