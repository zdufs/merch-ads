#!/usr/bin/env python3
"""Unit tests for the product-grid export reader (Snap for MOD + MerchFlow).
Run from the Ads folder:  python3 -m unittest tests.export_reader_tests -v
No Amazon API, no production DB — temp CSV fixtures only."""

import os
import sys

# tests/ on the path so this works whether unittest imports us as `tests.x`
# (package) or `x` (what `unittest discover` does).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402  (isolates the operator overlay)
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import export_reader                           # noqa: E402
import products                                # noqa: E402

SNAP_HEADER = ("Marketplace,Price,On Sale,ASIN,Ad-safe ASIN,Status,Product Type,"
               "Brand,Product Title,Sales,Created Date,Design ID\n")

MERCHFLOW_HEADER = ("listingId,status,asin,productTitle,marketplace,salesTotal,"
                    "adAsins,createdDate,listPrice,productType\n")

# The real dated Merch royalty report, byte-order mark and all.
SALES_REPORT_HEADER = ('\ufeff"Mkt","Date","ASIN","Title","Category 1","Category 2",'
                       '"Category 3","Product Type","Purchased","Cancelled","Returned",'
                       '"Revenue","Royalties","Currency"\n')


def write_csv(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class DetectTests(unittest.TestCase):
    def test_snap_header(self):
        p = write_csv(SNAP_HEADER)
        self.assertEqual(export_reader.detect(p), export_reader.SNAP)
        self.assertTrue(export_reader.is_export(p))
        os.remove(p)

    def test_merchflow_header(self):
        p = write_csv(MERCHFLOW_HEADER)
        self.assertEqual(export_reader.detect(p), export_reader.MERCHFLOW)
        os.remove(p)

    def test_the_merch_sales_report_is_not_an_export(self):
        """The dated SALES_REPORT carries "Product Type" and "ASIN" columns too.
        Reading it as a product grid would send royalty history to the campaign
        builder, so the check needs a column only the product grid has."""
        p = write_csv(SALES_REPORT_HEADER)
        self.assertFalse(export_reader.is_export(p))
        with self.assertRaises(export_reader.UnknownExportFormat):
            export_reader.detect(p)
        os.remove(p)

    def test_snap_export_without_a_marketplace_column_is_still_found(self):
        p = write_csv("ASIN,Ad-safe ASIN,Status,Product Type,Design ID\n")
        self.assertEqual(export_reader.detect(p), export_reader.SNAP)
        os.remove(p)


class SnapTranslationTests(unittest.TestCase):
    def rows(self, body):
        p = write_csv(SNAP_HEADER + body)
        try:
            return list(export_reader.rows(p))
        finally:
            os.remove(p)

    def test_row_translates_to_merchflow_shape(self):
        r = self.rows('US,"$21.99",No,B0TUMBLER1,,Live,Tumbler,Retro Name Vault,'
                      'REN Pewter Quartz Kite,3,"Aug 12, 2026, 1:11 PM",design-1\n')[0]
        self.assertEqual(r["marketplace"], "us")
        self.assertEqual(r["status"], "published")
        self.assertEqual(r["asin"], "B0TUMBLER1")
        self.assertEqual(r["productType"], "tumbler")
        self.assertEqual(r["salesTotal"], "3")
        self.assertEqual(r["createdDate"], "2026-08-12")
        self.assertEqual(r["listPrice"], "21.99")
        self.assertEqual(r["designId"], "design-1")

    def test_uk_maps_to_the_merchflow_marketplace_code(self):
        r = self.rows('UK,"£15.99",No,B0POPSOCK1,,Live,PopSocket,B,T,0,'
                      '"Aug 12, 2026, 1:11 PM",d\n')[0]
        self.assertEqual(r["marketplace"], "gb")
        self.assertEqual(r["productType"], "pop_socket")

    def test_a_non_live_listing_is_not_published(self):
        r = self.rows('US,"$21.99",No,B0TUMBLER1,,Draft,Tumbler,B,T,0,'
                      '"Aug 12, 2026, 1:11 PM",d\n')[0]
        self.assertNotEqual(r["status"], "published")

    def test_unknown_type_keeps_the_label_and_has_no_engine_type(self):
        r = self.rows('US,"$34.99",No,B0TUMBLER1,,Live,Hardcover Journal,B,T,0,'
                      '"Aug 12, 2026, 1:11 PM",d\n')[0]
        self.assertEqual(r["productType"], "")
        self.assertEqual(r["productTypeLabel"], "Hardcover Journal")

    def test_ad_safe_asin_carries_through(self):
        r = self.rows('US,"$21.99",No,B0TUMBLER1,B0AAAAAAAA,Live,Tumbler,B,T,0,'
                      '"Aug 12, 2026, 1:11 PM",d\n')[0]
        self.assertEqual(r["adAsins"], "B0AAAAAAAA")

    def test_merchflow_rows_pass_through_untouched(self):
        p = write_csv(MERCHFLOW_HEADER
                      + "L1,published,B0X,Title,us,7,,2026-08-12,21.99,standard_tshirt\n")
        r = list(export_reader.rows(p))[0]
        os.remove(p)
        self.assertEqual(r["productType"], "standard_tshirt")
        self.assertEqual(r["marketplace"], "us")
        self.assertEqual(r["createdDate"], "2026-08-12")


class FieldParsingTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(export_reader.snap_date("Aug 12, 2026, 1:11 PM"), "2026-08-12")
        self.assertEqual(export_reader.snap_date("Aug 12, 2026"), "2026-08-12")
        self.assertEqual(export_reader.snap_date("2026-08-12"), "2026-08-12")
        self.assertEqual(export_reader.snap_date("whenever"), "")
        self.assertEqual(export_reader.snap_date(""), "")

    def test_prices(self):
        self.assertEqual(export_reader.snap_price("$21.99"), "21.99")
        self.assertEqual(export_reader.snap_price("£15.99"), "15.99")
        self.assertEqual(export_reader.snap_price("€12,99"), "12.99")
        self.assertEqual(export_reader.snap_price(""), "")

    def test_sales_counts(self):
        self.assertEqual(export_reader.snap_number("1,234"), "1234")
        self.assertEqual(export_reader.snap_number(""), "0")


class TypeLabelTests(unittest.TestCase):
    def test_known_labels(self):
        cases = {
            "Tumbler": "tumbler",
            "Water Bottle": "water_bottle",
            "PopSocket": "pop_socket",
            "iPhone Case": "phone_case_apple_iphone",
            "Throw Pillow": "throw_pillow",
            "Crop Top": "crop_top",
            "Sweatshirt": "standard_sweatshirt",
            "Pullover Hoodie": "standard_pullover_hoodie",
            "Trucker Hat": "printed_trucker_hat",
            "T-Shirt": "standard_tshirt",
        }
        for label, want in cases.items():
            self.assertEqual(products.type_from_export_label(label), want, label)

    def test_engine_strings_pass_through(self):
        self.assertEqual(products.type_from_export_label("standard_tshirt"), "standard_tshirt")

    def test_unknown_label_returns_none(self):
        # Basketball Jersey used to sit here; the operator sells them now, so it
        # is a real shipped type. These two are still unpriced in the catalogue.
        for label in ("Sport Backpack", "Hardcover Journal", ""):
            self.assertIsNone(products.type_from_export_label(label), label)

    def test_a_near_miss_tee_is_never_guessed_into_the_lottery_type(self):
        """standard_tshirt routes designs into the lottery campaigns, so a label
        we do not know must come back None instead of close enough."""
        for label in ("Ringer T-Shirt", "Sport Jersey Shirt"):
            self.assertNotEqual(products.type_from_export_label(label), "standard_tshirt", label)


class CatalogTests(unittest.TestCase):
    """The catalog is several files now: Snap for MOD exports at most 100k rows,
    so a refresh arrives in chunks and the newest chunk must win."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SNAP_HEADER + body if name.startswith("snap") else body)
        return path

    def snap_row(self, asin, price, market="US", ptype="Tumbler", sales="0"):
        return (f'{market},"{price}",No,{asin},,Live,{ptype},B,T,{sales},'
                f'"Aug 12, 2026, 1:11 PM",d\n')

    def test_file_date_reads_both_names(self):
        self.assertEqual(export_reader.file_date("snap-grid-export-2026-08-15_23-53-46.csv"),
                         "2026-08-15")
        self.assertEqual(export_reader.file_date("export_products_2026-08-04T16_30_41.366Z.csv"),
                         "2026-08-04")
        self.assertIsNone(export_reader.file_date("SALES_REPORT-8_1_26-8_12_26.csv"))

    def test_files_are_listed_newest_first(self):
        self.write("snap-grid-export-2026-08-01_10-00-00.csv", self.snap_row("B0OLD", "$21.99"))
        self.write("snap-grid-export-2026-08-15_10-00-00.csv", self.snap_row("B0NEW", "$21.99"))
        names = [os.path.basename(p) for p in export_reader.catalog_files(self.dir)]
        self.assertEqual(names[0], "snap-grid-export-2026-08-15_10-00-00.csv")

    def test_newest_chunk_wins_for_a_repeated_listing(self):
        self.write("snap-grid-export-2026-08-01_10-00-00.csv", self.snap_row("B0AAA", "$23.99"))
        self.write("snap-grid-export-2026-08-15_10-00-00.csv", self.snap_row("B0AAA", "$21.99"))
        rows = list(export_reader.catalog_rows(self.dir))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["listPrice"], "21.99")
        self.assertEqual(rows[0]["_as_of"], "2026-08-15")

    def test_chunks_add_coverage_instead_of_replacing_it(self):
        self.write("snap-grid-export-2026-08-15_10-00-00.csv", self.snap_row("B0AAA", "$21.99"))
        self.write("snap-grid-export-2026-08-15_11-00-00.csv", self.snap_row("B0BBB", "$21.99"))
        asins = {r["asin"] for r in export_reader.catalog_rows(self.dir)}
        self.assertEqual(asins, {"B0AAA", "B0BBB"})

    def test_the_same_asin_in_two_markets_is_two_listings(self):
        self.write("snap-grid-export-2026-08-15_10-00-00.csv",
                   self.snap_row("B0AAA", "$21.99", market="US")
                   + self.snap_row("B0AAA", "£15.99", market="UK"))
        rows = list(export_reader.catalog_rows(self.dir))
        self.assertEqual({r["marketplace"] for r in rows}, {"us", "gb"})

    def test_marketplace_filter_keeps_one_market(self):
        self.write("snap-grid-export-2026-08-15_10-00-00.csv",
                   self.snap_row("B0AAA", "$21.99", market="US")
                   + self.snap_row("B0BBB", "€12.99", market="DE"))
        rows = list(export_reader.catalog_rows(self.dir, marketplace="de"))
        self.assertEqual([r["asin"] for r in rows], ["B0BBB"])

    def test_a_merchflow_export_joins_the_same_catalog(self):
        self.write("snap-grid-export-2026-08-15_10-00-00.csv", self.snap_row("B0AAA", "$21.99"))
        self.write("export_products_2026-08-04T16_30_41.366Z.csv",
                   MERCHFLOW_HEADER
                   + "L1,published,B0ZZZ,Title,us,7,,2026-08-12,21.99,standard_tshirt\n")
        asins = {r["asin"] for r in export_reader.catalog_rows(self.dir)}
        self.assertEqual(asins, {"B0AAA", "B0ZZZ"})

    def test_signature_changes_when_a_chunk_is_added(self):
        self.write("snap-grid-export-2026-08-15_10-00-00.csv", self.snap_row("B0AAA", "$21.99"))
        first = export_reader.catalog_signature(self.dir)
        self.write("snap-grid-export-2026-08-15_11-00-00.csv", self.snap_row("B0BBB", "$21.99"))
        self.assertNotEqual(first, export_reader.catalog_signature(self.dir))

    def test_a_sales_report_in_the_folder_is_not_part_of_the_catalog(self):
        self.write("SALES_REPORT-8_1_26-8_12_26.csv", SALES_REPORT_HEADER)
        self.assertEqual(export_reader.catalog_files(self.dir), [])


class RoyaltyFallbackTests(unittest.TestCase):
    """Snap for MOD has no trailing-30 columns, so the royalty-per-unit rate the
    profit screen needs comes from the dated SALES_REPORT instead."""

    def rows(self):
        import datetime
        today = datetime.date(2026, 8, 16)
        return today, [
            dict(mkt=".com", date=datetime.date(2026, 8, 10), asin="B0AAA",
                 purchased=2, returned=0, royalty=10.56, revenue=43.98),
            dict(mkt=".com", date=datetime.date(2026, 8, 1), asin="B0AAA",
                 purchased=1, returned=0, royalty=5.28, revenue=21.99),
            dict(mkt=".com", date=datetime.date(2026, 5, 1), asin="B0AAA",
                 purchased=9, returned=0, royalty=99.0, revenue=200.0),   # outside 30d
            dict(mkt=".de", date=datetime.date(2026, 8, 10), asin="B0BBB",
                 purchased=1, returned=0, royalty=2.69, revenue=19.99),
        ]

    def test_rate_is_royalty_over_units_in_the_window(self):
        import traz
        today, rows = self.rows()
        rate = traz.royalty_per_unit(30, mkt=".com", rows=rows, today=today)
        self.assertEqual(rate, {"B0AAA": 5.28})

    def test_other_markets_are_excluded_unless_asked_for(self):
        import traz
        today, rows = self.rows()
        self.assertNotIn("B0BBB", traz.royalty_per_unit(30, mkt=".com", rows=rows, today=today))
        self.assertIn("B0BBB", traz.royalty_per_unit(30, mkt=None, rows=rows, today=today))

    def test_no_sales_in_the_window_means_no_rate_rather_than_zero(self):
        import traz
        today, rows = self.rows()
        rate = traz.royalty_per_unit(1, mkt=".com", rows=rows, today=today)
        self.assertEqual(rate, {})


class SnapshotBankingTests(unittest.TestCase):
    """export_snapshot banks per-ASIN economics from each catalog file. It has
    to read a Snap chunk, and it has to bank EVERY unbanked chunk — banking only
    the newest file would leave most of a chunked refresh unbanked."""

    def setUp(self):
        import db
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.real = db.DB_PATH
        db.DB_PATH = self.db_path
        self.conn = db.connect()
        db.DB_PATH = self.real
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        self.conn.close()

    def write(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SNAP_HEADER + body)
        return path

    def test_banks_a_snap_chunk(self):
        import export_snapshot
        path = self.write("snap-grid-export-2026-08-15_10-00-00.csv",
                          'US,"$21.99",No,B0AAA,,Live,Standard T-Shirt,B,T,4,'
                          '"Aug 12, 2026, 1:11 PM",d\n')
        meta = export_snapshot.snapshot(path, conn=self.conn, asins={"B0AAA"})
        self.assertEqual(meta["banked"], 1)
        row = self.conn.execute(
            "SELECT asin, marketplace, product_type, list_price, sales_total"
            " FROM asin_econ_snapshot").fetchone()
        self.assertEqual(row[0], "B0AAA")
        self.assertEqual(row[1], "us")
        self.assertEqual(row[2], "standard_tshirt")
        self.assertEqual(row[3], "21.99")
        self.assertEqual(row[4], 4)

    def test_export_date_comes_from_the_snap_filename(self):
        import export_snapshot
        self.assertEqual(
            export_snapshot.export_date("snap-grid-export-2026-08-15_23-53-46.csv"),
            "2026-08-15")


class CatalogReaderRegressionTests(unittest.TestCase):
    """Every module that reads the product grid must go through export_reader.

    Three modules were migrated by swapping only their "find the newest file"
    helper, leaving a raw csv.DictReader with MerchFlow column names behind it.
    Those read a Snap chunk as ZERO usable rows — silently. derive_econ is the
    one that mattered: it would have dropped all five EU markets to DEFAULT_ECON
    the moment a Snap file became the newest export."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def snap(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SNAP_HEADER + body)
        return path

    # The short SNAP_HEADER above omits Sales/Royalties positions these rows
    # need, so these two build the full header themselves.
    FULL_HEADER = ("Marketplace,Price,On Sale,ASIN,Ad-safe ASIN,Status,Product Type,"
                   "Brand,Product Title,Sales,Returns,Return Rate,Royalties,"
                   "Created Date,Design ID\n")

    def write_full(self, body):
        path = os.path.join(self.dir, "snap-grid-export-2026-08-15_10-00-00.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.FULL_HEADER + body)
        return path

    def test_derive_econ_reads_a_snap_only_catalog(self):
        import derive_econ
        # three DE tees, 2 sales each at EUR 5.00 total -> 2.50 per unit
        self.write_full("".join(
            f'DE,"€18.45",No,B0DE{i:06d},,Live,Standard T-Shirt,B,T,2,0,,"€5.00",'
            f'"Aug 12, 2026, 1:11 PM",d\n'
            for i in range(3)))
        roy, price = derive_econ.collect("de", folder=self.dir)
        self.assertIn("standard_tshirt", roy,
                      "derive_econ found no rows in a Snap-only catalog")
        self.assertAlmostEqual(roy["standard_tshirt"][0], 2.50, places=2)
        self.assertAlmostEqual(price["standard_tshirt"][0], 18.45, places=2)

    def test_derive_econ_ignores_other_markets(self):
        import derive_econ
        self.write_full('US,"$21.99",No,B0US000001,,Live,Standard T-Shirt,B,T,2,0,,'
                        '"$10.00","Aug 12, 2026, 1:11 PM",d\n')
        roy, _price = derive_econ.collect("de", folder=self.dir)
        self.assertEqual(dict(roy), {})


if __name__ == "__main__":
    unittest.main()
