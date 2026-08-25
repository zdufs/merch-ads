#!/usr/bin/env python3
"""Unit tests for the Amazon Marketing Stream path.
Run from the Ads folder:  python3 -m unittest tests.stream_tests -v
No AWS, no Amazon API, no production database — vectors and temp files only.
"""

import datetime
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _no_operator_data  # noqa: F401,E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

import aws_sigv4                    # noqa: E402
import stream_config                # noqa: E402
import stream_drain                 # noqa: E402
import stream_store                 # noqa: E402


class SigV4Vector(unittest.TestCase):
    """AWS's own published "get-vanilla" case from the SigV4 test suite.

    This is the only thing standing between a hand-rolled signer and a day spent
    reading `SignatureDoesNotMatch`. It pins all three intermediate strings, so a
    failure says WHICH step drifted rather than just "wrong".
    """

    KEY = "AKIDEXAMPLE"
    SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    WHEN = datetime.datetime(2015, 8, 30, 12, 36, 0, tzinfo=datetime.timezone.utc)

    def test_get_vanilla(self):
        creq, sts, sig = aws_sigv4.parts_for_test(
            "GET", "https://example.amazonaws.com/", "us-east-1", "service",
            self.KEY, self.SECRET, now=self.WHEN)
        self.assertEqual(creq,
                         "GET\n/\n\n"
                         "host:example.amazonaws.com\n"
                         "x-amz-date:20150830T123600Z\n\n"
                         "host;x-amz-date\n"
                         + aws_sigv4.EMPTY_PAYLOAD_HASH)
        self.assertTrue(sts.startswith("AWS4-HMAC-SHA256\n20150830T123600Z\n"
                                       "20150830/us-east-1/service/aws4_request\n"))
        self.assertEqual(
            sig, "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")

    def test_authorization_header_shape(self):
        headers = aws_sigv4.sign(
            "GET", "https://example.amazonaws.com/", "us-east-1", "service",
            self.KEY, self.SECRET, now=self.WHEN)
        self.assertIn("Authorization", headers)
        self.assertIn("Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request",
                      headers["Authorization"])
        self.assertIn("SignedHeaders=host;x-amz-date", headers["Authorization"])
        # The secret must never travel in a header.
        self.assertNotIn(self.SECRET, json.dumps(headers))

    def test_signed_headers_carry_the_values_that_get_sent(self):
        """A header signed with one value and sent with another is rejected."""
        headers = aws_sigv4.sign(
            "POST", "https://sqs.us-east-1.amazonaws.com/", "us-east-1", "sqs",
            self.KEY, self.SECRET, body="{}",
            headers={"Content-Type": "  application/x-amz-json-1.0  ",
                     "X-Amz-Target": "AmazonSQS.ReceiveMessage"}, now=self.WHEN)
        self.assertEqual(headers["content-type"], "application/x-amz-json-1.0")
        self.assertIn("content-type", headers["Authorization"])

    def test_canonical_query_sorts_on_encoded_pairs(self):
        self.assertEqual(aws_sigv4.canonical_query("b=2&a=1"), "a=1&b=2")
        self.assertEqual(aws_sigv4.canonical_query("k=a/b"), "k=a%2Fb")
        self.assertEqual(aws_sigv4.canonical_query(""), "")


