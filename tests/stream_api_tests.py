#!/usr/bin/env python3
"""The one guard standing between us and counting every impression twice.

`engine/stream_api.py` had no test of any kind. It is 124 lines, it is reached
from `appctl stream-subscribe` and `stream-unsubscribe`, and those are LIVE
writes. The guard that matters is in `create_subscription`: a second
subscription to the same dataset means Amazon pushes every row twice, the drain
banks both, and there is no signal anywhere that says so. Spend, impressions
and sales would simply read double on every screen, consistently, which is the
failure mode this codebase treats as worse than a crash.

Nothing here talks to Amazon: `StreamAPI` is built with `__new__` so no `.env`
is read and no client is constructed.

Run from the Ads folder:  python3 -m unittest tests.stream_api_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import stream_api      # noqa: E402
import stream_config   # noqa: E402

DATASET = sorted(stream_config.DATASETS)[0]


class _Client:
    """Records what would have been sent; answers like a happy Amazon."""

    def __init__(self):
        self.sent = []

    def _send_retry(self, method, path, content_type, body):
        self.sent.append({"method": method, "path": path, "body": body})

        class R:
            # _unwrap reads .text before it reads .json(), so a fake without
            # one raises AttributeError and the test passes for the wrong
            # reason -- it would still see the ValueError it was expecting.
            status_code = 200
            text = '{"subscriptionId": "sub-new"}'

            @staticmethod
            def json():
                return {"subscriptionId": "sub-new"}
        return R()


def api(subscriptions):
    a = stream_api.StreamAPI.__new__(stream_api.StreamAPI)
    a.market, a.realm, a.region = "US", "NA", "us-east-1"
    a.client = _Client()
    a.list_subscriptions = lambda: list(subscriptions)
    return a


def sub(status, dataset=DATASET, sid="sub-1"):
    return {"subscriptionId": sid, "dataSetId": dataset, "status": status}


class ASecondSubscriptionIsRefused(unittest.TestCase):

    def test_an_active_subscription_blocks_a_second(self):
        a = api([sub(stream_api.ACTIVE)])
        with self.assertRaises(ValueError) as e:
            a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
        self.assertIn("already subscribed", str(e.exception))
        self.assertEqual(a.client.sent, [],
                         "nothing may be sent to Amazon on a refusal")

    def test_a_pending_subscription_also_blocks(self):
        """PENDING is not a failed subscription. It is one Amazon has accepted
        and not yet wired up, so a second create would leave two live."""
        a = api([sub(stream_api.PENDING)])
        with self.assertRaises(ValueError):
            a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
        self.assertEqual(a.client.sent, [])

    def test_an_archived_subscription_does_not_block(self):
        """ARCHIVED is Amazon's off switch and has no delete, so refusing on
        one would make a queue change impossible."""
        a = api([sub(stream_api.ARCHIVED)])
        a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
        self.assertEqual(len(a.client.sent), 1)

    def test_another_datasets_subscription_does_not_block(self):
        other = [d for d in stream_config.DATASETS if d != DATASET]
        if not other:
            self.skipTest("only one dataset configured")
        a = api([sub(stream_api.ACTIVE, dataset=other[0])])
        a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
        self.assertEqual(len(a.client.sent), 1)

    def test_an_unknown_dataset_is_refused_before_any_call(self):
        a = api([])
        with self.assertRaises(ValueError) as e:
            a.create_subscription("sp-not-a-dataset", "arn:aws:sqs:us-east-1:1:q")
        self.assertIn("Unknown dataset", str(e.exception))
        self.assertEqual(a.client.sent, [])


class TheCreateCarriesWhatAmazonRequires(unittest.TestCase):

    def test_a_client_request_token_is_always_sent(self):
        """Amazon rejects the create without it — 'Value null at
        clientRequestToken failed to satisfy constraint'."""
        a = api([])
        a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
        body = a.client.sent[0]["body"]
        self.assertTrue(body.get("clientRequestToken"),
                        "Amazon refuses a create with no idempotency token")
        self.assertEqual(body["dataSetId"], DATASET)
        self.assertEqual(body["destinationArn"], "arn:aws:sqs:us-east-1:1:q")

    def test_each_create_gets_a_fresh_token(self):
        """A token reused across an archive-and-recreate collides with the old
        request, and Amazon then answers the OLD one instead of subscribing."""
        seen = set()
        for _ in range(3):
            a = api([])
            a.create_subscription(DATASET, "arn:aws:sqs:us-east-1:1:q")
            seen.add(a.client.sent[0]["body"]["clientRequestToken"])
        self.assertEqual(len(seen), 3)


class ArchiveIsTheOffSwitch(unittest.TestCase):

    def test_archive_sends_a_put_with_archived_status(self):
        a = api([])
        a.archive_subscription("sub-7")
        sent = a.client.sent[0]
        self.assertEqual(sent["method"], "PUT")
        self.assertIn("sub-7", sent["path"])
        self.assertEqual(sent["body"]["status"], stream_api.ARCHIVED)


if __name__ == "__main__":
    unittest.main()
