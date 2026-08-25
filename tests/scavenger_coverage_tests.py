#!/usr/bin/env python3
"""A stale side file must not erase a cohort, and a build must say what it missed.

On 2026-08-22 an import of 2,324 new US designs reported Complete with
"Drinkware 723" on the screen, and created ZERO drinkware ads.

Two independent silences produced that:

  1. The US Drinkware cohort was read from a DEDICATED ad-safe export instead of
     the main one. That file was dated 24 June, held none of the August ASINs,
     and once the intake scope filtered it the whole series left the plan. Four
     cohorts were built where five were asked for. Nothing raised, nothing
     warned, and the screen was quoting the REQUEST.

  2. `build_one` reuses a campaign by NAME whatever its state, so 446 new hat
     ads were created inside "SCAVENGER - Hats 1", PAUSED since June. Created,
     counted, reported as built — and unable to serve.

So a dedicated file now SUPPLEMENTS the main export, and every build writes a
coverage report naming what it could not do.

The third silence was found on 2026-08-25 and is covered at the bottom of this
file. Amazon REFUSED about 873 product ads a night, in every market, from
2026-06-25 — and nothing recorded a count or a reason. `chunked_create` read
only the `success` block and dropped both the HTTP status and the error block,
and a refused ad never joins Amazon's live product-ad list, so the same ASINs
went back the next night, and the next, for sixty nights.

Run from the Ads folder:  python3 -m unittest tests.scavenger_coverage_tests -v
"""

import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import scavenger  # noqa: E402
import scavenger_build as sb  # noqa: E402

MAIN_COLS = ["marketplace", "status", "asin", "productTitle", "productType",
             "adAsins", "salesTotal", "createdDate", "listPrice"]

NEW_RETAIL, NEW_ADSAFE = "B0NEWDRINK", "B0NEWADSAFE"
OLD_ADSAFE = "B0OLDADSAFE"
TEE_ASIN = "B0NEWTEE00"


def write_main_export(folder):
    """A MerchFlow-shaped export holding one NEW drinkware design and one tee."""
    path = os.path.join(folder, "export_products_test.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAIN_COLS)
        w.writeheader()
        w.writerow({"marketplace": "us", "status": "published", "asin": NEW_RETAIL,
                    "productTitle": "New Tumbler Design", "productType": "tumbler",
                    "adAsins": NEW_ADSAFE, "salesTotal": "0",
                    "createdDate": "2026-08-18", "listPrice": "24.99"})
        w.writerow({"marketplace": "us", "status": "published", "asin": TEE_ASIN,
                    "productTitle": "New Tee Design", "productType": "standard_tshirt",
                    "adAsins": "", "salesTotal": "0",
                    "createdDate": "2026-08-18", "listPrice": "19.99"})
    return path


def write_stale_adsafe(folder):
    """The dedicated drinkware export — two months old, none of the new ASINs."""
    path = os.path.join(folder, "export mugs, tumblers.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Market", "Status", "ASIN (Ad-Safe)",
                                           "Title", "Sales Total"])
        w.writeheader()
        w.writerow({"Market": "US", "Status": "Live", "ASIN (Ad-Safe)": OLD_ADSAFE,
                    "Title": "June Tumbler", "Sales Total": "7"})
    return path


class StaleSideFileCannotEraseACohort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.main = write_main_export(self.tmp)
        self.side = write_stale_adsafe(self.tmp)
        self._find = sb.find_source
        sb.find_source = lambda kw: self.side
        self.addCleanup(setattr, sb, "find_source", self._find)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def series_of(self, specs):
        return {s["series"]: [a for a, _ in s["asins"]] for s in specs}

    def test_new_design_the_side_file_never_saw_is_still_planned(self):
        """The exact 2026-08-22 shape: scope holds only ASINs the side file lacks."""
        specs = sb.load_build_specs(self.main, scope={NEW_ADSAFE, TEE_ASIN})
        got = self.series_of(specs)
        self.assertIn("Drinkware", got,
                      "a stale dedicated file erased the Drinkware series again")
        self.assertEqual(got["Drinkware"], [NEW_ADSAFE])

    def test_the_side_file_still_contributes_what_it_alone_carries(self):
        """Supplementing, not replacing — the file's own ASINs still reach the plan."""
        specs = sb.load_build_specs(self.main, scope={OLD_ADSAFE})
        got = self.series_of(specs)
        self.assertEqual(got.get("Drinkware"), [OLD_ADSAFE])

    def test_both_sources_merge_into_one_series(self):
        specs = sb.load_build_specs(self.main, scope={NEW_ADSAFE, OLD_ADSAFE})
        got = self.series_of(specs)
        self.assertEqual(sorted(got.get("Drinkware", [])),
                         sorted([NEW_ADSAFE, OLD_ADSAFE]))

    def test_the_retail_asin_of_a_hardgood_is_never_advertised(self):
        """adAsins is the ad-eligible ASIN; the retail one returns AD_INELIGIBLE."""
        specs = sb.load_build_specs(self.main, scope={NEW_ADSAFE, NEW_RETAIL})
        self.assertNotIn(NEW_RETAIL, self.series_of(specs).get("Drinkware", []))


