#!/usr/bin/env python3
"""Four ways this engine failed quietly, and the guards that stop them.

Every one was found by the five-pass audit of 2026-08-24.

Run from the Ads folder:  python3 -m unittest tests.fail_closed_tests -v
"""

import ast
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)


class EditingACorruptRoyaltyFileWritesNothing(unittest.TestCase):
    """Editing one royalty used to delete every other override.

    Every setter reads the WHOLE file, adds one key and writes the result back.
    `_read_raw` answered `{}` for a corrupt file exactly as it did for a missing
    one, so one edit replaced the file with a single entry. The read errors that
    had been closing the economics gate went with them, which let live writes
    resume on fallback economics.
    """

    def setUp(self):
        import royalty_config
        self.rc = royalty_config
        self.original = royalty_config.CONFIG
        self.dir = tempfile.mkdtemp()
        royalty_config.CONFIG = os.path.join(self.dir, "royalty_overrides.json")
        royalty_config.invalidate()

    def tearDown(self):
        self.rc.CONFIG = self.original
        self.rc.invalidate()

    def test_a_missing_file_is_still_an_empty_config(self):
        self.assertEqual({}, self.rc._read_raw())

    def test_a_corrupt_file_raises_and_the_setter_writes_nothing(self):
        with open(self.rc.CONFIG, "w", encoding="utf-8") as f:
            f.write('{"product_types": {"mug": {"royalty": 2.54, "price": 16.99')
        with open(self.rc.CONFIG, encoding="utf-8") as f:
            before = f.read()

        with self.assertRaises(self.rc.OverridesUnreadable):
            self.rc.set_product_type("hoodie", 5.00, 31.99)

        with open(self.rc.CONFIG, encoding="utf-8") as f:
            self.assertEqual(before, f.read(), "a failed edit rewrote the file anyway")

    def test_a_json_document_that_is_not_an_object_raises(self):
        with open(self.rc.CONFIG, "w", encoding="utf-8") as f:
            f.write('["not", "an", "object"]')
        with self.assertRaises(self.rc.OverridesUnreadable):
            self.rc._read_raw()

    def test_a_good_file_still_keeps_every_other_override(self):
        with open(self.rc.CONFIG, "w", encoding="utf-8") as f:
            json.dump({"version": 2,
                       "product_types": {"mug": {"royalty": 2.54, "price": 16.99}}}, f)
        self.rc.set_product_type("hoodie", 5.00, 31.99)
        with open(self.rc.CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertIn("mug", raw["product_types"])
        self.assertIn("hoodie", raw["product_types"])

    def test_the_message_is_a_sentence_and_not_a_traceback(self):
        with open(self.rc.CONFIG, "w", encoding="utf-8") as f:
            f.write("{oops")
        try:
            self.rc._read_raw()
        except self.rc.OverridesUnreadable as e:
            text = str(e)
        self.assertIn("Nothing was changed", text)
        self.assertNotIn("Traceback", text)


class AnUnknownSnsTypeDoesNotCrashTheDrain(unittest.TestCase):
    """`parse_message` promises a LIST for every "unknown" message.

    Two of its three unknown returns gave one. The third — any valid SNS type
    that is not Notification or SubscriptionConfirmation — returned a bare
    dict, so the drain loop iterated its string KEYS and called `.get()` on a
    str. The message is not deleted when the drain raises, so one
    UnsubscribeConfirmation parked in the queue could abort every later drain
    and Stream would quietly stop arriving.
    """

    def test_every_unknown_branch_returns_a_list_of_rows(self):
        import stream_drain
        bodies = [
            json.dumps({"Type": "UnsubscribeConfirmation", "MessageId": "m1",
                        "TopicArn": "arn:aws:sns:us-east-1:1:t"}),
            json.dumps({"Type": "SomethingAmazonAddedLater", "MessageId": "m2"}),
            "this is not json at all",
        ]
        for body in bodies:
            kind, data = stream_drain.parse_message(body, "sp-traffic", "NA")
            with self.subTest(body=body[:40]):
                self.assertEqual("unknown", kind)
                self.assertIsInstance(data, list)
                self.assertTrue(all(isinstance(row, dict) for row in data))
                # the shape the drain loop actually uses
                self.assertTrue(all(row.get("dataset") == "unknown" for row in data))

    def test_the_drain_loops_shape_survives_it(self):
        import stream_drain
        _, rows = stream_drain.parse_message(
            json.dumps({"Type": "UnsubscribeConfirmation"}), "sp-traffic", "NA")
        # This is the line that used to raise AttributeError on a str.
        self.assertEqual([None], [row.get("nothing_here") for row in rows])


class TheHourlyDrainWrapperReportsItsExitCode(unittest.TestCase):
    """A shell group exits with its LAST command.

    `{ echo hdr; python drain.py; echo; }` therefore always exited 0, so an SQS
    failure, a database failure, a parser crash or expired credentials were all
    logged and then reported to launchd as success.
    """

    SCRIPT = os.path.join(HERE, "run_stream_drain.sh")

    def _run(self, drain_exit):
        """Run the real wrapper with a stub interpreter that exits `drain_exit`.

        This used to be `assertIn("drain_rc=$?", text)`, which is satisfied by
        the characters being somewhere in the file and says nothing about what
        launchd is handed. The stub also has to answer the `import requests`
        probe, or the wrapper decides it is unusable and falls back to the real
        python3 — and then the test measures nothing.

        AWS is never reached: the stub is what stands in for the drain. The
        data folder is a throwaway, so the log lands there and not in the
        operator's outputs/.
        """
        d = tempfile.mkdtemp(prefix="draincode-")
        py = os.path.join(d, "python3")
        with open(py, "w") as f:
            f.write("#!/bin/bash\n"
                    'if [ "$1" = "-c" ]; then exit 0; fi\n'   # the requests probe
                    'echo "stub drain ran"\n'
                    f"exit {drain_exit}\n")
        os.chmod(py, os.stat(py).st_mode | stat.S_IEXEC)
        env = dict(os.environ, ADS_PYTHON=py, MERCHADS_DATA_DIR=d)
        r = subprocess.run(["bash", self.SCRIPT], capture_output=True, text=True,
                           env=env, cwd=HERE, timeout=120)
        log = os.path.join(d, "outputs", "stream_drain.log")
        text = ""
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                text = f.read()
        return r.returncode, text

    def test_a_failed_drain_reaches_launchd(self):
        code, log = self._run(drain_exit=3)
        self.assertEqual(3, code,
                         "the drain failed and the wrapper reported success")
        self.assertIn("stub drain ran", log,
                      "the stub was not the interpreter that ran — the wrapper "
                      "fell back and this measured nothing")

    def test_a_clean_drain_still_reports_success(self):
        """The other direction. A wrapper that always exits non-zero would
        satisfy the test above and alarm launchd every hour instead."""
        code, log = self._run(drain_exit=0)
        self.assertEqual(0, code)
        self.assertIn("stub drain ran", log)

    def test_the_separator_is_still_written_after_the_drain(self):
        """The code has to be SAVED before the trailing echo, not after it.

        Dropping the echo would also make the exit code right, and would lose
        the blank line that separates one hour from the next in the log.
        """
        _code, log = self._run(drain_exit=3)
        self.assertTrue(log.startswith("==="), f"no header in the log:\n{log}")
        self.assertTrue(log.endswith("stub drain ran\n\n"),
                        f"the separator did not follow the drain:\n{log!r}")

    def test_the_old_shape_really_did_swallow_a_failure(self):
        old = subprocess.run(["bash", "-c", '{ echo h; false; echo; } >/dev/null; echo $?'],
                             capture_output=True, text=True)
        new = subprocess.run(["bash", "-c",
                              '{ echo h; false; rc=$?; echo; } >/dev/null; echo $rc'],
                             capture_output=True, text=True)
        self.assertEqual("0", old.stdout.strip(), "the old shape should hide the failure")
        self.assertEqual("1", new.stdout.strip(), "the new shape must report it")


class TheCatalogueFolderComesFromPathsOnly(unittest.TestCase):
    """`MERCHADS_POD_DIR` names the catalogue folder, and half the engine
    ignored it.

    Six modules computed `os.path.dirname(paths.REPO_ROOT)` instead. That is
    only ever the same folder while the variable is unset. Set it, and those
    modules read catalogue A while `products.export_signature()` — which does
    use `POD_ROOT` — banks the signature of catalogue B. The economics gate
    then certifies a catalogue that was never mapped, and unattended bid, pause
    and negative writes run on it.
    """

    READERS = ["map_products.py", "demand_feed.py", "scavenger_build.py",
               "traz.py", "export_paused_asins.py"]

    def test_no_catalogue_reader_re_derives_the_pod_folder(self):
        offenders = []
        for name in self.READERS:
            path = os.path.join(ENGINE, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                # os.path.dirname(HERE) / os.path.dirname(paths.REPO_ROOT)
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "dirname"):
                    continue
                src = ast.unparse(node)
                if "HERE" in src or "REPO_ROOT" in src:
                    offenders.append(f"{name}:{node.lineno}  {src}")
        self.assertEqual([], offenders,
                         "the catalogue folder is paths.POD_ROOT, never a "
                         "dirname of the data folder:\n  " + "\n  ".join(offenders))

    def test_they_all_actually_name_pod_root(self):
        for name in self.READERS:
            with self.subTest(module=name):
                with open(os.path.join(ENGINE, name), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("paths.POD_ROOT", text)


class TheNightlyRefusesToRunTwice(unittest.TestCase):
    """Two nightlies could overlap, and the builders LIST before they CREATE.

    launchd fires at 10:00, the app has its own full-run button, and a catch-up
    can be run by hand. Nothing stopped two of them running together, so two
    runs could each see a campaign as absent and both create it.

    Only the REFUSAL path is exercised here. Letting the test acquire the lock
    would run a real nightly against a temporary folder, and that talks to
    Amazon.
    """

    SCRIPT = os.path.join(HERE, "run_scheduled.sh")

    def test_a_second_run_stops_while_the_first_holds_the_lock(self):
        data = tempfile.mkdtemp()
        lock = os.path.join(data, "outputs", "run_scheduled.lock")
        os.makedirs(lock)
        with open(os.path.join(lock, "pid"), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))       # a pid that is definitely alive

        env = dict(os.environ, MERCHADS_DATA_DIR=data)
        proc = subprocess.run(["bash", self.SCRIPT], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(75, proc.returncode,
                         f"expected the run to refuse, got {proc.returncode}\n"
                         f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}")
        self.assertIn("already in progress", proc.stderr)

    def test_the_lock_is_taken_with_mkdir_and_released_on_exit(self):
        with open(self.SCRIPT, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('mkdir "$LOCK"', text,
                      "the lock must be mkdir: a [ -f ] test then a touch is "
                      "not atomic and both runs pass it")
        self.assertIn("trap ", text)
        self.assertIn("EX_TEMPFAIL", text)


class StreamBacklogIsReportedForEveryQueue(unittest.TestCase):
    """A backlogged queue could hide behind the last one drained.

    Queues are drained one after another, and the check asked for notes at ONE
    timestamp — the single global `MAX(at)`, which is when the LAST queue
    finished. Every earlier queue's note carries a different `at` and was never
    read.

    This is the one Stream failure with no other symptom. The drain is recent,
    the message count is healthy, and the queue underneath grows until SQS drops
    the oldest messages for good.
    """

    def _backlog(self, conn, realm=None):
        """The REAL function, not a copy of its query.

        These tests used to run their own paste of the SQL, which passes whether
        or not the module still uses it — the anti-pattern this repo has been
        caught by before. `drain_backlog` was pulled out of `health()` on
        2026-08-24 so a per-market reader could ask it too, and this is what
        makes that the only copy.
        """
        import stream_store
        return stream_store.drain_backlog(conn, realm=realm) or []

    def _db(self, rows):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE stream_drain_log (
            at TEXT, realm TEXT, dataset TEXT, received INT, banked INT,
            duplicates INT, confirmations INT, note TEXT)""")
        conn.executemany("INSERT INTO stream_drain_log VALUES (?,?,?,?,?,?,?,?)", rows)
        return conn

    def test_an_earlier_queues_backlog_is_not_hidden_by_a_clean_later_one(self):
        conn = self._db([
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 500, 500, 0, 0, "queue still full"),
            ("2026-08-24T10:00:42", "NA", "sp-conversion", 3, 3, 0, 0, ""),
        ])
        try:
            last = conn.execute("SELECT MAX(at) FROM stream_drain_log").fetchone()[0]
            old = [r[0] for r in conn.execute(
                "SELECT dataset FROM stream_drain_log WHERE at = ? AND note != ''", (last,))]
            self.assertEqual([], old, "the old query should miss it — that was the bug")

            self.assertEqual(["NA/sp-traffic"], self._backlog(conn))
        finally:
            conn.close()

    def test_only_the_newest_drain_of_each_queue_counts(self):
        # Yesterday's backlog must not keep alarming after today's clean drain.
        conn = self._db([
            ("2026-08-23T10:00:01", "NA", "sp-traffic", 500, 500, 0, 0, "queue still full"),
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 40, 40, 0, 0, ""),
        ])
        try:
            self.assertEqual([], self._backlog(conn))
        finally:
            conn.close()

    def test_two_realms_are_reported_separately(self):
        conn = self._db([
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 5, 5, 0, 0, ""),
            ("2026-08-24T10:00:05", "EU", "sp-traffic", 900, 900, 0, 0, "queue still full"),
        ])
        try:
            self.assertEqual(["EU/sp-traffic"], self._backlog(conn),
                             "the realm has to be named — one queue serves five markets")
            # And a reader asking about ONE realm hears only about that realm:
            # a US day must not be called an undercount because EU is behind.
            self.assertEqual([], self._backlog(conn, realm="NA"))
        finally:
            conn.close()

    def test_the_single_timestamp_query_is_gone_for_good(self):
        """The one thing behaviour cannot show: that the OLD query is absent.

        This used to also assert `"GROUP BY realm, dataset" in text`, which was
        satisfied by an unrelated query over `stream_message` on line 145 —
        proved by mutating the backlog query to `GROUP BY realm` and watching
        the whole suite stay green. `TheBacklogQueryUnderTestIsTheModulesOwn`
        answers that half by calling `health()`.
        """
        with open(os.path.join(ENGINE, "stream_store.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("stream_drain_log WHERE at = ?", text,
                         "the single-timestamp query is what hid the backlog")


class ABackfillThatAmazonRefusedExitsNonZero(unittest.TestCase):
    """Both weekly backfills reported terminal Amazon failures as success.

    `backfill_daily` had no failure list at all and `main()` returned nothing,
    so it exited 0 even when every report FAILED. `backfill_target_daily` had
    the machinery and returned 1 on `failed or pending`, but a FAILED or
    CANCELLED report was dropped from `pending` WITHOUT ever joining `failed`.

    The Monday nightly then recorded the history backfill as passed while
    campaign-day or target-day coverage had not been refreshed at all.
    """

    def _source(self, name):
        with open(os.path.join(ENGINE, name), encoding="utf-8") as f:
            return f.read()

    def test_both_count_a_terminal_report_as_a_failure(self):
        """Read the branch itself, not a slice of text near it.

        The first version of this test took the 400 characters after the `elif`
        and searched them. That passes or fails on how long the comment above
        the code happens to be, which is not the thing being checked.
        """
        for name in ("backfill_daily.py", "backfill_target_daily.py"):
            with self.subTest(module=name):
                path = os.path.join(ENGINE, name)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)

                # The branch that DROPS the job. Both files also have a
                # branch naming these statuses to decide whether an existing
                # job can be RESUMED, and that one is fine.
                branches = []
                for node in ast.walk(tree):
                    if not isinstance(node, ast.If):
                        continue
                    test = ast.unparse(node.test)
                    if "FAILED" not in test or "CANCELLED" not in test:
                        continue
                    body = " ".join(ast.unparse(stmt) for stmt in node.body)
                    if "del pending[" in body:
                        branches.append(node)
                self.assertTrue(branches,
                                f"{name}: no branch drops a terminal report "
                                f"from pending any more — has this moved?")

                for branch in branches:
                    body = " ".join(ast.unparse(stmt) for stmt in branch.body)
                    self.assertIn("failed.append", body,
                                  f"{name}: a report Amazon refused is dropped "
                                  f"from pending without joining the failure list")

    def _run_main(self, module_name, statuses):
        """Run the REAL main() against a stubbed Amazon and a stubbed db.

        The check here used to be `assertIn("return 1 if (failed or pending)
        else 0", text)`. That is satisfied by the line existing, whatever the
        code around it does with the result, and it says nothing at all about
        whether a refused report reaches the caller. So: answer `get_report`
        with the statuses given, and read the exit code main() actually returns.

        `statuses` is consumed in order, one per poll.
        """
        import contextlib
        import importlib
        import io
        import sqlite3
        import types

        module = importlib.import_module(module_name)
        answers = list(statuses)
        stored = []
        # backfill_target_daily reports its own coverage off the connection at
        # the end, so this has to be a real (empty, in-memory) database.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE target_daily (date TEXT, target_id TEXT)")

        class _Client:
            market = "US"

            def create_report(self, *a, **k):
                return "rid-1"

            def get_report(self, rid):
                status = answers.pop(0) if answers else "PENDING"
                return status, ("https://example.invalid/r" if status == "COMPLETED"
                                else None)

            def download_gzip_json(self, url):
                return [{"date": "2026-08-20", "cost": 1.0, "sales30d": 4.0,
                         "purchases30d": 1, "impressions": 10, "clicks": 2,
                         "unitsSoldClicks30d": 1, "campaignId": "c1",
                         "campaignName": "C", "keywordId": "k1",
                         "targetId": "k1", "keywordText": "kw",
                         "matchType": "EXACT", "adGroupId": "a1"}]

        def _store(*a, **_k):
            stored.append(a)
            return 1

        stub_db = types.SimpleNamespace(
            MAX_DAILY_WINDOW_DAYS=92,
            connect=lambda *a, **k: conn,
            get_report_job=lambda *a, **k: None,
            save_report_job=lambda *a, **k: None,
            set_report_status=lambda *a, **k: None,
            log_pull=lambda *a, **k: None,
            store_daily_total=_store,
            store_campaign_daily=_store,
            store_target_daily=_store,
        )
        saved = {name: getattr(module, name)
                 for name in ("AdsClient", "db", "time")}
        buf = io.StringIO()
        try:
            module.AdsClient = lambda *a, **k: _Client()
            module.db = stub_db
            module.time = types.SimpleNamespace(sleep=lambda *_a: None)
            argv = sys.argv
            sys.argv = [module_name, "--days", "5"]
            try:
                with contextlib.redirect_stdout(buf):
                    code = module.main()
            finally:
                sys.argv = argv
        finally:
            for name, value in saved.items():
                setattr(module, name, value)
            conn.close()
        return code, buf.getvalue()

    def test_a_refused_report_reaches_the_exit_code(self):
        """The Monday nightly reads this code, and nothing else."""
        for name in ("backfill_daily", "backfill_target_daily"):
            with self.subTest(module=name):
                code, out = self._run_main(name, ["FAILED"])
                self.assertEqual(1, code,
                                 f"{name}: Amazon refused the report and "
                                 f"main() reported success:\n{out}")

    def test_a_report_that_completed_still_exits_zero(self):
        """The other direction. A backfill that always failed would pass the
        test above just as happily, and then the nightly would cry every week
        until nobody read it."""
        for name in ("backfill_daily", "backfill_target_daily"):
            with self.subTest(module=name):
                code, out = self._run_main(name, ["COMPLETED"])
                self.assertEqual(0, code, f"{name}: a clean run was reported as "
                                          f"a failure:\n{out}")

    def test_main_is_what_the_process_exits_with(self):
        for name in ("backfill_daily.py", "backfill_target_daily.py"):
            with self.subTest(module=name):
                self.assertIn("sys.exit(main())", self._source(name))

    def test_both_scripts_still_parse(self):
        for name in ("backfill_daily.py", "backfill_target_daily.py"):
            with self.subTest(module=name):
                path = os.path.join(ENGINE, name)
                with open(path, encoding="utf-8") as f:
                    ast.parse(f.read(), path)


class ACatalogueCacheNeverBanksAPartialParse(unittest.TestCase):
    """The cache could certify a catalogue it had not fully read.

    `build()` called `_catalog_rows_csv(folder)` with no `skipped` collector, so
    it never learned that a chunk had been dropped. It then banked a signature
    covering that unreadable file. `read()` matched the signature, handed back
    the partial catalogue, and `map_products` stamped a complete mapping. The
    listings in the missing chunk lose their price and royalty, and no price
    means no break-even, which means every economics rule SKIPS the design —
    not paused, not flagged, exempt. That is the one outcome worse than pricing
    it wrong.

    `catalog_files()` filters by FILENAME only, so a malformed chunk really does
    reach the reader, and `catalog_signature()` really does include it.
    """

    SNAP_HEADER = "Product Type,Marketplace,ASIN,Title,Price,Status\n"
    SNAP_ROW = "Standard T-Shirt,us,B0TEST0001,A shirt,19.99,Live\n"

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(tempfile.mkdtemp(), "catalog_cache.sqlite")

    def _write(self, name, text):
        with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
            f.write(text)

    def _signature_in_db(self):
        import sqlite3
        if not os.path.exists(self.db):
            return None
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key='signature'").fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def test_a_clean_folder_is_banked(self):
        import catalog_cache
        self._write("snap-grid-export-2026-08-20.csv", self.SNAP_HEADER + self.SNAP_ROW)
        stored = catalog_cache.build(folder=self.folder, path=self.db)
        self.assertEqual(1, stored)
        self.assertIsNotNone(self._signature_in_db())

    def test_one_unreadable_chunk_stops_the_whole_build(self):
        import catalog_cache
        self._write("snap-grid-export-2026-08-20.csv", self.SNAP_HEADER + self.SNAP_ROW)
        # Named like an export, so catalog_files() returns it and the signature
        # covers it — but the header is not a product grid, so the reader skips
        # it. This is the exact shape that used to be banked as complete.
        self._write("snap-grid-export-2026-08-19.csv", "some,other,columns\n1,2,3\n")

        stored = catalog_cache.build(folder=self.folder, path=self.db)
        self.assertEqual(0, stored, "a partial catalogue must not be banked")
        self.assertIsNone(self._signature_in_db(),
                          "a signature over an unread chunk is what made the "
                          "partial cache look complete")

    def test_the_skipped_chunk_is_in_the_signature_so_this_is_a_real_shape(self):
        import export_reader
        self._write("snap-grid-export-2026-08-20.csv", self.SNAP_HEADER + self.SNAP_ROW)
        self._write("snap-grid-export-2026-08-19.csv", "some,other,columns\n1,2,3\n")
        names = [os.path.basename(p) for p in export_reader.catalog_files(self.folder)]
        self.assertIn("snap-grid-export-2026-08-19.csv", names,
                      "catalog_files filters by filename, so the bad chunk is "
                      "returned and the signature covers it")


class AReportAmazonWouldNotCreateReachesTheExitCode(unittest.TestCase):
    """Phase 0 printed "Done" and exited 0 after Amazon refused a report.

    `ensure_report_jobs` printed `CREATE FAILED` and then forgot: the key never
    reached `active`, so `poll_and_store` never saw it, and it never joined the
    failed set that decides the exit code. The nightly's step tracker recorded
    phase 0 as healthy while the tables that report fills had no new data.

    Also pinned here: the recover step used to promise "retrying tomorrow" for a
    report Amazon had not finished. `save_report_job` conflicts on report_type
    alone, so requesting today's window REPLACES that row and nothing is left to
    retry. Measured across all seven databases on 2026-08-24 this is not
    currently happening — every nightly report is COMPLETED and downloaded — but
    the promise was still false, and the abandonment is now printed where the
    decision is made.
    """

    def _tree(self):
        path = os.path.join(ENGINE, "phase0_pull.py")
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read(), path), f

    def test_ensure_report_jobs_returns_what_it_could_not_create(self):
        tree, _ = self._tree()
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "ensure_report_jobs")
        body = " ".join(ast.unparse(stmt) for stmt in fn.body)
        self.assertIn("create_failed.append(key)", body,
                      "a refused report request must be recorded")
        returns = [ast.unparse(n.value) for n in ast.walk(fn)
                   if isinstance(n, ast.Return) and n.value is not None]
        self.assertIn("(active, create_failed)", returns,
                      "the caller cannot see a creation failure otherwise")

    def test_the_exit_code_includes_creation_failures(self):
        path = os.path.join(ENGINE, "phase0_pull.py")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("if structure_failed or failed or create_failed:", text)
        self.assertIn("NOT REQUESTED", text,
                      "the operator has to be told which reports were refused")

    def test_the_recover_step_no_longer_promises_a_retry_it_cannot_make(self):
        """Read what the code PRINTS, not what the file contains.

        The first version searched the whole file and failed on the comment
        that explains the fix. This repo has done that before — a release check
        that failed on its own explanation — so the rule is: judge the syntax
        tree, never the prose around it.
        """
        tree, _ = self._tree()
        printed = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                printed.append(" ".join(ast.unparse(a) for a in node.args))
        joined = " ".join(printed)
        self.assertNotIn("retrying tomorrow", joined,
                         "the row is replaced by today's request, so there is "
                         "nothing left to retry tomorrow")
        self.assertIn("ABANDONING report", joined,
                      "the operator has to be told a report was given up on")

    def test_every_caller_unpacks_the_new_pair(self):
        """A tuple return that one caller still reads as a dict is a fresh bug."""
        tree, _ = self._tree()
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "ensure_report_jobs"]
        self.assertEqual(1, len(calls), "a new caller needs the pair too")
        path = os.path.join(ENGINE, "phase0_pull.py")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("active, create_failed = ensure_report_jobs(", text)


class KeywordRecommendationsSpeakTheMarketsLanguage(unittest.TestCase):
    """Every market asked Amazon for en_US keywords.

    `get_keyword_recommendations` hardcoded `"locale": "en_US"` while the
    scavenger builder runs nightly in UK, DE, FR, ES and IT. Those campaigns
    were offered English discovery keywords for German, French, Spanish and
    Italian marketplaces, and the DE, ES and IT snapshots hold English broad
    scavenger targets to show for it.

    The retry matters as much as the fix. Returning nothing on a rejected locale
    is not neutral: the caller falls back to title keywords, so a wrong-language
    set would be replaced by a thinner one with no explanation.
    """

    def test_every_market_declares_a_locale(self):
        import markets
        for code in markets.MARKETS:
            with self.subTest(market=code):
                self.assertIn("locale", markets.cfg(code))

    def test_the_locales_match_their_marketplaces(self):
        import markets
        expected = {"US": "en_US", "UK": "en_GB", "DE": "de_DE", "FR": "fr_FR",
                    "ES": "es_ES", "IT": "it_IT", "USKDP": "en_US"}
        got = {c: markets.cfg(c)["locale"] for c in markets.MARKETS}
        self.assertEqual(expected, got)

    def test_the_request_no_longer_hardcodes_one_locale(self):
        import ads_client, inspect
        src = inspect.getsource(ads_client.AdsClient.get_keyword_recommendations)
        body = src.split('body = {')[1].split('}')[0]
        self.assertIn('"locale": locale', body,
                      "the request body must take the market's locale")
        self.assertNotIn('"locale": "en_US"', body)

    def test_a_rejected_locale_is_retried_as_en_us(self):
        import ads_client, inspect
        src = inspect.getsource(ads_client.AdsClient.get_keyword_recommendations)
        self.assertIn('resp.status_code == 400 and locale != "en_US"', src,
                      "a 400 on the market locale must fall back, not return "
                      "an empty list the caller reads as 'no suggestions'")


class TheWriteCapCountsOnlyWhatWasSent(unittest.TestCase):
    """The runaway guard claimed writes it had refused.

    `_budget` added `n` and THEN compared, so a 600-entity request that stopped
    after 500 announced "has written 600 entities". That is a claim about the
    live account, made by the one message an operator reads when something has
    gone wrong.

    The deeper problem is named in the message rather than fixed here. A public
    write method chunks into batches of 100 and collects each batch's result;
    the SystemExit unwinds past that method, so the results for the batches that
    DID land never reach the caller and never reach `writes_log` or the local
    mirror. Fixing that properly means restructuring all seventeen batched write
    methods, which is not a change to make inside an audit — so the stop now
    says plainly that the earlier writes are on the account and the audit trail
    may not have them, and `cap_stopped` carries the numbers.
    """

    def _client(self, cap=500):
        import ads_client
        c = ads_client.AdsClient.__new__(ads_client.AdsClient)
        c._auto, c._written, c._write_cap = True, 0, cap
        c.market, c.cap_stopped = "US", None
        return c

    def test_a_refused_batch_is_not_counted_as_written(self):
        c = self._client()
        for _ in range(5):
            c._budget(100)
        self.assertEqual(500, c._written)
        with self.assertRaises(SystemExit):
            c._budget(100)
        self.assertEqual(500, c._written,
                         "the refused batch was counted as written")

    def test_the_message_names_what_actually_landed(self):
        c = self._client()
        for _ in range(5):
            c._budget(100)
        try:
            c._budget(100)
        except SystemExit as e:
            text = str(e)
        self.assertIn("has written 500 entities", text)
        self.assertIn("next batch of 100", text)
        self.assertIn("That batch was NOT sent", text)

    def test_the_message_admits_the_audit_trail_may_be_incomplete(self):
        c = self._client()
        for _ in range(5):
            c._budget(100)
        try:
            c._budget(100)
        except SystemExit as e:
            text = str(e)
        self.assertIn("ON THE ACCOUNT", text)
        self.assertIn("writes_log", text)

    def test_cap_stopped_carries_the_numbers(self):
        c = self._client()
        c._budget(400)
        with self.assertRaises(SystemExit):
            c._budget(200)
        self.assertEqual({"written": 400, "cap": 500, "refused": 200,
                          "market": "US", "surface": "change"}, c.cap_stopped)

    def test_exactly_at_the_cap_is_allowed(self):
        c = self._client()
        c._budget(500)          # 500 is the limit, not one less
        self.assertEqual(500, c._written)
        self.assertIsNone(c.cap_stopped)

    def test_a_manual_run_is_never_capped(self):
        c = self._client()
        c._auto = False
        c._budget(10_000)
        self.assertEqual(0, c._written)


class TheEconomicsGateJudgesEveryMarket(unittest.TestCase):
    """Six of seven markets auto-applied with no economics gate at all.

    `run_scheduled.sh` ran the gate inside `if [ "$M" = "US" ]`, so UK, DE, FR,
    ES, IT and USKDP kept auto-applying whatever the state of their economics —
    while `appctl econ-gate` is market-aware and `derive_econ` runs for each of
    them a few lines above.

    And the check underneath it could not fail. `products._derived_econ_reasons`
    returned an empty list — which reads as "no problem" and is what OPENS the
    gate — for a database it could not open and for a query it could not run. A
    locked or corrupt database waved every economics-driven write through on
    whatever the fallback tables happened to say.

    Verified before changing it: the gate is open in all seven markets, so this
    refuses nothing today.
    """

    def test_the_nightly_gates_every_market_not_just_us(self):
        with open(os.path.join(HERE, "run_scheduled.sh"), encoding="utf-8") as f:
            text = f.read()
        # The CALL, not the echo that mentions it: a line that pipes the reply
        # into python to read `data.ok`.
        gate_line = [ln for ln in text.splitlines()
                     if "appctl.py econ-gate" in ln
                     and "ECON_OK=0" in ln
                     and not ln.strip().startswith("#")]
        self.assertEqual(1, len(gate_line), "expected exactly one gate call")
        # The call must not sit inside a US-only branch.
        idx = text.index(gate_line[0])
        before = text[:idx].rsplit("ECON_OK=1", 1)[-1]
        self.assertNotIn('[ "$M" = "US" ]', before,
                         "the gate is still behind a US-only branch")

    def test_an_unreadable_database_blocks_instead_of_reading_as_fine(self):
        import sqlite3
        import products

        class Locked:
            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")

        reasons = products._derived_econ_reasons("DE", conn=Locked())
        self.assertTrue(reasons, "a locked database read as 'no problem'")
        self.assertIn("could not read", reasons[0])

    def test_a_missing_table_is_still_benign(self):
        """Pre-migration databases price from the shipped tables. That is a real
        state and must not close the gate, or every old install refuses."""
        import sqlite3
        import products

        class NoTable:
            def execute(self, *a):
                raise sqlite3.OperationalError("no such table: market_econ")

        self.assertEqual([], products._derived_econ_reasons("DE", conn=NoTable()))

    def test_an_unexpected_error_also_blocks(self):
        import products

        class Weird:
            def execute(self, *a):
                raise RuntimeError("something nobody predicted")

        reasons = products._derived_econ_reasons("DE", conn=Weird())
        self.assertTrue(reasons)
        self.assertIn("RuntimeError", reasons[0])


class TheApprovalQueueCannotApplyYesterdaysEvidence(unittest.TestCase):
    """`negatives-apply` sent captured ids straight to Amazon.

    The approval queue is a screen an operator can leave open. Those ids were
    resolved against ONE snapshot, and every other apply path in this engine
    re-resolves against fresh state before it writes — `everywhere-apply` and
    `harvest-prune-apply` both do. A nightly pull landing in between meant
    negating a search term, or pausing an ad group, that had since earned its
    keep.

    Rebuilding the plan at apply time would be the WRONG fix: it would silently
    apply a different set from the one the operator read and approved. The
    evidence DATE is compared instead, and a moved snapshot refuses.
    """

    def test_the_engine_compares_the_approved_snapshot(self):
        """Both dates, each from the table its half of the plan was read from.

        Negatives come from `search_term_perf` and pauses from
        `targeting_perf`, and those two drift apart. Comparing one half against
        the other's date refused every apply in one direction of the drift and
        skipped the pause check in the other."""
        path = os.path.join(ENGINE, "appctl.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "cmd_negatives_apply")
        body = " ".join(ast.unparse(s) for s in fn.body)
        for table in ("search_term_perf", "targeting_perf"):
            self.assertIn(f"latest_snapshot(conn, '{table}')", body,
                          f"each half's date must come from its own table; "
                          f"{table} is not resolved here")

    def test_each_half_is_judged_against_its_own_table(self):
        """The decision itself, not the source text around it."""
        import appctl
        fresh, stale = "2026-08-23", "2026-08-20"

        def judge(plan, has_negatives, has_pauses, st, tg):
            current = min([d for d in (st, tg) if d], default=None)
            return appctl._stale_evidence(
                appctl._evidence_checks(plan, has_negatives, has_pauses,
                                        st, tg, current))

        # Targeting is behind, so the preview's own `as_of` is the OLDER date.
        # Both halves are current against their own tables, so nothing may be
        # refused — this is the deadlock the one-table comparison created.
        plan = {"as_of": stale, "as_of_search_terms": fresh,
                "as_of_targeting": stale}
        self.assertIsNone(judge(plan, True, True, fresh, stale))
        # …and a targeting snapshot that really did move refuses the pauses.
        self.assertIsNotNone(judge(plan, True, True, fresh, fresh))

    def test_it_refuses_rather_than_rebuilding(self):
        import appctl, inspect
        self.assertIn("Nothing", inspect.getsource(appctl._stale_evidence))
        src = inspect.getsource(appctl.cmd_negatives_apply)
        self.assertNotIn("phase2_apply.candidates", src,
                         "re-running candidates would apply a different set "
                         "from the one the operator approved")

    def test_an_older_app_that_sends_no_as_of_still_works(self):
        """Refusing every client that predates the field would be its own
        outage. Ask the real helpers, not the text around them: `assertIn(
        "as_of_checked", src)` was satisfied by the name appearing anywhere,
        including in a comment explaining it."""
        import appctl, inspect
        # No dates at all: nothing can be compared, so nothing may be refused.
        self.assertEqual([], appctl._evidence_checks(
            {}, True, True, "2026-08-23", "2026-08-23", "2026-08-23"))
        self.assertIsNone(appctl._stale_evidence([]))
        # Only the old single `as_of`: compared the way the preview built it,
        # against the OLDER of the two current dates.
        plan = {"as_of": "2026-08-20"}
        checks = appctl._evidence_checks(plan, True, True, "2026-08-23",
                                         "2026-08-20", "2026-08-20")
        self.assertEqual(1, len(checks))
        self.assertIsNone(appctl._stale_evidence(checks),
                          "an older app's plan still matches and must apply")
        moved = appctl._evidence_checks(plan, True, True, "2026-08-23",
                                        "2026-08-23", "2026-08-23")
        self.assertIsNotNone(appctl._stale_evidence(moved))
        # And the reply has to say which of the two happened.
        self.assertIn("as_of_checked",
                      inspect.getsource(appctl.cmd_negatives_apply))

    def test_the_app_sends_the_snapshot_it_approved_against(self):
        path = os.path.join(HERE, "MerchAds", "Views", "Actions", "ApprovalsView.swift")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('plan["as_of"] = asOf', text,
                      "the engine's check is inert unless the app sends it")


class TheAppNeverWritesIntoItsOwnSignedBundle(unittest.TestCase):
    """The app invalidated its own code signature on first use.

    `cp -R` does not preserve mtimes, so after an install every engine `.py` in
    the bundle looks NEWER than the `.pyc` shipped beside it. CPython then
    rewrites those `.pyc` — inside `Contents/Resources`, which the code
    signature seals — and from the first command the app runs,
    `codesign --verify --deep --strict` fails on the installed copy.

    Seen on 2026-08-24: `package_app.sh --install` reported "installed copy
    failed signature verify" twice, and `codesign --verify --verbose=4` named
    the modified files as `python/lib/python3.12/encodings/__pycache__/*.pyc`.

    Two guards, because either alone would do and both are cheap: the app tells
    the interpreter not to write bytecode at all, and the installer preserves
    mtimes so the shipped bytecode stays valid and nothing pays to re-parse it.

    An orphaned worker made it worse — `pkill -x "Merch Ads"` stops the app but
    not the python processes it spawned, and one was still running from the
    bundle after the app was gone.
    """

    def test_the_bridge_disables_bytecode_writing(self):
        path = os.path.join(HERE, "MerchAds", "Bridge", "PythonBridge.swift")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('environment["PYTHONDONTWRITEBYTECODE"] = "1"', text)

    def test_both_bundled_shell_runners_disable_it_too(self):
        for name in ("run_scheduled.sh", "run_stream_drain.sh"):
            with self.subTest(script=name):
                with open(os.path.join(HERE, name), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("export PYTHONDONTWRITEBYTECODE=1", text,
                              f"{name} ships inside the bundle and runs the "
                              f"bundled interpreter")

    def test_the_installer_preserves_mtimes(self):
        with open(os.path.join(HERE, "scripts", "package_app.sh"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn('/bin/cp -Rp "${APP_DST}" "${DEST}"', text)
        self.assertNotIn('/bin/cp -R "${APP_DST}" "${DEST}"', text,
                         "a copy without -p makes every shipped .pyc look stale")

    def test_the_installer_still_verifies_the_signature(self):
        """The check that caught this must not be softened to make it pass."""
        with open(os.path.join(HERE, "scripts", "package_app.sh"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn("codesign --verify --deep --strict", text)
        self.assertIn("installed copy failed signature verify", text)


class TheBacklogQueryUnderTestIsTheModulesOwn(unittest.TestCase):
    """The three tests above run a COPY of the query. This runs the real one.

    Found by mutation on 2026-08-24. Changing the module's inner query from
    `GROUP BY realm, dataset` to `GROUP BY realm` — which restores the exact
    failure the class above was written for, one queue's backlog hidden behind
    another's clean drain — broke nothing in the whole suite.

    Both halves of the guard missed it, for different reasons:

      * `NEW` is a hand-copied SQL string. The tests that use it prove the
        QUERY is right, and go on proving it after the module stops using it.
      * `test_the_module_uses_the_per_queue_query` asserts the source contains
        "GROUP BY realm, dataset" — and it still did, on line 145, in an
        unrelated query over `stream_message`. A string-presence assertion is
        satisfied by any occurrence, including one the fix never touched.

    So this calls `health()` and reads `drain_backlog` off the reply.
    """

    @staticmethod
    def _env():
        """health() returns before the backlog query unless a queue is
        configured, so the NA queues are named here."""
        import stream_config as sc
        return {sc.env_key("NA", d):
                f"https://sqs.us-east-1.amazonaws.com/123456789012/mc-{d}"
                for d in (sc.TRAFFIC, sc.CONVERSION)}

    def _health_with(self, rows):
        import sqlite3
        import stream_store
        conn = sqlite3.connect(":memory:")
        conn.executescript(stream_store.SCHEMA)
        conn.executemany(
            "INSERT INTO stream_drain_log (at, realm, dataset, received, banked,"
            " duplicates, confirmations, note) VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        real = stream_store.connect
        try:
            stream_store.connect = lambda *a, **k: conn
            return stream_store.health(env=self._env())
        finally:
            stream_store.connect = real
            conn.close()

    def test_an_earlier_queues_backlog_survives_a_later_clean_drain(self):
        info = self._health_with([
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 500, 500, 0, 0,
             "queue still full"),
            ("2026-08-24T10:00:42", "NA", "sp-conversion", 3, 3, 0, 0, ""),
        ])
        self.assertEqual(info.get("drain_backlog"), ["NA/sp-traffic"],
                         "the backlogged queue must reach the reply, not just "
                         "a query the test wrote for itself")

    def test_a_clean_account_reports_no_backlog(self):
        """The other direction, so a query that flags everything cannot pass."""
        info = self._health_with([
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 5, 5, 0, 0, ""),
            ("2026-08-24T10:00:42", "NA", "sp-conversion", 3, 3, 0, 0, ""),
        ])
        self.assertIsNone(info.get("drain_backlog"))

    def test_yesterdays_backlog_stops_alarming_after_a_clean_drain(self):
        info = self._health_with([
            ("2026-08-23T10:00:01", "NA", "sp-traffic", 500, 500, 0, 0,
             "queue still full"),
            ("2026-08-24T10:00:01", "NA", "sp-traffic", 40, 40, 0, 0, ""),
        ])
        self.assertIsNone(info.get("drain_backlog"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
