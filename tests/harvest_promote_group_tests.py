# tests/harvest_promote_group_tests.py
import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)
os.environ["ADS_MARKET"] = "US"
import db                       # noqa: E402
import harvest_promote_group as hpg   # noqa: E402
import phase4_harvest_create as p4    # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


def add_winner(conn, term, src_ag="scav1", cpc=0.20):
    conn.execute("""INSERT INTO harvest_log(search_term,source_ad_group_id,kind,product_type,
                    source_campaign_id,cpc,promoted) VALUES(?,?,?,?,?,?,0)""",
                 (term, src_ag, "keyword", "standard_tshirt", "c1", cpc))
    conn.commit()


def add_design(conn, asin, pt="standard_tshirt"):
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES(?,?,?)",
                 ("ag_"+asin, asin, pt))
    conn.commit()


class FakeClient:
    """No live Amazon calls — records every call and returns controllable canned
    responses shaped like the real ads_client.AdsClient create_* / list_all methods."""

    def __init__(self, existing_campaigns=None, existing_ad_groups=None, keywords_fail=False,
                 existing_keywords=None, negatives_fail=False):
        self.existing_campaigns = existing_campaigns or []
        self.existing_ad_groups = existing_ad_groups or []
        self.keywords_fail = keywords_fail
        self.existing_keywords = existing_keywords or []  # seeded "already created" keywords
        self.negatives_fail = negatives_fail
        self.calls = {"create_campaigns": [], "create_ad_groups": [], "create_product_ads": [],
                      "create_keywords": [], "create_negative_keywords": [], "list_all": [],
                      "list_keywords": []}
        self._next = {"campaignId": 9000, "adGroupId": 8000, "adId": 7000, "keywordId": 6000,
                      "negativeKeywordId": 5000}

    def _ids(self, kind, n):
        start = self._next[kind]; self._next[kind] += n
        return [str(start + i) for i in range(n)]

    def list_all(self, path, content_type, result_key, extra_body=None):
        self.calls["list_all"].append({"path": path, "result_key": result_key, "extra_body": extra_body})
        if result_key == "campaigns":
            return list(self.existing_campaigns)
        if result_key == "adGroups":
            return list(self.existing_ad_groups)
        return []

    def create_campaigns(self, items):
        self.calls["create_campaigns"].append(items)
        ids = self._ids("campaignId", len(items))
        success = [{"index": i, "campaignId": cid} for i, cid in enumerate(ids)]
        return 200, {"campaigns": {"success": success}}

    def create_ad_groups(self, items):
        self.calls["create_ad_groups"].append(items)
        ids = self._ids("adGroupId", len(items))
        success = [{"index": i, "adGroupId": aid} for i, aid in enumerate(ids)]
        return 200, {"adGroups": {"success": success}}

    def create_product_ads(self, items):
        self.calls["create_product_ads"].append(items)
        ids = self._ids("adId", len(items))
        success = [{"index": i, "adId": aid} for i, aid in enumerate(ids)]
        return 200, {"productAds": {"success": success}}

    def create_keywords(self, items):
        self.calls["create_keywords"].append(items)
        if self.keywords_fail:
            return 200, {"keywords": {"success": []}}
        ids = self._ids("keywordId", len(items))
        success = [{"index": i, "keywordId": kid} for i, kid in enumerate(ids)]
        return 200, {"keywords": {"success": success}}

    def list_keywords(self, campaign_ids):
        self.calls["list_keywords"].append(list(campaign_ids))
        return list(self.existing_keywords)

    def create_negative_keywords(self, items):
        # Real ads_client.AdsClient.create_negative_keywords returns a list of
        # per-batch result dicts, each with a `created_ids` list (one entry per
        # input item, None where that item errored) — mirror that shape here so
        # apply_group's real-return-value handling gets exercised, not a stub.
        self.calls["create_negative_keywords"].append(items)
        if self.negatives_fail:
            return [{"created_ids": [None for _ in items]}]
        ids = self._ids("negativeKeywordId", len(items))
        return [{"created_ids": ids}]