class CoverageNamesWhatWasMissed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "outputs"))
        # Derive the report path from a temp root, never the operator's folder.
        self._here = sb.HERE
        sb.HERE = self.tmp
        self.addCleanup(setattr, sb, "HERE", self._here)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def report(self, scope, specs, plan, built, write=True):
        sb.write_coverage(scope, specs, plan, built, write)
        with open(sb.coverage_path()) as fh:
            return json.load(fh)

    def test_a_scoped_asin_no_series_claimed_is_counted(self):
        specs = [{"series": "Hoodies", "asins": [("A1", "t")]}]
        plan = [("Hoodies", [[("A1", "t")]])]
        rep = self.report({"A1", "A2", "A3"}, specs, plan,
                          [{"series": "Hoodies", "campaign": "SCAVENGER - Hoodies 1",
                            "state": "ENABLED", "added": 1}])
        self.assertEqual(rep["scoped"], 3)
        self.assertEqual(rep["planned"], 1)
        self.assertEqual(rep["unplanned"], 2)
        self.assertEqual(sorted(rep["unplanned_sample"]), ["A2", "A3"])

    def test_a_paused_campaign_that_took_ads_is_named(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        rep = self.report({"A1"}, specs, plan,
                          [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                            "state": "PAUSED", "added": 446}])
        self.assertEqual(rep["paused_campaigns"], ["SCAVENGER - Hats 1"])
        self.assertEqual(rep["series"][0]["added"], 446)

    def test_a_paused_campaign_that_took_nothing_is_not_an_alarm(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        rep = self.report({"A1"}, specs, plan,
                          [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                            "state": "PAUSED", "added": 0}])
        self.assertEqual(rep["paused_campaigns"], [])

    def test_the_shard_cap_tail_is_reported_not_swallowed(self):
        big = [(f"A{i}", "t") for i in range(3)]
        specs = [{"series": "Drinkware", "asins": big}]
        plan = [("Drinkware", [[big[0]]])]        # shard() kept one of three
        rep = self.report({a for a, _ in big}, specs, plan, [])
        row = rep["series"][0]
        self.assertEqual(row["matched"], 3)
        self.assertEqual(row["planned"], 1)
        self.assertEqual(row["over_cap"], 2)

    def test_an_empty_plan_still_writes_a_report(self):
        """The failure being guarded against produced NO plan at all."""
        rep = self.report({"A1", "A2"}, [], [], [])
        self.assertEqual(rep["planned"], 0)
        self.assertEqual(rep["unplanned"], 2)