class ConfigMapping(unittest.TestCase):
    def test_realm_follows_the_market_endpoint(self):
        import markets
        self.assertEqual(
            stream_config.realm_for_endpoint(markets.MARKETS["US"]["endpoint"]), "NA")
        self.assertEqual(
            stream_config.realm_for_endpoint(markets.MARKETS["USKDP"]["endpoint"]), "NA")
        for m in ("UK", "DE", "FR", "ES", "IT"):
            self.assertEqual(
                stream_config.realm_for_endpoint(markets.MARKETS[m]["endpoint"]), "EU",
                f"{m} must resolve to the EU realm")

    def test_queue_url_parses_into_an_arn(self):
        info = stream_config.parse_queue_url(
            "https://sqs.eu-west-1.amazonaws.com/210987654321/merchads-sp-conversion-eu")
        self.assertEqual(info["region"], "eu-west-1")
        self.assertEqual(info["account"], "210987654321")
        self.assertEqual(info["arn"],
                         "arn:aws:sqs:eu-west-1:210987654321:merchads-sp-conversion-eu")

    def test_a_non_queue_url_is_refused(self):
        for bad in ("", "arn:aws:sqs:us-east-1:123456789012:q",
                    "https://sqs.us-east-1.amazonaws.com/12345/q",
                    "http://sqs.us-east-1.amazonaws.com/123456789012/q"):
            with self.assertRaises(ValueError):
                stream_config.parse_queue_url(bad)

    def test_each_dataset_gets_its_own_publisher_account(self):
        """The silent-failure trap: one policy reused for both datasets means
        every sp-conversion message is dropped without a word."""
        arn = "arn:aws:sqs:us-east-1:123456789012:q"
        traffic = stream_config.queue_policy(arn, "NA", stream_config.TRAFFIC)
        conversion = stream_config.queue_policy(arn, "NA", stream_config.CONVERSION)
        t_src = traffic["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"]
        c_src = conversion["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"]
        self.assertNotEqual(t_src, c_src)
        self.assertIn("906013806264", t_src)
        self.assertIn("802324068763", c_src)

    def test_policy_grants_the_reviewer_role(self):
        """Amazon validates the queue before activating a subscription. Without
        this grant the create is rejected for a reason nobody guesses."""
        policy = stream_config.queue_policy(
            "arn:aws:sqs:us-east-1:123456789012:q", "NA", stream_config.TRAFFIC)
        sids = {s["Sid"] for s in policy["Statement"]}
        self.assertIn("AllowStreamReviewerGetQueueAttributes", sids)

    def test_env_key_naming(self):
        self.assertEqual(stream_config.env_key("NA", "sp-traffic"),
                         "STREAM_QUEUE_NA_SP_TRAFFIC")
        self.assertEqual(stream_config.env_key("EU", "sp-conversion"),
                         "STREAM_QUEUE_EU_SP_CONVERSION")

    def test_aws_keys_absent_is_none_not_an_exception(self):
        self.assertIsNone(stream_config.aws_keys({}))
        self.assertIsNone(stream_config.aws_keys({"AWS_ACCESS_KEY_ID": "x"}))
        self.assertEqual(
            stream_config.aws_keys({"AWS_ACCESS_KEY_ID": "x",
                                    "AWS_SECRET_ACCESS_KEY": "y"}), ("x", "y"))

    def test_configured_queues_reads_env(self):
        env = {"STREAM_QUEUE_NA_SP_TRAFFIC":
               "https://sqs.us-east-1.amazonaws.com/123456789012/t"}
        found = stream_drain.configured_queues(env)
        self.assertEqual(len(found), 1)
        realm, dataset, url, region = found[0]
        self.assertEqual((realm, dataset, region), ("NA", "sp-traffic", "us-east-1"))
        self.assertEqual(stream_drain.configured_queues(env, "EU"), [])


