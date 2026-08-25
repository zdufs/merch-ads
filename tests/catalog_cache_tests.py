#!/usr/bin/env python3
"""The catalogue cache must never change an answer — only how fast it arrives.

The product grid arrives as several CSV chunks in the POD folder, and
`export_reader.catalog_rows()` merges them on every call. The nightly makes
about twenty of those calls across seven markets, and one pass over 2.0 million
listings takes twenty seconds, so roughly seven minutes a night is spent parsing
the same bytes.

The cache banks that merge into a table. The rule that makes it safe is that it
is a PURE OPTIMISATION: whenever the table does not match the files on disk, the
reader falls back to the CSVs. A stale cache is therefore slow, never wrong.

That rule is the whole point of this file. The engine has already been bitten
once by a stale file quietly standing in for a fresh one — the 2026-08-22
Drinkware import built zero ads and reported Complete — so a cache that can
serve yesterday's catalogue is exactly the shape of bug this codebase cannot
afford twice.

Run from the Ads folder:  python3 -m unittest tests.catalog_cache_tests -v
No Amazon API, no production database — temp CSV fixtures only.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402  (isolates the operator overlay)
import ast  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import catalog_cache  # noqa: E402
import export_reader  # noqa: E402

MERCHFLOW_HEADER = ("listingId,status,asin,productTitle,marketplace,salesTotal,"
                    "adAsins,createdDate,listPrice,productType,brandName,"
                    "royaltyTotal,salesLast30,royaltyLast30\n")


def mf_row(asin, mkt="us", price="19.99", title="A Design", sales="3",
           ptype="standard_tshirt", status="published", created="2026-08-01"):
    return (f"L{asin},{status},{asin},{title},{mkt},{sales},"
            f"{asin}AD,{created},{price},{ptype},MyBrand,12.34,1,4.11\n")


class CatalogFixture(unittest.TestCase):
    """A POD folder with real export files, and a cache database beside it."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="catalog-cache-")
        self.db = os.path.join(self.folder, "catalog_cache.sqlite")
        self._real_db_path = catalog_cache.db_path
        catalog_cache.db_path = lambda: self.db
        self.addCleanup(setattr, catalog_cache, "db_path", self._real_db_path)
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write_export(self, name, rows):
        path = os.path.join(self.folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(MERCHFLOW_HEADER)
            for r in rows:
                fh.write(r)
        return path


class CachedRowsMatchTheFiles(CatalogFixture):
    # Deliberately NOT in alphabetical order. See the ordering test below.
    OLD = ["B0ZZZ00001", "B0AAA00002"]
    NEW = ["B0MMM00003", "B0ZZZ00001"]

    def _two_chunks(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row(a) for a in self.OLD])
        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row(self.NEW[0]), mf_row(self.NEW[1], price="24.99")])

    def test_a_cached_read_is_identical_to_reading_the_csvs(self):
        """The core promise. Same rows, same fields, same order."""
        self._two_chunks()
        from_csv = list(export_reader._catalog_rows_csv(self.folder))
        catalog_cache.build(self.folder)
        from_cache = list(catalog_cache.read(self.folder))

        self.assertTrue(from_csv, "fixture produced no rows")
        self.assertEqual(len(from_cache), len(from_csv))
        for cached, csv_row in zip(from_cache, from_csv):
            for field, want in csv_row.items():
                if field.startswith("_") or field in catalog_cache.FIELDS:
                    self.assertEqual(cached.get(field), want,
                                     f"{field} differs for {csv_row['asin']}")

    def test_the_cache_yields_merge_order_not_asin_order(self):
        """A tripwire, and it is worth saying why it looks redundant.

        `_stream` ends its query with ORDER BY rowid. Removing that line breaks
        NOTHING today — measured, not assumed — because the read selects all
        twenty columns, so no index covers it and SQLite falls back to scanning
        the table, which is rowid order anyway.

        That is an accident of the column list, not a guarantee. With a query
        the UNIQUE (marketplace, asin) index CAN cover, SQLite returns ASIN
        order instead: inserting ZZZ, AAA, MMM and selecting them back gives
        AAA, MMM, ZZZ. Callers take the FIRST row they see for an ASIN, so a
        silent re-sort would change which chunk's price wins.

        So the ORDER BY stays, and this fixture is deliberately shuffled: the
        day that query becomes index-covered, this fails instead of quietly
        reordering the catalogue.
        """
        self._two_chunks()
        catalog_cache.build(self.folder)
        got = [r["asin"] for r in catalog_cache.read(self.folder)]
        self.assertEqual(got, [r["asin"] for r in
                               export_reader._catalog_rows_csv(self.folder)])
        self.assertNotEqual(got, sorted(got),
                            "fixture is no longer shuffled, so it can no longer "
                            "tell merge order from ASIN order")