class ABuildThatDiesPartWaySaysSo(unittest.TestCase):
    """A stopped build used to leave YESTERDAY's report on disk.

    On 2026-08-24 two markets stopped part-way. US hit the new write cap after
    475 product ads; DE lost every TLS call when the app bundle was replaced
    underneath the running nightly. Neither reached write_coverage, so
    outputs/scav_build_US.json still described the previous morning's
    successful build — same shape, same keys, `added` counts from a run that
    had finished. Only `as_of` said otherwise, and nothing reads it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "outputs"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._here, sb.HERE = sb.HERE, self.tmp
        self.addCleanup(setattr, sb, "HERE", self._here)
        self._argv, sys.argv = sys.argv, ["scavenger_build.py", "--apply", "--auto"]
        self.addCleanup(setattr, sys, "argv", self._argv)

        specs = [{"series": "Hoodies", "asins": [("A1", "t1")]},
                 {"series": "Hats", "asins": [("A2", "t2")]}]
        self._patch(sb, "load_build_specs", lambda *a, **k: specs)
        self._patch(sb.db, "connect", lambda *a, **k: None)
        self._patch(sb.killswitch, "check", lambda *a, **k: None)

        class _FakeClient:
            declared = False

            def list_all(self, *a, **k):
                return []

            def declare_campaign_builder(self):
                type(self).declared = True

        self.client_class = _FakeClient
        self._patch(sb, "AdsClient", _FakeClient)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def _report(self):
        with open(sb.coverage_path()) as fh:
            return json.load(fh)

    def test_the_report_names_what_stopped_the_build(self):
        calls = []

        def build_one(client, conn, series, n, asins, titles, write, camp_index):
            calls.append(series)
            if len(calls) == 1:
                return 1, 0, False, 0
            raise SystemExit("STOPPED: this automatic run has written 475 entities")

        self._patch(sb, "build_one", build_one)
        with self.assertRaises(SystemExit):
            sb.main()
        rep = self._report()
        self.assertIn("475", rep["stopped"])
        self.assertIn("SystemExit", rep["stopped"])

    def test_the_report_covers_only_what_finished(self):
        def build_one(client, conn, series, n, asins, titles, write, camp_index):
            if series == "Hoodies":
                return 1, 0, False, 0
            raise OSError("Could not find a suitable TLS CA certificate bundle")

        self._patch(sb, "build_one", build_one)
        with self.assertRaises(OSError):
            sb.main()
        rep = self._report()
        rows = {r["series"]: r for r in rep["series"]}
        self.assertEqual(1, rows["Hoodies"]["added"])
        self.assertEqual(0, rows["Hats"]["added"],
                         "a series that never ran was reported as built")
        self.assertIn("TLS", rep["stopped"])

    def test_a_failure_before_the_loop_is_recorded_too(self):
        """Listing the campaigns is a TLS call like any other, and the whole
        class of failure on 2026-08-24 was TLS. A build that dies there has
        written nothing, and the report has to say that rather than leave
        yesterday's."""
        class _Dead:
            def list_all(self, *a, **k):
                raise OSError("Could not find a suitable TLS CA certificate bundle")

            def declare_campaign_builder(self):
                pass

        self._patch(sb, "AdsClient", _Dead)
        self._patch(sb, "build_one", lambda *a, **k: (0, 0, False, 0))
        with self.assertRaises(OSError):
            sb.main()
        rep = self._report()
        self.assertIn("TLS", rep["stopped"])
        self.assertEqual(0, sum(r["added"] for r in rep["series"]))

    def test_a_report_that_cannot_be_written_does_not_replace_the_real_error(self):
        """Otherwise the log names a permissions problem where a write cap or a
        missing CA bundle actually stopped the build."""
        def explode(*a, **k):
            raise PermissionError("read-only file system")

        self._patch(sb, "write_coverage", explode)
        self._patch(sb, "build_one",
                    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the real one")))
        with self.assertRaises(RuntimeError) as cm:
            sb.main()
        self.assertIn("the real one", str(cm.exception))

    def test_a_build_that_finished_is_not_marked_stopped(self):
        self._patch(sb, "build_one",
                    lambda *a, **k: (2, 3, False, 0))
        sb.main()
        rep = self._report()
        self.assertIsNone(rep["stopped"])
        self.assertEqual(4, sum(r["added"] for r in rep["series"]))   # both series


class ApictlReadsTheReportBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        import appctl
        self.appctl = appctl

    def test_a_missing_report_is_reported_not_assumed_clean(self):
        got = self.appctl._scavenger_coverage(
            os.path.join(self.tmp, "nope.json"), scoped=12)
        self.assertFalse(got["available"])
        self.assertIn("unverified", got["note"])

    def test_the_refused_count_survives_the_read_back(self):
        """A field the app never sees is the same as not having it."""
        path = os.path.join(self.tmp, "scav_build_US.json")
        with open(path, "w") as fh:
            json.dump({"unplanned": 0, "refused": 738, "paused_campaigns": [],
                       "series": [{"series": "Hats", "matched": 479, "planned": 479,
                                   "added": 0, "refused": 194, "over_cap": 0,
                                   "paused_campaigns": []}]}, fh)
        got = self.appctl._scavenger_coverage(path, scoped=738)
        self.assertEqual(got["refused"], 738)
        self.assertEqual(got["series"][0]["refused"], 194)

    def test_the_bulk_sample_is_stripped_but_the_counts_survive(self):
        path = os.path.join(self.tmp, "scav_build_US.json")
        with open(path, "w") as fh:
            json.dump({"unplanned": 2, "unplanned_sample": ["A", "B"],
                       "series": [], "paused_campaigns": []}, fh)
        got = self.appctl._scavenger_coverage(path, scoped=2)
        self.assertTrue(got["available"])
        self.assertEqual(got["unplanned"], 2)
        self.assertNotIn("unplanned_sample", got)


# ---------------------------------------------------------------------------
# The refusals: counted in the report, explained on stderr, silent on stdout.
# ---------------------------------------------------------------------------


def refusal_payload(key, accepted, refused, id_field="adId"):
    """A 207 shaped the way the SP v3 create endpoints shape one.

    `accepted` is [index…]; `refused` is [(index, trigger)…]. The trigger is
    the ASIN for a product ad — which is the whole point of capturing it.
    """
    return {key: {
        "success": [{"index": i, id_field: f"id{i}"} for i in accepted],
        "error": [{"index": i,
                   "cause": {"trigger": trigger},
                   "errors": [{"errorType": "AD_INELIGIBLE",
                               "errorValue": {"otherError": {
                                   "reason": "ASIN is not eligible for advertising"}}}]}
                  for i, trigger in refused],
    }}


def live_refusal_payload(key, accepted, refused_indexes, id_field="adId"):
    """The 207 Amazon ACTUALLY sends, copied from the 2026-08-25 run.

    The difference from `refusal_payload` is the whole point: there is no
    `cause.trigger`. Amazon identifies a refused entry by its POSITION in the
    submitted batch and nothing else, and the errorType is `adEligibilityError`
    with `AD_INELIGIBLE` as the value rather than the other way round.

    All 164 reasons printed on 2026-08-25 had this shape, so every line read
    "0: adEligibilityError AD_INELIGIBLE" — the right reason attached to no
    design. The old fixture asserted the shape the diagnosis ASSUMED, which is
    why nothing caught it before the payload was seen live.
    """
    return {key: {
        "success": [{"index": i, id_field: f"id{i}"} for i in accepted],
        "error": [{"index": i,
                   "errors": [{"errorType": "adEligibilityError",
                               "errorValue": {"otherError": {
                                   "reason": "AD_INELIGIBLE"}}}]}
                  for i in refused_indexes],
    }}


class _Captured:
    """stdout and stderr, kept apart. Which stream a line lands on is the point."""

    def __init__(self):
        self.out, self.err = io.StringIO(), io.StringIO()

    def __enter__(self):
        self._out_cm = redirect_stdout(self.out)
        self._err_cm = redirect_stderr(self.err)
        self._out_cm.__enter__()
        self._err_cm.__enter__()
        return self

    def __exit__(self, *exc):
        self._err_cm.__exit__(*exc)
        self._out_cm.__exit__(*exc)
        return False


class ARefusedBatchSaysWhy(unittest.TestCase):
    """`chunked_create` held the HTTP status and the response body and dropped both.

    That is what made this invisible for sixty nights. The count it did print —
    "product ads added: 0/40" — reads as "everything here is already
    advertised", which is exactly what the comment beside it asserted.
    """

    def setUp(self):
        self._sleep = sb.time.sleep
        sb.time.sleep = lambda *a, **k: None
        self.addCleanup(setattr, sb.time, "sleep", self._sleep)

    def run_create(self, status, payload, items=3):
        batch_items = [{"asin": f"B0TEST{i:04d}"} for i in range(items)]
        with _Captured() as cap:
            ok = sb.chunked_create(lambda b: (status, payload), batch_items,
                                   "productAds", "adId", label="SCAVENGER - Hats 1")
        return ok, cap.out.getvalue(), cap.err.getvalue()

    def test_the_reasons_land_on_stderr_with_the_asin_and_the_status(self):
        payload = refusal_payload("productAds", [0],
                                  [(1, "B0TEST0001"), (2, "B0TEST0002")])
        ok, out, err = self.run_create(207, payload)
        self.assertEqual(ok, 1)
        self.assertIn("2 of 3 REFUSED", err)
        self.assertIn("HTTP 207", err)
        self.assertIn("B0TEST0001", err)
        self.assertIn("AD_INELIGIBLE", err)
        self.assertIn("not eligible for advertising", err)
        self.assertIn("SCAVENGER - Hats 1", err,
                      "a reason with no campaign beside it cannot be acted on")

    def test_the_reasons_never_reach_stdout(self):
        """appctl promises exactly one JSON object on stdout, and the builders
        run under it. A diagnostic on stdout is a broken envelope."""
        payload = refusal_payload("productAds", [], [(0, "B0TEST0000")])
        ok, out, err = self.run_create(207, payload, items=1)
        self.assertEqual(ok, 0)
        self.assertNotIn("REFUSED", out)
        self.assertNotIn("AD_INELIGIBLE", out)
        self.assertNotIn("B0TEST0000", out)

    def test_a_fully_accepted_batch_stays_quiet(self):
        """A line printed on every good night is a line nobody reads."""
        payload = refusal_payload("productAds", [0, 1, 2], [])
        ok, out, err = self.run_create(207, payload)
        self.assertEqual(ok, 3)
        self.assertEqual(err.strip(), "")

    def test_a_response_with_no_error_block_still_reports_the_count(self):
        """Amazon has changed this shape before. An unreadable body must still
        produce the count and the raw text, never silence."""
        ok, out, err = self.run_create(500, {"message": "internal failure"})
        self.assertEqual(ok, 0)
        self.assertIn("3 of 3 REFUSED", err)
        self.assertIn("HTTP 500", err)
        self.assertIn("internal failure", err)

    def test_an_unexpected_error_shape_is_printed_raw_rather_than_dropped(self):
        payload = {"productAds": {"success": [],
                                  "error": [{"index": 0, "somethingNew": "quota"}]}}
        ok, out, err = self.run_create(207, payload)
        self.assertIn("quota", err)

    def test_one_line_per_batch_cannot_flood_the_run_log(self):
        payload = refusal_payload(
            "productAds", [], [(i, f"B0TEST{i:04d}") for i in range(100)])
        ok, out, err = self.run_create(207, payload, items=100)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, err)
        self.assertLess(len(lines[0]), 1200, "a refusal line has to stay readable")


