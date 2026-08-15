#!/usr/bin/env python3
"""Banking the Merch sales report instead of reading one file at a time.

The report is the ONLY source of organic royalty. Each download covers one
window, and the engine used to read whichever file was newest — so importing a
fresh report silently hid every earlier period. On the real account that meant
dropping from 5,217 rows to 1,328, taking ~$9k of April–May US royalty out of
halo without a word.

These tests pin the properties that keep that from coming back: imports
accumulate, re-imports are idempotent, colour/size variants are folded, and
gaps in coverage are reported rather than hidden.

Run from the Ads folder:  python3 -m unittest tests.sales_import_tests -v
"""

import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
import sales_import  # noqa: E402

HEADER = ('"Mkt","Date","ASIN","Title","Category 1","Category 2","Category 3",'
          '"Product Type","Purchased","Cancelled","Returned","Revenue","Royalties","Currency"')


def csv_file(rows, header=HEADER):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for r in rows:
            fh.write(r + "\n")
    return path


def line(mkt=".com", date="7/13/26", asin="B0AAA", ptype="Standard t-shirt",
         purchased=1, cancelled=0, returned=0, revenue=19.49, royalty=3.69,
         title="Tee", cur="USD", cat="Youth"):
    return (f'"{mkt}","{date}","{asin}","{title}","{cat}","8","Orange","{ptype}",'
            f'{purchased},{cancelled},{returned},{revenue},{royalty},"{cur}"')


class SharedStore(unittest.TestCase):
    """The report is account-wide, so it must not shard across market DBs."""

    def test_connect_shared_ignores_ads_market(self):
        real = db.DB_PATH
        try:
            os.environ["ADS_MARKET"] = "DE"
            conn = db.connect_shared(ro=True) if os.path.exists(
                os.path.join(HERE, "ads_data.sqlite")) else None
            if conn is None:
                self.skipTest("no default DB present in this checkout")
            conn.close()
        finally:
            os.environ["ADS_MARKET"] = "US"
            db.DB_PATH = real