class MessageParsing(unittest.TestCase):
    def test_subscription_confirmation_is_recognised(self):
        """Miss this and the queue stays empty forever while everything reports
        healthy — the single most expensive way for Stream to fail."""
        body = json.dumps({
            "Type": "SubscriptionConfirmation",
            "MessageId": "abc",
            "Token": "tok-123",
            "TopicArn": "arn:aws:sns:us-east-1:906013806264:sp-traffic",
            "Message": "You have chosen to subscribe...",
        })
        kind, data = stream_drain.parse_message(body, "sp-traffic", "NA")
        self.assertEqual(kind, "confirm")
        self.assertEqual(data["token"], "tok-123")
        self.assertEqual(data["topic_arn"],
                         "arn:aws:sns:us-east-1:906013806264:sp-traffic")

    def test_notification_keeps_the_payload_whole(self):
        inner = {"time_window_start": "2026-08-21T09:00:00Z", "profile_id": "555",
                 "campaign_id": "1", "clicks": 3, "cost": 1.5, "unexpected": "keep me"}
        body = json.dumps({
            "Type": "Notification", "MessageId": "m-1",
            "TopicArn": "arn:aws:sns:us-east-1:906013806264:sp-traffic",
            "Timestamp": "2026-08-21T10:03:00.000Z",
            "Message": json.dumps(inner)})
        kind, rows = stream_drain.parse_message(body, "sp-traffic", "NA")
        self.assertEqual(kind, "notification")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["message_id"], "m-1")
        self.assertEqual(row["time_window_start"], "2026-08-21T09:00:00Z")
        self.assertEqual(row["profile_id"], "555")
        self.assertEqual(json.loads(row["payload"]), inner)

    def test_camel_case_field_names_are_found_too(self):
        body = json.dumps({
            "Type": "Notification", "MessageId": "m-2", "TopicArn": "t",
            "Message": json.dumps({"timeWindowStart": "2026-08-21T09:00:00Z",
                                   "profileId": "777"})})
        _, rows = stream_drain.parse_message(body, "sp-traffic", "NA")
        row = rows[0]
        self.assertEqual(row["time_window_start"], "2026-08-21T09:00:00Z")
        self.assertEqual(row["profile_id"], "777")

    def test_raw_delivery_body_is_the_payload_itself(self):
        """If SNS raw delivery is ever on, there is no envelope. Assuming one
        would bank an empty payload for every row of an hour we cannot re-fetch."""
        inner = {"time_window_start": "2026-08-21T09:00:00Z", "clicks": 1}
        kind, rows = stream_drain.parse_message(json.dumps(inner), "sp-traffic", "NA")
        self.assertEqual(kind, "notification")
        self.assertEqual(json.loads(rows[0]["payload"]), inner)

    def test_unparseable_body_is_kept_not_dropped(self):
        kind, rows = stream_drain.parse_message("<html>oops</html>", "sp-traffic", "NA")
        self.assertEqual(kind, "unknown")
        self.assertEqual(rows[0]["payload"], "<html>oops</html>")

    def test_a_batched_payload_becomes_one_row_per_record(self):
        """The array used to be banked WHOLE, identified by its first record.
        Every downstream query reads json_extract(payload,'$.advertiser_id') and
        friends, which is NULL for an array — so the message counted towards the
        banked total and towards Stream health while both records contributed
        nothing to any figure on the panel. An undercount that stays internally
        consistent is the one failure this pipeline exists to refuse."""
        inner = [{"time_window_start": "2026-08-21T09:00:00Z", "clicks": 1},
                 {"time_window_start": "2026-08-21T09:00:00Z", "clicks": 2}]
        body = json.dumps({"Type": "Notification", "MessageId": "m-3",
                           "TopicArn": "t", "Message": json.dumps(inner)})
        _, rows = stream_drain.parse_message(body, "sp-traffic", "NA")
        self.assertEqual(len(rows), 2)
        self.assertEqual([json.loads(r["payload"])["clicks"] for r in rows], [1, 2])
        for r in rows:
            self.assertIsInstance(json.loads(r["payload"]), dict)
            self.assertEqual(r["time_window_start"], "2026-08-21T09:00:00Z")
        self.assertEqual(len({r["message_id"] for r in rows}), 2,
                         "both records share one primary key, so one overwrites "
                         "the other on insert")

    def test_a_single_record_array_keeps_the_plain_message_id(self):
        """A list of one is the common shape if Amazon ever wraps, and it must
        not start suffixing ids that dedupe was already working on."""
        body = json.dumps({"Type": "Notification", "MessageId": "m-4",
                           "TopicArn": "t",
                           "Message": json.dumps([{"clicks": 1}])})
        _, rows = stream_drain.parse_message(body, "sp-traffic", "NA")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message_id"], "m-4")
        self.assertEqual(json.loads(rows[0]["payload"]), {"clicks": 1})