class _FakeAmazon:
    """Just enough client for build_one: one existing campaign, full on keywords."""

    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        self.submitted = []

    def list_all(self, *a, **k):
        return [{"adGroupId": "ag1"}]

    def list_product_ads(self, agids):
        return []

    def list_keywords(self, cids):
        return [{"keywordText": f"kw{i}"} for i in range(scavenger.MAX_KEYWORDS)]

    def create_product_ads(self, items):
        self.submitted.extend(items)
        return self.status, self.payload


class BuildOneReportsWhatAmazonTurnedDown(unittest.TestCase):
    def setUp(self):
        self._sleep = sb.time.sleep
        sb.time.sleep = lambda *a, **k: None
        self.addCleanup(setattr, sb.time, "sleep", self._sleep)
        self._log = sb.db.log_write
        sb.db.log_write = lambda *a, **k: None
        self.addCleanup(setattr, sb.db, "log_write", self._log)

    def build(self, status, payload, asins):
        client = _FakeAmazon(status, payload)
        name = scavenger.camp_name(1, "Hats")
        camp_index = {name: {"campaignId": "c1", "name": name, "state": "ENABLED"}}
        with _Captured() as cap:
            got = sb.build_one(client, None, "Hats", 1, asins,
                               ["t"] * len(asins), True, camp_index)
        return got, cap.out.getvalue(), cap.err.getvalue()

    def test_the_refused_count_leaves_the_function(self):
        asins = ["B0AAA00001", "B0AAA00002", "B0AAA00003"]
        payload = refusal_payload("productAds", [0],
                                  [(1, asins[1]), (2, asins[2])])
        (added, kw_added, used_recs, refused), out, err = self.build(207, payload, asins)
        self.assertEqual(added, 1)
        self.assertEqual(refused, 2,
                         "submitted minus accepted is what Amazon refused")
        self.assertIn("product ads added: 1/3", out)
        self.assertIn("2 of 3 product ads REFUSED", out)

    def test_a_night_with_nothing_refused_reports_zero(self):
        asins = ["B0AAA00001"]
        payload = refusal_payload("productAds", [0], [])
        (added, _, _, refused), out, err = self.build(207, payload, asins)
        self.assertEqual((added, refused), (1, 0))
        self.assertNotIn("REFUSED", out)

    def test_a_preview_run_refuses_nothing_because_it_submits_nothing(self):
        client = _FakeAmazon(207, refusal_payload("productAds", [], []))
        name = scavenger.camp_name(1, "Hats")
        camp_index = {name: {"campaignId": "c1", "name": name, "state": "ENABLED"}}
        with _Captured():
            added, kw_added, used_recs, refused = sb.build_one(
                client, None, "Hats", 1, ["B0AAA00001"], ["t"], False, camp_index)
        self.assertEqual((added, refused), (1, 0))
        self.assertEqual(client.submitted, [])


