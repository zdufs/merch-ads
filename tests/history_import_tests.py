#!/usr/bin/env python3
"""Banking monthly history exported from the Ads console.

The console reaches back years where the API stops at ~95 days, so these
imports are the only copy of anything older. Two traps, both hit on the first
real export:

  * `Month` is month-OF-YEAR. A 2023-2024 report returns twelve rows per
    currency with July 2023 and July 2024 SUMMED. Banking that would record two
    years of spend as one month, permanently and invisibly — so an ambiguous
    file must be REFUSED, not guessed at.
  * `Country` came back empty, so EUR is DE+FR+ES+IT merged and must be
    labelled as such rather than attributed to one country.

Run from the Ads folder:  python3 -m unittest tests.history_import_tests -v
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import db  # noqa: E402
import history_import  # noqa: E402

HEADER = ("Month,Country,Country code,Advertiser account ID,Advertiser account name,"
          "Budget currency,Impressions,Clicks,Total cost,Sales,Purchases,Units sold")
HEADER_Y = "Year," + HEADER


def csv_file(rows, header=HEADER, name=None):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, name or "History_backfill_2025.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for r in rows:
            fh.write(r + "\n")
    return path


def line(month=7, cur="USD", cost=100.0, sales=800.0, purchases=40, units=42,
         impressions=1000, clicks=50):
    return (f'{month},,,"=""amzn1.ads-account.x""",Example Trading Ltd,{cur},'
            f'{impressions},{clicks},{cost},{sales},{purchases},{units}')


class YearSafety(unittest.TestCase):
    """The whole point: never bank a month whose year is a guess."""

    def test_multi_year_filename_is_refused(self):
        path = csv_file([line(month=7)], name="History_backfill_2023-2024.csv")
        with self.assertRaises(history_import.HistoryFormatError) as ctx:
            history_import.parse(path)
        self.assertIn("month-of-year", str(ctx.exception))

    def test_single_year_filename_is_accepted(self):
        path = csv_file([line(month=7)], name="History_backfill_2025.csv")
        rows, meta = history_import.parse(path)
        self.assertEqual(rows[0][0], "2025-07")
        self.assertEqual(meta["year_source"], "filename/argument")

    def test_explicit_year_overrides_an_ambiguous_name(self):
        path = csv_file([line(month=7)], name="History_backfill_2023-2024.csv")
        rows, _ = history_import.parse(path, year=2024)
        self.assertEqual(rows[0][0], "2024-07")

    def test_a_year_column_wins_and_dates_every_row(self):
        path = csv_file(["2023," + line(month=7), "2024," + line(month=7)],
                        header=HEADER_Y, name="History_backfill_2023-2024.csv")
        rows, meta = history_import.parse(path)
        self.assertEqual(meta["year_source"], "column")
        self.assertEqual(sorted(r[0] for r in rows), ["2023-07", "2024-07"])

    def test_a_year_column_keeps_the_two_julys_apart(self):
        """Without Year these two rows collapse into one — that is the data loss."""
        path = csv_file(["2023," + line(month=7, cost=100),
                         "2024," + line(month=7, cost=250)],
                        header=HEADER_Y, name="History_backfill_2023-2024.csv")
        rows, _ = history_import.parse(path)
        by_month = {r[0]: r[5] for r in rows}
        self.assertEqual(by_month["2023-07"], 100.0)
        self.assertEqual(by_month["2024-07"], 250.0)

    def test_a_non_history_csv_is_rejected(self):
        path = csv_file(["1,2"], header="foo,bar")
        with self.assertRaises(history_import.HistoryFormatError):
            history_import.parse(path)


class Mapping(unittest.TestCase):

    def test_currency_maps_to_market_and_eur_is_flagged_as_merged(self):
        path = csv_file([line(cur="USD"), line(cur="GBP"), line(cur="EUR")])
        rows, _ = history_import.parse(path)
        got = {r[1]: r[2] for r in rows}
        self.assertEqual(got["USD"], "US")
        self.assertEqual(got["GBP"], "UK")
        self.assertEqual(got["EUR"], "EU", "EUR covers four markets, not one")

    def test_out_of_range_months_are_skipped(self):
        path = csv_file([line(month=0), line(month=13), line(month=6)])
        rows, _ = history_import.parse(path)
        self.assertEqual([r[0] for r in rows], ["2025-06"])

    def test_an_empty_export_is_not_an_error(self):
        path = csv_file([])
        rows, meta = history_import.parse(path)
        self.assertEqual(rows, [])
        self.assertEqual(meta["rows_banked"], 0)


class Bank(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        real = db.DB_PATH
        db.DB_PATH = self.path
        self.conn = db.connect()
        db.DB_PATH = real

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_reimport_is_idempotent(self):
        path = csv_file([line(month=7), line(month=8)])
        history_import.bank(path, conn=self.conn)
        meta = history_import.bank(path, conn=self.conn)
        self.assertEqual(meta["new_rows"], 0)
        self.assertEqual(meta["total_rows"], 2)

    def test_separate_years_accumulate(self):
        a = csv_file([line(month=7)], name="History_backfill_2024.csv")
        b = csv_file([line(month=7)], name="History_backfill_2025.csv")
        history_import.bank(a, conn=self.conn)
        meta = history_import.bank(b, conn=self.conn)
        self.assertEqual(meta["total_rows"], 2)
        cov = history_import.coverage(self.conn)
        self.assertEqual(cov["first_month"], "2024-07")
        self.assertEqual(cov["last_month"], "2025-07")

    def test_coverage_splits_by_market(self):
        path = csv_file([line(cur="USD", cost=10), line(cur="EUR", cost=5)])
        history_import.bank(path, conn=self.conn)
        markets = {m["market"]: m["spend"] for m in
                   history_import.coverage(self.conn)["by_market"]}
        self.assertEqual(markets["US"], 10.0)
        self.assertEqual(markets["EU"], 5.0)

    def test_import_is_logged(self):
        path = csv_file([line(month=7)])
        history_import.bank(path, conn=self.conn)
        rows = db.imported_file_log(self.conn, kind=history_import.KIND)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "2025-07")


if __name__ == "__main__":
    unittest.main()