class Store(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.unlink(self.path)
        self._real = stream_store.db_path
        stream_store.db_path = lambda: self.path

    def tearDown(self):
        stream_store.db_path = self._real
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _row(self, mid, window="2026-08-21T09:00:00Z"):
        return {"message_id": mid, "dataset": "sp-traffic", "realm": "NA",
                "topic_arn": "t", "published_at": "2026-08-21T10:00:00Z",
                "time_window_start": window, "profile_id": "555",
                "payload": json.dumps({"clicks": 1, "cost": 0.5})}

    def test_redelivery_is_free(self):
        """SQS is at-least-once. The same message WILL arrive twice."""
        conn = stream_store.connect()
        banked, dupes = stream_store.store_messages(conn, [self._row("a"), self._row("b")])
        self.assertEqual((banked, dupes), (2, 0))
        banked, dupes = stream_store.store_messages(conn, [self._row("b"), self._row("c")])
        self.assertEqual((banked, dupes), (1, 1))
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM stream_message").fetchone()[0], 3)
        conn.close()

    def test_coverage_reports_the_hours_held(self):
        conn = stream_store.connect()
        stream_store.store_messages(conn, [
            self._row("a", "2026-08-21T09:00:00Z"),
            self._row("b", "2026-08-21T11:00:00Z")])
        cov = stream_store.coverage(conn)
        self.assertEqual(len(cov), 1)
        self.assertEqual(cov[0]["messages"], 2)
        self.assertEqual(cov[0]["first_window"], "2026-08-21T09:00:00Z")
        self.assertEqual(cov[0]["last_window"], "2026-08-21T11:00:00Z")
        conn.close()

    def test_field_census_counts_real_keys(self):
        conn = stream_store.connect()
        stream_store.store_messages(conn, [self._row("a"), self._row("b")])
        census = stream_store.field_census(conn, "sp-traffic")
        names = {f["field"] for f in census["fields"]}
        self.assertEqual(names, {"clicks", "cost"})
        self.assertEqual(census["records_sampled"], 2)
        conn.close()

    def test_readonly_connect_never_creates_the_file(self):
        """Asking "did anything arrive?" before setup must not leave behind an
        empty database that then looks like a configured-but-silent Stream."""
        self.assertIsNone(stream_store.connect(ro=True))
        self.assertFalse(os.path.exists(self.path))


class Health(unittest.TestCase):
    """The System Health card. Local state only — no AWS call may happen here.

    The card exists because Stream ran for a day with no screen at all: it was
    healthy, and the only way to know was a terminal. These tests pin what the
    card is allowed to CLAIM, because a green Stream card that is wrong is worse
    than no card.
    """

    ENV = {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK",
           "STREAM_QUEUE_NA_SP_TRAFFIC":
               "https://sqs.us-east-1.amazonaws.com/1234/merchads-sp-traffic-na",
           "STREAM_QUEUE_NA_SP_CONVERSION":
               "https://sqs.us-east-1.amazonaws.com/1234/merchads-sp-conversion-na"}

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.unlink(self.path)
        self._real = stream_store.db_path
        stream_store.db_path = lambda: self.path

    def tearDown(self):
        stream_store.db_path = self._real
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _bank(self, dataset, received, window="2026-08-21T09:00:00Z"):
        conn = stream_store.connect()
        stream_store.store_messages(conn, [{
            "message_id": dataset + received, "dataset": dataset, "realm": "NA",
            "topic_arn": "t", "published_at": window,
            "time_window_start": window, "profile_id": "555",
            "payload": json.dumps({"clicks": 1})}])
        conn.execute("UPDATE stream_message SET received_at=? WHERE dataset=?",
                     (received, dataset))
        stream_store.log_drain(conn, "NA", dataset, 1, 1, 0, 0)
        conn.execute("UPDATE stream_drain_log SET at=?", (received,))
        conn.commit()
        conn.close()

    def test_no_queues_configured_reports_not_configured(self):
        info = stream_store.health({})
        self.assertFalse(info["configured"])
        self.assertEqual(info["queues_configured"], 0)
        self.assertEqual(info["datasets"], [])

    def test_configured_but_never_drained_is_stale_not_healthy_zero(self):
        """No database at all means the drain has never run. That is a fault,
        and it must not look like a calm zero."""
        info = stream_store.health(self.ENV)
        self.assertTrue(info["configured"])
        self.assertFalse(info["database"])
        self.assertTrue(info["drain_stale"])
        self.assertEqual({d["state"] for d in info["datasets"]}, {"waiting"})

    def test_a_dataset_that_never_delivered_is_waiting_not_stale(self):
        """sp-conversion sat empty for its whole first day while sp-traffic
        flowed. Empty is not broken."""
        now = datetime.datetime(2026, 8, 21, 12, 0, 0)
        self._bank("sp-traffic", "2026-08-21T11:40:00")
        info = stream_store.health(self.ENV, now=now)
        by = {d["dataset"]: d for d in info["datasets"]}
        self.assertEqual(by["sp-traffic"]["state"], "flowing")
        self.assertEqual(by["sp-conversion"]["state"], "waiting")
        self.assertEqual(by["sp-conversion"]["messages"], 0)
        self.assertFalse(info["drain_stale"])

    def test_a_stopped_drain_goes_stale(self):
        """Stream never resends. A drain that stopped loses data at the end of
        SQS retention, so the card has to shout before then."""
        now = datetime.datetime(2026, 8, 21, 12, 0, 0)
        self._bank("sp-traffic", "2026-08-20T12:00:00")
        info = stream_store.health(self.ENV, now=now)
        self.assertTrue(info["drain_stale"])
        self.assertEqual(info["drain_age_minutes"], 24 * 60)
        by = {d["dataset"]: d for d in info["datasets"]}
        self.assertEqual(by["sp-traffic"]["state"], "quiet")

    def test_a_drain_that_could_not_empty_the_queue_is_reported(self):
        """The failure that hides behind a healthy-looking drain.

        A drain that ran out of time still reports a recent timestamp and a big
        pile of banked messages, so every other signal on the card reads green.
        Only the note says the queue was still full — and a backlog compounds
        every hour until SQS drops the oldest messages, which Stream will never
        resend.
        """
        now = datetime.datetime(2026, 8, 21, 12, 0, 0)
        self._bank("sp-traffic", "2026-08-21T11:40:00")
        conn = stream_store.connect()
        conn.execute("UPDATE stream_drain_log SET note=? WHERE dataset=?",
                     ("time budget 60s ran out, queue not empty", "sp-traffic"))
        conn.commit()
        conn.close()
        info = stream_store.health(self.ENV, now=now)
        self.assertFalse(info["drain_stale"])          # recent, and still wrong
        self.assertEqual(info["drain_backlog"], ["NA/sp-traffic"])

    def test_a_drain_that_emptied_the_queue_reports_no_backlog(self):
        now = datetime.datetime(2026, 8, 21, 12, 0, 0)
        self._bank("sp-traffic", "2026-08-21T11:40:00")
        info = stream_store.health(self.ENV, now=now)
        self.assertIsNone(info["drain_backlog"])

    def test_health_makes_no_network_call(self):
        """System Health opens seven databases already. It must stay offline."""
        import stream_sqs
        real = stream_sqs._sqs_call
        stream_sqs._sqs_call = lambda *a, **k: self.fail("health() called AWS")
        self.addCleanup(setattr, stream_sqs, "_sqs_call", real)
        self._bank("sp-traffic", "2026-08-21T11:40:00")
        stream_store.health(self.ENV)


class DrainExhaustion(unittest.TestCase):
    """Whether the queue was EMPTIED, not just how many messages were read.

    This was the bug the operator caught by eye: the panel said the account had
    spent $1.71 by mid-morning when the real figure was far higher. Nothing was
    broken in the reading or the arithmetic. The hourly drain simply had a 60
    second budget, Stream sends roughly one message per impression, and SQS
    hands them over about ten at a time — so each run read less than one hour's
    arrivals and the queue grew all day. Every drain still logged a healthy
    count of messages banked, which is exactly why it went unnoticed.
    """

    def setUp(self):
        import stream_drain, stream_sqs
        self.drain, self.sqs = stream_drain, stream_sqs
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.unlink(self.path)
        self._real_path = stream_store.db_path
        stream_store.db_path = lambda: self.path
        self._real_recv = stream_sqs.receive
        self._real_del = stream_sqs.delete_batch
        stream_sqs.delete_batch = lambda *a, **k: None

    def tearDown(self):
        stream_store.db_path = self._real_path
        self.sqs.receive = self._real_recv
        self.sqs.delete_batch = self._real_del
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _message(self, n):
        body = json.dumps({"Message": json.dumps({"impressions": 1}),
                           "TopicArn": "t", "Timestamp": "2026-08-21T09:00:00Z"})
        return {"MessageId": f"m{n}", "ReceiptHandle": f"r{n}", "Body": body}

    def _run(self, budget):
        conn = stream_store.connect()
        try:
            return self.drain.drain_queue(
                conn, {"key": "k", "secret": "s"}, "NA", "sp-traffic",
                "https://sqs.us-east-1.amazonaws.com/1/q", "us-east-1",
                budget, verbose=False)
        finally:
            conn.close()

    def test_a_queue_that_never_runs_dry_is_not_exhausted(self):
        n = [0]

        def endless(*a, **k):
            n[0] += 1
            return [self._message(f"{n[0]}-{i}") for i in range(10)]

        self.sqs.receive = endless
        summary = self._run(1)
        self.assertFalse(summary["exhausted"])
        self.assertGreater(summary["received"], 0)

    def test_an_empty_queue_is_exhausted(self):
        self.sqs.receive = lambda *a, **k: []
        summary = self._run(5)
        self.assertTrue(summary["exhausted"])
        self.assertEqual(summary["received"], 0)

    def test_the_backlog_is_written_to_the_drain_log(self):
        """health() reads the note, so the note has to actually be stored."""
        self.sqs.receive = lambda *a, **k: [self._message("x")]
        self._run(1)
        conn = stream_store.connect(ro=True)
        note = conn.execute("SELECT note FROM stream_drain_log").fetchone()[0]
        conn.close()
        self.assertIn("not empty", note)

    def test_the_default_budget_is_big_enough_for_a_busy_hour(self):
        """A guard on the number itself. Measured throughput is about 7
        messages a second, and a busy hour delivers on the order of a thousand
        messages, so anything under ~150 seconds cannot keep up."""
        # Read the default straight off the module rather than trusting a copy
        # of the number written here.
        src = io.open("engine/stream_drain.py", encoding="utf-8").read()
        marker = '--seconds", type=int, default='
        default = int(src.split(marker, 1)[1].split(",", 1)[0])
        self.assertGreaterEqual(default, 150)


if __name__ == "__main__":
    unittest.main()