class TheCoverageReportCountsTheRefusals(unittest.TestCase):
    """`added` alone cannot tell a quiet night from a rejected one.

    The 2026-08-24 US run submitted 738 ASINs and created none of them. The
    report on disk said `added: 0`, which is the same number a market with
    nothing new to add writes. The Import screen read it as clean.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "outputs"))
        self._here, sb.HERE = sb.HERE, self.tmp
        self.addCleanup(setattr, sb, "HERE", self._here)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def report(self, specs, plan, built):
        with _Captured() as cap:
            sb.write_coverage(None, specs, plan, built, True)
        with open(sb.coverage_path()) as fh:
            return json.load(fh), cap.out.getvalue()

    def test_the_refusals_are_counted_per_series_and_in_total(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]},
                 {"series": "Drinkware", "asins": [("A2", "t")]}]
        plan = [("Hats", [[("A1", "t")]]), ("Drinkware", [[("A2", "t")]])]
        built = [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                  "state": "ENABLED", "added": 0, "kw_added": 0, "refused": 194},
                 {"series": "Drinkware", "campaign": "SCAVENGER - Drinkware 1",
                  "state": "ENABLED", "added": 271, "kw_added": 0, "refused": 289}]
        rep, out = self.report(specs, plan, built)
        rows = {r["series"]: r for r in rep["series"]}
        self.assertEqual(rows["Hats"]["refused"], 194)
        self.assertEqual(rows["Drinkware"]["refused"], 289)
        self.assertEqual(rows["Drinkware"]["added"], 271)
        self.assertEqual(rep["refused"], 483)
        self.assertIn("REFUSED 483", out)

    def test_the_campaign_that_was_refused_is_named(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        rep, _ = self.report(specs, plan,
                             [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                               "state": "PAUSED", "added": 0, "kw_added": 0,
                               "refused": 194}])
        camp = rep["series"][0]["campaigns"][0]
        self.assertEqual(camp["name"], "SCAVENGER - Hats 1")
        self.assertEqual(camp["refused"], 194)

    def test_a_build_that_refused_nothing_says_nothing(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        rep, out = self.report(specs, plan,
                               [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                                 "state": "ENABLED", "added": 1, "kw_added": 0,
                                 "refused": 0}])
        self.assertEqual(rep["refused"], 0)
        self.assertNotIn("REFUSED", out)


class AWholeBuildTotalsItsRefusals(unittest.TestCase):
    """End to end through main(), the way the nightly runs it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "outputs"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._here, sb.HERE = sb.HERE, self.tmp
        self.addCleanup(setattr, sb, "HERE", self._here)
        self._argv, sys.argv = sys.argv, ["scavenger_build.py", "--apply", "--auto"]
        self.addCleanup(setattr, sys, "argv", self._argv)
        specs = [{"series": "Hoodies", "asins": [("A1", "t1")]},
                 {"series": "Hats", "asins": [("A2", "t2")]}]
        self._patch(sb, "load_build_specs", lambda *a, **k: specs)
        self._patch(sb.db, "connect", lambda *a, **k: None)
        self._patch(sb.killswitch, "check", lambda *a, **k: None)

        class _Client:
            def list_all(self, *a, **k):
                return []

            def declare_campaign_builder(self):
                pass

        self._patch(sb, "AdsClient", _Client)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def test_every_series_refusal_reaches_the_top_level_count(self):
        def build_one(client, conn, series, n, asins, titles, write, camp_index):
            return (0, 0, False, 31) if series == "Hoodies" else (0, 0, False, 24)

        self._patch(sb, "build_one", build_one)
        with _Captured():
            sb.main()
        with open(sb.coverage_path()) as fh:
            rep = json.load(fh)
        self.assertEqual(rep["refused"], 55)
        rows = {r["series"]: r["refused"] for r in rep["series"]}
        self.assertEqual(rows, {"Hoodies": 31, "Hats": 24})

    def test_a_build_that_stops_still_counts_what_it_was_refused(self):
        """The 2026-08-24 US shape: the write cap stopped the run part-way, and
        the refusals up to that point are still the evidence."""
        def build_one(client, conn, series, n, asins, titles, write, camp_index):
            if series == "Hoodies":
                return 0, 0, False, 31
            raise SystemExit("STOPPED: this automatic run has written 475 entities")

        self._patch(sb, "build_one", build_one)
        with _Captured(), self.assertRaises(SystemExit):
            sb.main()
        with open(sb.coverage_path()) as fh:
            rep = json.load(fh)
        self.assertEqual(rep["refused"], 31)
        self.assertIn("475", rep["stopped"])


