#!/usr/bin/env python3
"""KDP book economics (Spec: KDP support). Formula + fail-closed behavior.
Uses a temp config file — never the real kdp_books.json."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import kdp_econ  # noqa: E402


class KdpEcon(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)                 # start with no config
        self._orig = kdp_econ.CONFIG
        kdp_econ.CONFIG = self.path

    def tearDown(self):
        kdp_econ.CONFIG = self._orig
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_no_data_fails_closed(self):
        self.assertIsNone(kdp_econ.book_econ("B0BOOK1"))

    def test_direct_royalty_override(self):
        kdp_econ.save_book("B0BOOK1", {"list_price": 19.99, "royalty": 7.54})
        e = kdp_econ.book_econ("b0book1")     # case-insensitive
        self.assertAlmostEqual(e["royalty"], 7.54)
        self.assertAlmostEqual(e["break_even"], round(7.54 / 19.99, 4))
        self.assertTrue(e["known"])

    def test_paperback_bw_computed(self):
        # 300 pages b&w US, $19.99 -> rate 60%, print 1.00 + 0.012*300 = 4.60
        # royalty = 0.60*19.99 - 4.60 = 7.394 -> 7.39
        kdp_econ.save_book("B0BOOK2", {"list_price": 19.99, "format": "paperback",
                                       "ink": "bw", "page_count": 300, "marketplace": "US"})
        e = kdp_econ.book_econ("B0BOOK2")
        self.assertAlmostEqual(e["royalty"], 7.39, places=2)

    def test_rate_tier_under_999(self):
        # $8.99 -> 50% rate; flat print 2.30 for <=110 pages
        kdp_econ.save_book("B0BOOK3", {"list_price": 8.99, "page_count": 90})
        e = kdp_econ.book_econ("B0BOOK3")
        self.assertAlmostEqual(e["royalty"], round(0.50 * 8.99 - 2.30, 2), places=2)

    def test_color_unsupported_fails_closed(self):
        kdp_econ.save_book("B0BOOK4", {"list_price": 15.99, "ink": "color", "page_count": 40})
        self.assertIsNone(kdp_econ.book_econ("B0BOOK4"))   # no guessing color print cost

    def test_ebook_70_tier(self):
        kdp_econ.save_book("B0BOOK5", {"list_price": 4.99, "format": "ebook", "file_size_mb": 1.0})
        e = kdp_econ.book_econ("B0BOOK5")
        self.assertAlmostEqual(e["royalty"], round(0.70 * 4.99 - 0.06, 2), places=2)

    def test_clear(self):
        kdp_econ.save_book("B0BOOK6", {"list_price": 12.99, "royalty": 5.0})
        kdp_econ.clear_book("B0BOOK6")
        self.assertIsNone(kdp_econ.book_econ("B0BOOK6"))


if __name__ == "__main__":
    unittest.main()