class BuildGroupPlan(unittest.TestCase):
    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def test_groups_by_type_and_computes_bid(self):
        add_winner(self.conn, "rookie first birthday", cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        add_design(self.conn, "B2", "standard_tshirt")
        add_design(self.conn, "B3", "standard_pullover_hoodie")
        plan = hpg.build_group_plan(self.conn, "rookie first birthday", "scav1", "c1",
                                    ["B1", "B2", "B3"])
        self.assertEqual(plan["bid"], 0.23)                 # 0.20 * 1.15
        types = {g["product_type"]: set(g["asins"]) for g in plan["groups"]}
        self.assertEqual(types["standard_tshirt"], {"B1", "B2"})
        self.assertEqual(types["standard_pullover_hoodie"], {"B3"})
        self.assertTrue(all(g["campaign_name"].startswith("Harvested") for g in plan["groups"]))

    def test_unmapped_asin_is_skipped_not_crashed(self):
        add_winner(self.conn, "rookie first birthday")
        add_design(self.conn, "B1", "standard_tshirt")
        plan = hpg.build_group_plan(self.conn, "rookie first birthday", "scav1", "c1",
                                    ["B1", "GHOST"])
        self.assertEqual(plan["skipped_asins"], ["GHOST"])
        self.assertEqual([a for g in plan["groups"] for a in g["asins"]], ["B1"])


class ApplyGroup(unittest.TestCase):
    """apply_group against a FakeClient — no live Amazon calls anywhere here."""

    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def _plan(self, term="rookie first birthday", asins=("B1", "B2", "B3")):
        add_winner(self.conn, term, cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        add_design(self.conn, "B2", "standard_tshirt")
        add_design(self.conn, "B3", "standard_pullover_hoodie")
        return hpg.build_group_plan(self.conn, term, "scav1", "c1", list(asins))

    def test_apply_success(self):
        plan = self._plan()
        client = FakeClient()
        result = hpg.apply_group(client, self.conn, plan)

        self.assertTrue(result["promoted"])
        self.assertEqual(result["negations"], 1)
        self.assertEqual(result["campaigns_created"], 2)      # two product types
        self.assertEqual(result["ad_groups_created"], 2)
        self.assertEqual(result["keywords_created"], 2)       # one keyword per group
        self.assertEqual(result["product_ads_created"], 3)    # B1, B2, B3

        # source negation fired exactly once, against the source ad group/campaign
        self.assertEqual(len(client.calls["create_negative_keywords"]), 1)
        self.assertEqual(client.calls["create_negative_keywords"][0],
                         [{"campaignId": "c1", "adGroupId": "scav1",
                           "keywordText": "rookie first birthday"}])

        row = self.conn.execute(
            "SELECT promoted FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
            ("rookie first birthday", "scav1")).fetchone()
        self.assertEqual(row[0], 1)

        log_row = self.conn.execute(
            "SELECT action FROM writes_log WHERE action='harvest_promote'").fetchone()
        self.assertIsNotNone(log_row)

    def test_apply_creates_nothing_does_not_negate_or_promote(self):
        # Amazon rejects every keyword create — campaigns/ad groups/product ads all
        # go through fine, but nothing "converts" to a live keyword.
        plan = self._plan()
        client = FakeClient(keywords_fail=True)
        result = hpg.apply_group(client, self.conn, plan)

        self.assertEqual(result["keywords_created"], 0)
        self.assertFalse(result["promoted"])
        self.assertEqual(result["negations"], 0)
        self.assertEqual(client.calls["create_negative_keywords"], [])

        row = self.conn.execute(
            "SELECT promoted FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
            ("rookie first birthday", "scav1")).fetchone()
        self.assertEqual(row[0], 0)

        log_row = self.conn.execute(
            "SELECT action FROM writes_log WHERE action='harvest_promote'").fetchone()
        self.assertIsNone(log_row)

    def test_apply_empty_groups_does_not_negate_or_promote(self):
        # Every chosen ASIN is unmapped -> plan["groups"] is empty outright.
        add_winner(self.conn, "rookie first birthday", cpc=0.20)
        plan = hpg.build_group_plan(self.conn, "rookie first birthday", "scav1", "c1", ["GHOST"])
        self.assertEqual(plan["groups"], [])

        client = FakeClient()
        result = hpg.apply_group(client, self.conn, plan)

        self.assertFalse(result["promoted"])
        self.assertEqual(result["negations"], 0)
        self.assertEqual(client.calls["create_negative_keywords"], [])

    def test_idempotent_retry_when_keyword_already_exists(self):
        # Simulates a RETRY of a prior run that created the keyword and then
        # failed before negating/promoting. Amazon rejects the retry's
        # create_keywords call as a duplicate (0 new ids), but the keyword is
        # actually live in the target ad group. The retry must still finish
        # the job — negate the source term and mark the winner promoted —
        # instead of stranding it in "Needs a design" forever.
        term = "rookie first birthday"
        add_winner(self.conn, term, cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        plan = hpg.build_group_plan(self.conn, term, "scav1", "c1", ["B1"])

        # One group -> one campaign/ad group -> FakeClient's fresh id counters
        # are deterministic, so the created ids are "9000"/"8000".
        client = FakeClient(
            keywords_fail=True,
            existing_keywords=[{"adGroupId": "8000", "campaignId": "9000",
                                "keywordText": term, "keywordId": "kw1", "bid": 0.23}])
        result = hpg.apply_group(client, self.conn, plan)

        self.assertEqual(result["keywords_created"], 0)      # nothing NEW created
        self.assertEqual(result["groups_with_keyword"], 1)   # but it's already live
        self.assertTrue(result["promoted"])
        self.assertEqual(result["negations"], 1)
        self.assertEqual(len(client.calls["list_keywords"]), 1)   # fallback check fired
        self.assertEqual(len(client.calls["create_negative_keywords"]), 1)

        row = self.conn.execute(
            "SELECT promoted FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
            (term, "scav1")).fetchone()
        self.assertEqual(row[0], 1)

    def test_negations_count_reflects_reality(self):
        # The keyword creates fine (live), but the negative-keyword write
        # fails. The promote must still go through — the keyword is live —
        # but negations must report 0, not the old hardcoded 1.
        plan = self._plan()
        client = FakeClient(negatives_fail=True)
        result = hpg.apply_group(client, self.conn, plan)

        self.assertTrue(result["promoted"])
        self.assertEqual(result["keywords_created"], 2)
        self.assertEqual(result["negations"], 0)
        self.assertEqual(len(client.calls["create_negative_keywords"]), 1)

        row = self.conn.execute(
            "SELECT promoted FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
            ("rookie first birthday", "scav1")).fetchone()
        self.assertEqual(row[0], 1)

    def test_reuses_existing_ad_group(self):
        term = "rookie first birthday"
        add_winner(self.conn, term, cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        plan = hpg.build_group_plan(self.conn, term, "scav1", "c1", ["B1"])

        camp_name = p4.camp_name("standard_tshirt")
        ag_name = f"{term[:70]} [standard_tshirt]"
        client = FakeClient(
            existing_campaigns=[{"campaignId": "c500", "name": camp_name}],
            existing_ad_groups=[{"campaignId": "c500", "name": ag_name, "adGroupId": "ag500"}],
        )
        result = hpg.apply_group(client, self.conn, plan)

        self.assertEqual(client.calls["create_ad_groups"], [])   # reused, not created
        self.assertEqual(result["ad_groups_created"], 0)
        self.assertEqual(result["campaigns_created"], 0)          # campaign reused too
        self.assertTrue(result["promoted"])

        # product ads + keyword landed on the REUSED ad group id, not a fresh one
        pa_items = client.calls["create_product_ads"][0]
        self.assertTrue(all(item["adGroupId"] == "ag500" for item in pa_items))
        kw_items = client.calls["create_keywords"][0]
        self.assertTrue(all(item["adGroupId"] == "ag500" for item in kw_items))


# append to tests/harvest_promote_group_tests.py
import json, subprocess

# timeout: these shell out to appctl against the REAL market database.
# With the app running, its serve workers can hold a lock, and SQLite
# waits forever by default — that is how the suite hung twice with
# nothing but an exit code to show for it. A timeout turns an
# indefinite hang into a named test failure.
SUBPROCESS_TIMEOUT = 60

class Endpoint(unittest.TestCase):
    def test_dry_run_returns_plan_no_apply(self):
        env = dict(os.environ, ADS_MARKET="US")
        payload = json.dumps({"term": "zzz-nomatch", "source_ad_group_id": "x",
                              "source_campaign_id": "c", "asins": []})
        p = subprocess.run(["python3", os.path.join(ENGINE, "appctl.py"), "harvest-promote-group"],
                           input=payload.encode(), capture_output=True, cwd=HERE, env=env, timeout=SUBPROCESS_TIMEOUT)
        d = json.loads(p.stdout.decode())
        self.assertTrue(d["ok"])
        self.assertFalse(d["data"]["applied"])
        self.assertIn("plan", d["data"])

    def test_missing_fields_returns_top_level_error(self):
        env = dict(os.environ, ADS_MARKET="US")
        payload = json.dumps({"term": "x"})   # missing source_ad_group_id/source_campaign_id
        p = subprocess.run(["python3", os.path.join(ENGINE, "appctl.py"), "harvest-promote-group"],
                           input=payload.encode(), capture_output=True, cwd=HERE, env=env, timeout=SUBPROCESS_TIMEOUT)
        d = json.loads(p.stdout.decode())
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    def test_malformed_stdin_returns_top_level_error(self):
        env = dict(os.environ, ADS_MARKET="US")
        p = subprocess.run(["python3", os.path.join(ENGINE, "appctl.py"), "harvest-promote-group"],
                           input=b"not json", capture_output=True, cwd=HERE, env=env, timeout=SUBPROCESS_TIMEOUT)
        d = json.loads(p.stdout.decode())
        self.assertFalse(d["ok"])
        self.assertIn("error", d)


class AFailedSourceNegativeIsReported(unittest.TestCase):
    """The keyword is live, so the term is not re-queued — that is deliberate.
    What was missing is the sentence: the audit row said "negated in <source>"
    whether or not the negative landed, so a failed negation left the term
    serving in BOTH places, competing with its own replacement and paying twice,
    with nothing anywhere saying so.

    Found by review, 2026-08-23.

    These three tests used to read `inspect.getsource(hpg.apply_group)` and
    search it for strings. That passes whether or not the code BEHAVES, and it
    would keep passing if the branch were made unreachable. Its `setUp` also
    called `fresh_conn()`, which does not exist in this file — guarded by
    `if "fresh_conn" in globals()`, so it silently produced None and no test
    ever noticed. Found 2026-08-24 by tests/undefined_name_lint_tests.py.

    They run against the FakeClient now, like every other test here.
    """

    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def _plan(self, term="rookie first birthday"):
        add_winner(self.conn, term, cpc=0.20)
        add_design(self.conn, "B1", "standard_tshirt")
        return hpg.build_group_plan(self.conn, term, "scav1", "c1", ["B1"])

    def _audit(self):
        return self.conn.execute(
            "SELECT detail, result FROM writes_log WHERE action='harvest_promote' "
            "ORDER BY rowid DESC LIMIT 1").fetchone()

    def test_a_landed_negative_reads_as_negated(self):
        result = hpg.apply_group(FakeClient(), self.conn, self._plan())
        self.assertTrue(result["source_negated"])

    def test_a_refused_negative_does_not_read_as_negated(self):
        result = hpg.apply_group(FakeClient(negatives_fail=True), self.conn, self._plan())
        self.assertFalse(result["source_negated"],
                         "a negative Amazon refused must not read as negated")

    def test_a_failed_negation_is_logged_as_failed(self):
        plan = self._plan()
        hpg.apply_group(FakeClient(negatives_fail=True), self.conn, plan)

        detail, result = self._audit()
        self.assertEqual("failed", result,
                         "a negation that did not land must not be logged as submitted")
        self.assertIn("was NOT created", detail)
        self.assertIn("competing with its", detail,
                      "the row has to say what it costs, not just that it failed")

    def test_a_successful_negation_is_logged_as_submitted(self):
        plan = self._plan()
        hpg.apply_group(FakeClient(), self.conn, plan)
        detail, result = self._audit()
        self.assertEqual("submitted", result)
        self.assertIn("negated in scav1", detail)

    def test_promoted_is_still_set_so_the_keyword_is_not_created_twice(self):
        # The destination keyword IS live, so re-queuing this term would create
        # it a second time. promoted=1 stays even when the negative fails.
        plan = self._plan()
        result = hpg.apply_group(FakeClient(negatives_fail=True), self.conn, plan)
        self.assertTrue(result["promoted"])
        row = self.conn.execute(
            "SELECT promoted FROM harvest_log WHERE search_term=? AND source_ad_group_id=?",
            ("rookie first birthday", "scav1")).fetchone()
        self.assertEqual(1, row[0])


class PhaseFourReportsWhatDidNotLand(unittest.TestCase):
    """The nightly's own promoter had the same hole one layer up.

    `phase4_harvest_create.apply()` already printed that a source negative had
    been refused, and logged the row as failed. Then `main()` returned None and
    the process exited 0, so `appctl promote` — which kept only the exit code —
    answered a green "keywords exit 0" for a run where every refused term was
    still serving in the ad group it was meant to leave, competing with the
    replacement that had just gone live.

    Driven against the FakeClient above, like the promote-group tests, so the
    counts come from the real code path rather than from its source text.
    """

    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def _adgroups(self, term="rookie first birthday"):
        add_winner(self.conn, term, cpc=0.20)
        # The winner's SOURCE ad group is the one that must carry the design:
        # phase4 reads the ASIN and type off it.
        self.conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type)"
                          " VALUES('scav1','B1','standard_tshirt')")
        self.conn.commit()
        adgroups, _skipped = p4.build_plan(self.conn, None)
        return adgroups

    def test_a_clean_promotion_counts_what_landed(self):
        got = p4.apply(FakeClient(), self.conn, self._adgroups())
        self.assertEqual(1, got["requested"])
        self.assertEqual(1, got["created"])
        self.assertEqual(1, got["negatives_requested"])
        self.assertEqual(1, got["negatives_landed"])
        self.assertEqual(0, got["negatives_refused"])
        self.assertEqual(0, p4.report(got))

    def test_a_refused_source_negative_is_counted_and_exits_non_zero(self):
        got = p4.apply(FakeClient(negatives_fail=True), self.conn, self._adgroups())
        self.assertEqual(1, got["negatives_refused"])
        self.assertEqual(0, got["negatives_landed"])
        self.assertEqual(1, p4.report(got),
                         "exiting 0 is what let the app call this a success")

    def test_a_run_that_could_not_start_says_so(self):
        """Amazon refused every campaign, so nothing was promoted at all."""

        class NoCampaigns(FakeClient):
            def create_campaigns(self, items):
                self.calls["create_campaigns"].append(items)
                return 400, {"campaigns": {"error": [{"index": 0}]}}

        got = p4.apply(NoCampaigns(), self.conn, self._adgroups())
        self.assertEqual("no campaigns available", got["aborted"])
        self.assertEqual(1, p4.report(got))

    def test_the_counts_are_printed_where_appctl_reads_them(self):
        import contextlib
        import io
        import json
        got = p4.apply(FakeClient(negatives_fail=True), self.conn, self._adgroups())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p4.report(got)
        line = buf.getvalue().splitlines()[-1]
        self.assertTrue(line.startswith(p4.RESULT_PREFIX))
        self.assertEqual(1, json.loads(line[len(p4.RESULT_PREFIX):])["negatives_refused"])


class TheAppIsToldWhatDidNotLand(unittest.TestCase):
    """`appctl promote` carried the exit code and 1500 characters of log tail.
    Neither could say how many source negatives Amazon refused."""

    def test_the_counts_are_read_back_out_of_the_phase_output(self):
        import appctl
        import json
        text = ("  exact keywords created: 3/3 (HTTP 200)\n"
                "  source negations: 1/3 landed\n"
                + p4.RESULT_PREFIX + json.dumps(
                    {"phase": "keywords", "negatives_requested": 3,
                     "negatives_landed": 1, "negatives_refused": 2}) + "\n")
        got = appctl._promote_summary(text)
        self.assertTrue(got["reported"])
        self.assertEqual(2, got["negatives_refused"])

    def test_a_phase_that_reported_nothing_is_unverified_not_clean(self):
        import appctl
        got = appctl._promote_summary("Done. 4 terms promoted + negated.\n")
        self.assertFalse(got["reported"])
        self.assertIn("unverified", got["note"])