class ShardKeepsTheCohortInsideTheCampaignCap(unittest.TestCase):
    """`shard()` bounds a cohort, and the bound is what the coverage report
    calls `over_cap`.

    Found by mutation on 2026-08-24. Deleting the truncation, so a cohort is
    split into as many campaigns as it needs, broke nothing in the whole suite —
    and `shard` is a pure function of a list, which is about as testable as
    code gets. The coverage tests above use it, but only ever with a cohort far
    under the cap, so the line that enforces the cap never ran.

    Two things go wrong without it, and neither announces itself. The account
    grows more campaigns than MAX_CAMPAIGNS allows, each one a thing to budget,
    name and watch. And `over_cap` reports 0, because nothing was dropped — so
    the screen that exists to say "these designs got no ads" says everything
    landed.
    """

    def test_a_small_cohort_is_split_by_asins_per_campaign(self):
        cohort = list(range(scavenger.MAX_ASINS + 5))
        shards = sb.shard(cohort)
        self.assertEqual([len(s) for s in shards], [scavenger.MAX_ASINS, 5])

    def test_a_cohort_never_becomes_more_than_the_campaign_cap(self):
        cohort = list(range(scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS * 3))
        shards = sb.shard(cohort)
        self.assertEqual(len(shards), scavenger.MAX_CAMPAIGNS)
        self.assertTrue(all(len(s) == scavenger.MAX_ASINS for s in shards))

    def test_the_tail_past_the_cap_is_dropped_not_squeezed_in(self):
        """Dropping is the deliberate choice — the alternative is campaigns
        over the ASIN cap, which Amazon refuses. What matters is that the drop
        is a real drop, so `over_cap` can count it."""
        over = 137
        cohort = list(range(scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS + over))
        placed = [a for s in sb.shard(cohort) for a in s]
        self.assertEqual(len(placed),
                         scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS)
        self.assertEqual(len(cohort) - len(placed), over)
        self.assertEqual(placed, cohort[:len(placed)],
                         "the tail goes, not a slice from the middle")

    def test_an_exactly_full_cohort_drops_nothing(self):
        cohort = list(range(scavenger.MAX_ASINS * scavenger.MAX_CAMPAIGNS))
        placed = [a for s in sb.shard(cohort) for a in s]
        self.assertEqual(placed, cohort)

    def test_an_empty_cohort_makes_no_campaigns(self):
        self.assertEqual(sb.shard([]), [])

    def test_the_caps_are_the_shipped_numbers(self):
        self.assertEqual(scavenger.MAX_ASINS, 1000)
        self.assertEqual(scavenger.MAX_CAMPAIGNS, 6)



HARDGOOD_COLS = MAIN_COLS


def write_hardgood_export(folder):
    """Four listings that pin the whole rule.

    A hat WITH an ad-safe ASIN, a hat WITHOUT one, a tee (apparel never has one
    and does not need one), and a hat with no sales that no cohort would touch.
    """
    path = os.path.join(folder, "export_products_hardgoods.csv")
    rows = [
        {"asin": "B0HATSAFE0", "productType": "printed_trucker_hat",
         "adAsins": "B0HATADSAFE", "salesTotal": "5", "productTitle": "Safe Hat"},
        {"asin": "B0HATBARE0", "productType": "printed_trucker_hat",
         "adAsins": "", "salesTotal": "9", "productTitle": "Bare Hat"},
        {"asin": "B0TEEBARE0", "productType": "standard_tshirt",
         "adAsins": "", "salesTotal": "3", "productTitle": "Bare Tee"},
        {"asin": "B0HATZERO0", "productType": "printed_trucker_hat",
         "adAsins": "", "salesTotal": "0", "productTitle": "Unsold Hat"},
    ]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HARDGOOD_COLS)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r.update({"marketplace": "us", "status": "published",
                      "createdDate": "2020-01-01", "listPrice": "24.99"})
            w.writerow(r)
    return path