class AStaleCacheIsRefused(CatalogFixture):
    """Every one of these costs twenty seconds. None of them costs an answer."""

    def test_a_new_export_makes_the_cache_stale(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001")])
        catalog_cache.build(self.folder)
        self.assertIsNotNone(catalog_cache.read(self.folder))

        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0NEW00001")])
        self.assertIsNone(catalog_cache.read(self.folder),
                          "a new export must invalidate the cache")

    def test_rewriting_an_export_in_place_makes_the_cache_stale(self):
        """The signature carries mtimes, so a chunk re-exported under the same
        name is a different catalogue even though the filename did not move."""
        name = "export_products_2026-08-01T00_00_00.000Z.csv"
        path = self.write_export(name, [mf_row("B0OLD00001")])
        catalog_cache.build(self.folder)
        self.write_export(name, [mf_row("B0OLD00001", price="24.99")])
        os.utime(path, (1, 1))
        self.assertIsNone(catalog_cache.read(self.folder))

    def test_no_cache_file_at_all(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001")])
        self.assertIsNone(catalog_cache.read(self.folder))

    def test_a_corrupt_cache_falls_back_instead_of_raising(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001")])
        catalog_cache.build(self.folder)
        with open(self.db, "r+b") as fh:      # scribble over the header page
            fh.write(b"not a database at all")
        self.assertIsNone(catalog_cache.read(self.folder))


class TheReaderFallsBack(CatalogFixture):
    """export_reader.catalog_rows is the seam. It must be impossible to tell
    from its output whether the cache was used."""

    def test_a_fresh_cache_is_used(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001"), mf_row("B0OLD00002")])
        catalog_cache.build(self.folder)

        # Make the CSVs unreadable. Anything that still comes back came from
        # the cache — no mock, no counter, just the files taken away.
        for name in os.listdir(self.folder):
            if name.endswith(".csv"):
                os.chmod(os.path.join(self.folder, name), 0o000)
                self.addCleanup(os.chmod, os.path.join(self.folder, name), 0o644)

        asins = [r["asin"] for r in export_reader.catalog_rows(self.folder)]
        self.assertEqual(sorted(asins), ["B0OLD00001", "B0OLD00002"])

    def test_a_stale_cache_never_hides_a_new_export(self):
        """The 2026-08-22 Drinkware failure in cache form: yesterday's
        catalogue served in place of today's, and every screen reading healthy.
        """
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001")])
        catalog_cache.build(self.folder)
        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0NEW00001")])

        asins = {r["asin"] for r in export_reader.catalog_rows(self.folder)}
        self.assertIn("B0NEW00001", asins, "the fresh export must reach the caller")
        self.assertIn("B0OLD00001", asins)

    def test_an_explicit_file_list_never_reads_the_cache(self):
        """`files=` is a scoped read of named files. Answering it from a cache
        of the whole catalogue would return listings the caller excluded."""
        old = self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                                [mf_row("B0OLD00001")])
        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0NEW00001")])
        catalog_cache.build(self.folder)

        asins = {r["asin"] for r in export_reader.catalog_rows(self.folder, files=[old])}
        self.assertEqual(asins, {"B0OLD00001"})

    def test_the_marketplace_filter_survives_the_cache(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0USA00001", mkt="us"),
                           mf_row("B0GBR00001", mkt="gb")])
        catalog_cache.build(self.folder)
        from_cache = {r["asin"] for r in export_reader.catalog_rows(self.folder,
                                                                    marketplace="gb")}
        self.assertEqual(from_cache, {"B0GBR00001"})

    def test_newest_file_wins_survives_the_round_trip(self):
        """The merge rule itself. A listing in two chunks is served from the
        newer one, and banking it must not resurrect the older price."""
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0DUPE0001", price="19.99")])
        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0DUPE0001", price="24.99")])
        catalog_cache.build(self.folder)

        rows = [r for r in export_reader.catalog_rows(self.folder)
                if r["asin"] == "B0DUPE0001"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["listPrice"], "24.99")
        self.assertEqual(rows[0]["_as_of"], "2026-08-20")


class BuildBehaviour(CatalogFixture):
    def test_building_twice_stores_the_same_catalogue(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001"), mf_row("B0OLD00002")])
        first = catalog_cache.build(self.folder)
        second = catalog_cache.build(self.folder)
        self.assertEqual(first, 2)
        self.assertEqual(second, 2)
        self.assertEqual(len(list(catalog_cache.read(self.folder))), 2)

    def test_a_removed_export_is_not_still_served(self):
        """A rebuild replaces the catalogue. It never merges into the old one."""
        gone = self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                                 [mf_row("B0GONE0001")])
        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0KEPT0001")])
        catalog_cache.build(self.folder)
        os.remove(gone)
        catalog_cache.build(self.folder)
        asins = {r["asin"] for r in catalog_cache.read(self.folder)}
        self.assertEqual(asins, {"B0KEPT0001"})

    def test_a_rebuild_reads_the_csvs_and_never_the_cache_it_is_replacing(self):
        """`build()` must not go through `catalog_rows()`.

        That function now consults the cache, so a build whose signature still
        matches would read its own table and bank it straight back — rebuilding
        the cache FROM the cache. Every value would be identical, so nothing
        would look wrong, and `--rebuild` would quietly stop being able to
        repair anything. It is the operator's one escape hatch; it has to reach
        the files.
        """
        import sqlite3
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001", price="19.99")])
        catalog_cache.build(self.folder)

        # Tamper with the banked value, leaving the signature alone so the
        # cache still reads as current.
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE catalog SET listPrice='999.00'")
        conn.commit()
        conn.close()
        self.assertEqual([r["listPrice"] for r in catalog_cache.read(self.folder)],
                         ["999.00"], "fixture did not actually tamper with the cache")

        catalog_cache.build(self.folder)
        self.assertEqual([r["listPrice"] for r in catalog_cache.read(self.folder)],
                         ["19.99"], "the rebuild read its own table instead of the CSV")

    def test_an_export_that_grew_without_its_mtime_moving_is_stale(self):
        """The half-copied chunk.

        The signature carries each file's name and mtime, both to one-second
        resolution. Drop a 17 MB chunk into the POD folder while the nightly is
        reading it and the build banks a TRUNCATED catalogue — then the copy
        finishes inside the same second, so the mtime never moves and the
        signature still matches. The cache would serve a partial catalogue and
        report itself current, which is the one failure mode a fallback cannot
        rescue because nothing asks for it.

        Size closes it: a file that grew is a different file.
        """
        name = "export_products_2026-08-01T00_00_00.000Z.csv"
        path = self.write_export(name, [mf_row("B0PART0001")])
        stamp = os.stat(path)
        catalog_cache.build(self.folder)
        self.assertIsNotNone(catalog_cache.read(self.folder))

        self.write_export(name, [mf_row("B0PART0001"), mf_row("B0REST0002")])
        os.utime(path, (stamp.st_atime, stamp.st_mtime))   # same second, as a fast copy would be
        self.assertEqual(int(os.stat(path).st_mtime), int(stamp.st_mtime),
                         "fixture failed to hold the mtime still")
        self.assertIsNone(catalog_cache.read(self.folder),
                          "the file grew — the cache must not still claim to match it")

    def test_an_empty_folder_is_never_banked_as_a_catalogue(self):
        """Banking zero listings saves nothing and invents a bad state.

        With no exports the CSV path returns nothing anyway, so an empty table
        buys no speed. What it does buy is a cache that reports `matches: true`
        holding `rows: 0` — an account with no products, stated confidently.
        That is the exact shape `paths.py` exists to prevent, and it would
        replace a perfectly good cache if the exports were merely moved aside
        for a moment.
        """
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0KEPT0001")])
        catalog_cache.build(self.folder)

        for name in os.listdir(self.folder):
            if name.endswith(".csv"):
                os.remove(os.path.join(self.folder, name))
        self.assertEqual(catalog_cache.build(self.folder), 0)

        st = catalog_cache.status(self.folder)
        self.assertEqual(st["rows"], 1, "the good cache was overwritten with an empty one")
        self.assertFalse(st["matches"], "no exports must never read as current")
        self.assertIsNone(catalog_cache.read(self.folder))

    def test_status_reports_staleness_rather_than_hiding_it(self):
        self.write_export("export_products_2026-08-01T00_00_00.000Z.csv",
                          [mf_row("B0OLD00001")])
        self.assertFalse(catalog_cache.status(self.folder)["available"])
        catalog_cache.build(self.folder)
        fresh = catalog_cache.status(self.folder)
        self.assertTrue(fresh["available"] and fresh["matches"])
        self.assertEqual(fresh["rows"], 1)

        self.write_export("export_products_2026-08-20T00_00_00.000Z.csv",
                          [mf_row("B0NEW00001")])
        stale = catalog_cache.status(self.folder)
        self.assertTrue(stale["available"])
        self.assertFalse(stale["matches"])
        self.assertIn("CSV", stale["note"])


class EveryFieldACallerReadsIsBanked(unittest.TestCase):
    """A field the cache does not store reads back as None — it does not raise.

    That is how a price silently stops reaching an economics rule, which is the
    failure this codebase keeps paying for. So the field set is not a comment:
    it is read out of the callers and checked.
    """

    CALLERS = ("map_products.py", "derive_econ.py", "demand_feed.py", "traz.py",
               "export_paused_asins.py", "lottery_build.py", "scavenger_build.py")

    @staticmethod
    def _catalogue_reads(source):
        """Every field name read off a catalogue row in one module, and every
        read whose field name this cannot see.

        Read from the syntax tree, not by grepping the file. These modules also
        walk Amazon API payloads and sales-report rows, which carry their own
        keys — a text search over the whole file reports those too, and the only
        way to quieten it is an allowlist that grows until it excuses the real
        thing. So: find the loops that actually iterate the catalogue, and look
        only at the loop variable inside them.

        Returns `(names, unreadable)`. A read like `p.get(field)` names its
        field with a variable, so no amount of reading the tree says which field
        it is. That is not a read this can approve, and for a while it was one
        this SKIPPED: `traz.load_asin_royalty(field="royaltyLast30")` reached
        the row that way, and changing its default to a MerchFlow-only column
        passed all twenty tests here while a literal `p.get("bsr")` two lines
        away failed them. Unreadable reads are returned so the test can fail on
        them instead.
        """
        tree = ast.parse(source)

        def is_catalogue(expr):
            return any(
                isinstance(n, ast.Call)
                and (getattr(n.func, "attr", None) in ("catalog_rows", "rows"))
                and "catalog_rows" in ast.unparse(expr)
                for n in ast.walk(expr))

        # `source = export_reader.rows(p) if p else export_reader.catalog_rows(…)`
        # then `for row in source:` — two of the seven callers are shaped this way.
        aliases = {t.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   for t in node.targets
                   if isinstance(t, ast.Name) and is_catalogue(node.value)}

        def literal(node):
            return isinstance(node, ast.Constant) and isinstance(node.value, str)

        found, unreadable = set(), []
        for node in ast.walk(tree):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            iterates = (is_catalogue(node.iter)
                        or (isinstance(node.iter, ast.Name) and node.iter.id in aliases))
            if not iterates:
                continue
            var = node.target.id
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "get"
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == var
                        and inner.args):
                    if literal(inner.args[0]):
                        found.add(inner.args[0].value)
                    else:
                        unreadable.append((inner.lineno, ast.unparse(inner)))
                # `row["asin"]` reads the same field a different way.
                if (isinstance(inner, ast.Subscript)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == var):
                    if literal(inner.slice):
                        found.add(inner.slice.value)
                    else:
                        unreadable.append((inner.lineno, ast.unparse(inner)))
        return found, unreadable

    def test_every_snap_field_is_banked(self):
        """FIELDS claims to be exactly what export_reader._snap_row produces,
        plus the two the merge adds. Read that claim out of the function rather
        than trusting the comment: a Snap field nobody banks is a field that
        vanishes the moment the cache is warm."""
        import inspect
        src = inspect.getsource(export_reader._snap_row).strip()
        ret = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Return)][0]
        produced = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
        self.assertTrue(produced, "could not read _snap_row's returned keys")
        self.assertEqual(produced - set(catalog_cache.FIELDS), set(),
                         "_snap_row produces fields the cache does not bank")
        self.assertEqual(set(catalog_cache.FIELDS) - produced, {"_source", "_as_of"},
                         "FIELDS carries something _snap_row never produces")

    def test_an_underscored_field_cannot_be_added_unnoticed(self):
        """_STORED drops every underscored field and _stream rebuilds exactly
        two of them by hand. A third would be missing from a cached row
        entirely — present when the CSVs are read, absent when they are not.
        The module refuses to import rather than let that happen."""
        # Run the module's OWN source with one extra underscored field, rather
        # than patching the imported copy — the guard runs at import, so that is
        # the only place it can be observed.
        src = open(catalog_cache.__file__, encoding="utf-8").read()
        anchor = '          "returnedTotal", "_source", "_as_of")'
        self.assertIn(anchor, src, "FIELDS no longer ends where this test expects")
        patched = src.replace(
            anchor, '          "returnedTotal", "_source", "_as_of", "_something_new")', 1)

        namespace = {"__name__": "catalog_cache_probe", "__file__": catalog_cache.__file__}
        with self.assertRaises(RuntimeError) as caught:
            exec(compile(patched, catalog_cache.__file__, "exec"), namespace)
        self.assertIn("_something_new", str(caught.exception))

        # And the real module still imports, so the guard is not simply always on.
        exec(compile(src, catalog_cache.__file__, "exec"),
             {"__name__": "catalog_cache_probe", "__file__": catalog_cache.__file__})

    def test_no_caller_reads_a_field_the_cache_does_not_store(self):
        engine = os.path.join(HERE, "engine")
        missing, seen_any = {}, 0
        for name in self.CALLERS:
            with open(os.path.join(engine, name), encoding="utf-8") as fh:
                fields, _ = self._catalogue_reads(fh.read())
            seen_any += len(fields)
            unbanked = {f for f in fields if f not in catalog_cache.FIELDS}
            if unbanked:
                missing[name] = sorted(unbanked)
        # A lint that reads an empty graph passes forever and says nothing.
        self.assertGreater(seen_any, 20,
                           "the scan found almost no catalogue reads — it has "
                           "stopped matching how the callers iterate")
        self.assertEqual(missing, {},
                         "these fields are read off a catalogue row but never "
                         "banked, so they would come back as None: add them to "
                         "catalog_cache.FIELDS")

    def test_no_caller_reaches_a_catalogue_field_through_a_variable(self):
        """The test above can only judge a field name it can read.

        A read like `p.get(field)` hides the name behind a variable, so the scan
        above simply did not see it — and a read nobody checks is worse than one
        nobody wrote, because the file looks covered. This was not theoretical:
        `traz.load_asin_royalty(field="royaltyLast30")` reached the row that
        way. Setting that default to `royaltyLast12Months`, a real MerchFlow
        column the cache does not bank, passed the whole suite. Every ASIN would
        have read 0.0 with the cache warm and a real royalty with it cold.

        So an unreadable read fails here rather than passing quietly. The fix is
        to write the field name out; if a caller genuinely needs to choose one,
        the choice belongs in `catalog_cache.FIELDS` where this can see it.
        """
        engine = os.path.join(HERE, "engine")
        hidden = {}
        for name in self.CALLERS:
            with open(os.path.join(engine, name), encoding="utf-8") as fh:
                _, unreadable = self._catalogue_reads(fh.read())
            if unreadable:
                hidden[name] = [f"line {ln}: {txt}" for ln, txt in unreadable]
        self.assertEqual(hidden, {},
                         "these read a catalogue field through a variable, so "
                         "no test can tell whether the cache banks it — write "
                         "the field name out as a literal")


if __name__ == "__main__":
    unittest.main()