class Parse(unittest.TestCase):

    def test_folds_colour_and_size_variants_of_the_same_asin_day(self):
        path = csv_file([
            line(cat="Youth", purchased=1, royalty=3.69, revenue=19.49),
            line(cat="Adult", purchased=2, royalty=7.38, revenue=38.98),
            line(cat="Kids", purchased=1, royalty=3.69, revenue=19.49),
        ])
        rows, meta = sales_import.parse(path)
        os.unlink(path)
        self.assertEqual(len(rows), 1, "three variants are one ASIN-day")
        self.assertEqual(meta["rows_in_file"], 3)
        self.assertEqual(rows[0][5], 4)                       # purchased summed
        self.assertAlmostEqual(rows[0][9], 14.76, places=2)   # royalty summed

    def test_two_digit_dates_parse_to_the_right_century(self):
        path = csv_file([line(date="7/9/26"), line(date="12/25/26", asin="B0BBB")])
        rows, meta = sales_import.parse(path)
        os.unlink(path)
        self.assertEqual(meta["period_start"], "2026-07-09")
        self.assertEqual(meta["period_end"], "2026-12-25")

    def test_period_end_uses_real_dates_not_text_order(self):
        """'7/9/26' sorts after '7/13/26' as text — that bug hid a report's true span."""
        path = csv_file([line(date="7/9/26"), line(date="7/13/26", asin="B0BBB")])
        _, meta = sales_import.parse(path)
        os.unlink(path)
        self.assertEqual(meta["period_end"], "2026-07-13")

    def test_refunds_stay_negative_so_royalty_is_net(self):
        path = csv_file([line(purchased=1, royalty=3.69),
                         line(purchased=0, returned=1, royalty=-3.69)])
        rows, _ = sales_import.parse(path)
        os.unlink(path)
        self.assertAlmostEqual(rows[0][9], 0.0, places=2)

    def test_a_non_sales_report_csv_is_rejected_loudly(self):
        path = csv_file(["a,b"], header="col1,col2")
        with self.assertRaises(sales_import.SalesReportFormatError):
            sales_import.parse(path)
        os.unlink(path)

    def test_unparseable_rows_are_skipped_not_fatal(self):
        path = csv_file([line(), '".com","not-a-date","B0CCC","t","","","","Tee",1,0,0,1,1,"USD"'])
        rows, meta = sales_import.parse(path)
        os.unlink(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(meta["skipped"], 1)


class Bank(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.conn = self._fresh()

    def _fresh(self):
        real = db.DB_PATH
        db.DB_PATH = self.path
        conn = db.connect()
        db.DB_PATH = real
        return conn

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_a_second_report_adds_history_instead_of_replacing_it(self):
        """The whole point: the newest file must not hide the older one."""
        first = csv_file([line(date="4/15/26", asin="B0AAA"),
                          line(date="5/01/26", asin="B0BBB")])
        second = csv_file([line(date="7/13/26", asin="B0CCC")])
        sales_import.bank(first, self.conn)
        meta = sales_import.bank(second, self.conn)
        os.unlink(first); os.unlink(second)
        self.assertEqual(meta["total_rows"], 3)
        cov = sales_import.coverage(self.conn)
        self.assertEqual(cov["first_day"], "2026-04-15")
        self.assertEqual(cov["last_day"], "2026-07-13")

    def test_reimporting_the_same_report_changes_nothing(self):
        path = csv_file([line(date="4/15/26"), line(date="4/16/26", asin="B0BBB")])
        sales_import.bank(path, self.conn)
        meta = sales_import.bank(path, self.conn)
        os.unlink(path)
        self.assertEqual(meta["new_rows"], 0)
        self.assertEqual(meta["total_rows"], 2)

    def test_overlapping_reports_dedupe_on_the_shared_day(self):
        a = csv_file([line(date="7/12/26", asin="B0AAA"), line(date="7/13/26", asin="B0BBB")])
        b = csv_file([line(date="7/13/26", asin="B0BBB"), line(date="7/14/26", asin="B0CCC")])
        sales_import.bank(a, self.conn)
        meta = sales_import.bank(b, self.conn)
        os.unlink(a); os.unlink(b)
        self.assertEqual(meta["new_rows"], 1, "only 7/14 is new")
        self.assertEqual(meta["total_rows"], 3)

    def test_markets_are_kept_apart(self):
        path = csv_file([line(mkt=".com"), line(mkt=".de", royalty=3.20)])
        sales_import.bank(path, self.conn)
        os.unlink(path)
        got = dict(self.conn.execute(
            "SELECT mkt, royalty FROM sales_report_rows").fetchall())
        self.assertAlmostEqual(got[".com"], 3.69, places=2)
        self.assertAlmostEqual(got[".de"], 3.20, places=2)

    def test_coverage_reports_holes_rather_than_hiding_them(self):
        path = csv_file([line(date="4/15/26"), line(date="4/16/26", asin="B0BBB"),
                         line(date="4/20/26", asin="B0CCC")])
        sales_import.bank(path, self.conn)
        os.unlink(path)
        cov = sales_import.coverage(self.conn)
        self.assertEqual(cov["days"], 3)
        self.assertEqual(cov["gaps"], [{"start": "2026-04-17", "end": "2026-04-19"}])

    def test_every_import_is_logged_for_provenance(self):
        path = csv_file([line(date="4/15/26")])
        sales_import.bank(path, self.conn)
        rows = db.imported_file_log(self.conn, kind=sales_import.KIND)
        os.unlink(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "2026-04-15")      # period_start

    def test_banked_rows_match_the_shape_traz_expects(self):
        path = csv_file([line(date="4/15/26")])
        sales_import.bank(path, self.conn)
        os.unlink(path)
        rows = sales_import.banked_rows(self.conn)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ("mkt", "date", "asin", "title", "ptype", "purchased",
                    "returned", "royalty", "revenue"):
            self.assertIn(key, row)
        self.assertIsInstance(row["date"], datetime.date)

    def test_empty_store_reports_no_coverage_rather_than_failing(self):
        cov = sales_import.coverage(self.conn)
        self.assertEqual(cov["days"], 0)
        self.assertIsNone(cov["first_day"])


if __name__ == "__main__":
    unittest.main()