class AHardgoodWithNoAdSafeAsinIsSkipped(unittest.TestCase):
    """The retail ASIN of a hardgood is not advertisable, and never was.

    `scavenger.py` has said so in a comment since the module was written:
    "Listings with no ad-eligible ASIN simply can't be advertised and are
    skipped." The code did the opposite — it fell back to the retail ASIN and
    submitted it — and nothing recorded the outcome, so the claim went sixty
    nights without being checked.

    The 2026-08-25 run log is the first evidence. Every refused batch answered
    `adEligibilityError AD_INELIGIBLE`, and the counts matched the catalogue to
    the unit: SCAVENGER - Hats 1 submitted 194 and Amazon refused 194, against
    exactly 194 hats in that shard with no ad-safe ASIN. Drinkware matched the
    same way in four more shards, including a control of 1,000 ad-safe ASINs
    that refused nothing.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.export = write_hardgood_export(self.tmp)
        self._find, sb.find_source = sb.find_source, lambda kw: None
        self.addCleanup(setattr, sb, "find_source", self._find)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def series_of(self, **kw):
        specs = sb.load_build_specs(self.export, **kw)
        return {s["series"]: [a for a, _ in s["asins"]] for s in specs}

    def test_the_bare_hardgood_never_reaches_the_plan(self):
        got = self.series_of()
        self.assertEqual(got.get("Hats"), ["B0HATADSAFE"],
                         "the retail ASIN of a hat with no ad-safe ASIN was "
                         "submitted again — Amazon answers AD_INELIGIBLE")
        self.assertNotIn("B0HATBARE0", got.get("Hats", []))

    def test_apparel_still_advertises_its_own_retail_asin(self):
        """The fix must not reach apparel: a tee's retail ASIN IS eligible."""
        got = self.series_of()
        self.assertEqual(got.get("US Tees"), ["B0TEEBARE0"])

    def test_the_skip_is_counted_and_named_by_series(self):
        skipped = {}
        sb.load_build_specs(self.export, skipped=skipped)
        self.assertEqual(skipped, {"Hats": 1},
                         "a silent skip only moves the lie somewhere quieter")

    def test_a_design_no_cohort_would_have_advertised_is_not_counted(self):
        """Impact, not inventory.

        18,001 of the 19,177 designs the economics gate could not price were
        hats nothing advertised, and reporting that count as a problem was how
        a real warning got muted. The same trap applies here: the catalogue
        holds tens of thousands of unsold hats with no ad-safe ASIN, and none
        of them is lost coverage, because no cohort ever wanted them.
        """
        skipped = {}
        sb.load_build_specs(self.export, skipped=skipped)
        self.assertEqual(skipped.get("Hats"), 1,
                         "B0HATZERO0 has no sales and no cohort claims it")

    def test_a_scoped_bare_hardgood_is_counted_as_a_gap_too(self):
        """Intake asked for it by name, and it still cannot be advertised.

        Two gaps here, not one: the sold hat a cohort claims on its own, and
        the unsold one this scope asked for. Both are real — scoping is an
        explicit request to advertise a design — so both are counted.
        """
        skipped = {}
        got = self.series_of(scope={"B0HATZERO0"}, skipped=skipped)
        self.assertNotIn("Hats", got)
        self.assertEqual(skipped, {"Hats": 2})

    def test_the_ad_safe_types_are_the_hardgood_cohort_types(self):
        """The set is a type question and must stay one.

        Every type in it belongs to a cohort the builder actually builds, so a
        type added here can never quietly stop a series that has no hardgoods.
        """
        cohort_types = {t for c in scavenger.COHORTS for t in c["types"]}
        self.assertTrue(scavenger.AD_SAFE_REQUIRED_TYPES <= cohort_types)
        self.assertNotIn(scavenger.COHORT_TYPE, scavenger.AD_SAFE_REQUIRED_TYPES,
                         "the New Uploads cohort is tees; it must never be skipped")
        for t in ("standard_tshirt", "standard_pullover_hoodie",
                  "standard_sweatshirt"):
            self.assertFalse(scavenger.needs_ad_safe_asin(t))
        for t in ("mug", "tumbler", "water_bottle", "printed_trucker_hat",
                  "printed_baseball_hat", "sport_sun_visor"):
            self.assertTrue(scavenger.needs_ad_safe_asin(t))


class TheCoverageReportStatesTheHardgoodGap(unittest.TestCase):
    """Skipping is right; skipping in silence is the same failure one layer on.

    Several hundred US hardgood designs are advertised nowhere. Before this
    the builder submitted them and Amazon threw them away, so the loss was
    invisible.
    After it the builder does not submit them, so the loss would be invisible
    for a different reason. The count is what keeps it visible either way.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "outputs"))
        self._here, sb.HERE = sb.HERE, self.tmp
        self.addCleanup(setattr, sb, "HERE", self._here)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def report(self, specs, plan, built, no_ad_safe=None):
        with _Captured() as cap:
            sb.write_coverage(None, specs, plan, built, True, no_ad_safe=no_ad_safe)
        with open(sb.coverage_path()) as fh:
            return json.load(fh), cap.out.getvalue()

    def test_the_gap_is_reported_per_series_and_in_total(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        built = [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                  "state": "ENABLED", "added": 1, "kw_added": 0, "refused": 0}]
        rep, out = self.report(specs, plan, built,
                               no_ad_safe={"Hats": 194, "Drinkware": 280})
        self.assertEqual(rep["no_ad_safe"], 474)
        self.assertEqual(rep["no_ad_safe_series"], {"Hats": 194, "Drinkware": 280})
        rows = {r["series"]: r for r in rep["series"]}
        self.assertEqual(rows["Hats"]["no_ad_safe"], 194)
        self.assertIn("474", out)
        self.assertIn("ad-safe", out)

    def test_a_series_skipped_down_to_nothing_still_reports_its_gap(self):
        """The shape that would otherwise vanish.

        A series whose every listing lacks an ad-safe ASIN produces no spec, so
        it never reaches `series` at all. Reporting the gap only there would
        make it disappear exactly when it is total.
        """
        rep, out = self.report([], [], [], no_ad_safe={"Drinkware": 280})
        self.assertEqual(rep["no_ad_safe"], 280)
        self.assertEqual(rep["no_ad_safe_series"], {"Drinkware": 280})
        self.assertEqual(rep["series"], [])
        self.assertIn("280", out)

    def test_a_build_with_no_gap_says_nothing(self):
        specs = [{"series": "Hats", "asins": [("A1", "t")]}]
        plan = [("Hats", [[("A1", "t")]])]
        built = [{"series": "Hats", "campaign": "SCAVENGER - Hats 1",
                  "state": "ENABLED", "added": 1, "kw_added": 0, "refused": 0}]
        rep, out = self.report(specs, plan, built)
        self.assertEqual(rep["no_ad_safe"], 0)
        self.assertEqual(rep["no_ad_safe_series"], {})
        self.assertNotIn("ad-safe", out)


class ARefusalIsNamedByPositionAndMustStillNameTheDesign(unittest.TestCase):
    """Amazon sends an index, not an ASIN, and the batch is what decodes it.

    The 2026-08-25 run captured 164 refusal reasons across 39 batches and six
    markets. Every one carried `errorType: adEligibilityError` with the value
    `AD_INELIGIBLE`, and NOT ONE carried `cause.trigger`. So every printed line
    read "0: adEligibilityError AD_INELIGIBLE": the right reason, attached to
    nothing.

    That was enough to settle the hardgoods half, because whole cohorts were
    refused together — SCAVENGER - Hats 1 submitted 194 and Amazon refused 194,
    matching the 194 hats in that shard with no ad-safe ASIN exactly. It is not
    enough for the apparel half, where 88 of 3,682 tees are refused and the
    question is which 88. The index is the only handle Amazon gives, and the
    batch we submitted is the only thing that can turn it back into a design.
    """

    def setUp(self):
        self._sleep = sb.time.sleep
        sb.time.sleep = lambda *a, **k: None
        self.addCleanup(setattr, sb.time, "sleep", self._sleep)

    def run_create(self, payload, items=3, status=207):
        batch = [{"asin": f"B0TEST{i:04d}"} for i in range(items)]
        with _Captured() as cap:
            ok = sb.chunked_create(lambda b: (status, payload), batch,
                                   "productAds", "adId", label="SCAVENGER - US Tees 1")
        return ok, cap.out.getvalue(), cap.err.getvalue()

    def test_the_live_payload_still_names_the_asin(self):
        """The exact shape from the run log, which named no ASIN at all."""
        payload = live_refusal_payload("productAds", [0], [1, 2])
        ok, out, err = self.run_create(payload)
        self.assertEqual(ok, 1)
        self.assertIn("B0TEST0001", err,
                      "Amazon's index was printed raw, so the refusal named no design")
        self.assertIn("B0TEST0002", err)
        self.assertIn("adEligibilityError", err)
        self.assertIn("AD_INELIGIBLE", err)

    def test_the_index_is_kept_beside_the_asin(self):
        """The mapping is only as good as the caller passing the batch it sent,
        so the position stays readable next to the name it resolved to."""
        payload = live_refusal_payload("productAds", [], [2])
        ok, out, err = self.run_create(payload)
        self.assertIn("B0TEST0002 (#2)", err)

    def test_amazons_own_trigger_still_wins_when_it_sends_one(self):
        """`cause.trigger` is the documented field. If it comes back, use it."""
        payload = refusal_payload("productAds", [0], [(1, "B0REALASIN")])
        ok, out, err = self.run_create(payload)
        self.assertIn("B0REALASIN", err)
        self.assertNotIn("(#1)", err)

    def test_an_index_the_batch_cannot_explain_degrades_to_the_index(self):
        """Never invent a design. An index past the end of the batch, or a
        caller that passed no batch, prints the position and nothing more."""
        payload = live_refusal_payload("productAds", [], [99])
        ok, out, err = self.run_create(payload)
        self.assertIn("99:", err)
        self.assertNotIn("B0TEST", err)

    def test_a_reporter_given_no_batch_still_reports(self):
        from ads_client import report_refused
        payload = live_refusal_payload("productAds", [], [0])
        with _Captured() as cap:
            n = report_refused("productAds", 1, 0, 207, payload, label="X")
        self.assertEqual(n, 1)
        self.assertIn("AD_INELIGIBLE", cap.err.getvalue())

    def test_the_resolved_asin_never_reaches_stdout(self):
        payload = live_refusal_payload("productAds", [], [0])
        ok, out, err = self.run_create(payload, items=1)
        self.assertNotIn("B0TEST0000", out)
        self.assertNotIn("REFUSED", out)


if __name__ == "__main__":
    unittest.main()
