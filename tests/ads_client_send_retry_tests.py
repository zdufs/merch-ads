#!/usr/bin/env python3
"""_send_retry must survive a transient connection drop on READS — but never
retry a mutating WRITE on one.

_send_retry already backed off on HTTP 429/5xx, but a connection-level drop
(RemoteDisconnected/timeout) is raised by requests before any status code, so it
sailed straight through and failed the call. The token fetch had the same gap and
was fixed first (ads_client_token_retry_tests).

The asymmetry here is deliberate: a re-fetched list has no side effect, so reads
retry (retry_transport=True, set by list_all). A mutating POST/PUT that the server
actually processed before the socket dropped must NOT be blindly re-sent, or it
could double-apply — so writes keep the default (retry_transport=False) and a drop
propagates as before.

Run from the Ads folder:  python3 -m unittest tests.ads_client_send_retry_tests -v"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import requests  # noqa: E402
import ads_client  # noqa: E402


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload or "")

    def json(self):
        return self._payload


def _client():
    """An AdsClient without __init__: a cached token (so _headers never hits the
    network) plus the attributes _send_retry / list_all read."""
    c = ads_client.AdsClient.__new__(ads_client.AdsClient)
    c._access_token = "T"
    c._token_expiry = 10 ** 12   # far future — access_token() short-circuits
    c.client_id = "cid"
    c.profile_id = "pid"
    c.base = "https://example.test"
    return c


class SendRetryTransport(unittest.TestCase):
    def test_reads_retry_a_connection_drop_then_succeed(self):
        c = _client()
        calls = {"n": 0}

        def flaky(method, url, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ConnectionError("drop")
            return FakeResp(200, {"ok": True})

        with mock.patch.object(ads_client.requests, "request", flaky), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            resp = c._send_retry("POST", "/sp/x/list", "ct", {}, retry_transport=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls["n"], 3)   # 2 drops + 1 success

    def test_writes_do_NOT_retry_a_connection_drop(self):
        # A mutating write must fail fast on a drop — retrying risks double-apply.
        c = _client()
        calls = {"n": 0}

        def drop(method, url, **kw):
            calls["n"] += 1
            raise requests.exceptions.ConnectionError("drop")

        with mock.patch.object(ads_client.requests, "request", drop), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            with self.assertRaises(requests.exceptions.ConnectionError):
                c._send_retry("PUT", "/sp/targets", "ct", {})   # default: no transport retry

        self.assertEqual(calls["n"], 1)

    def test_status_backoff_unchanged_by_the_change(self):
        # The pre-existing 429/5xx backoff must still work (and for writes too).
        c = _client()
        calls = {"n": 0}

        def flaky(method, url, **kw):
            calls["n"] += 1
            return FakeResp(503) if calls["n"] < 2 else FakeResp(200, {"ok": True})

        with mock.patch.object(ads_client.requests, "request", flaky), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            resp = c._send_retry("PUT", "/sp/targets", "ct", {})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls["n"], 2)

    def test_reads_give_up_after_five_drops(self):
        c = _client()
        calls = {"n": 0}

        def always_drop(method, url, **kw):
            calls["n"] += 1
            raise requests.exceptions.ConnectionError("drop")

        with mock.patch.object(ads_client.requests, "request", always_drop), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            with self.assertRaises(requests.exceptions.ConnectionError):
                c._send_retry("POST", "/sp/x/list", "ct", {}, retry_transport=True)

        self.assertEqual(calls["n"], 5)

    def test_list_all_survives_a_connection_drop(self):
        # Integration: list_all must opt into transport retry so a paginated
        # read doesn't crash the whole run on one blip.
        c = _client()
        calls = {"n": 0}

        def flaky(method, url, **kw):
            calls["n"] += 1
            if calls["n"] < 2:
                raise requests.exceptions.ConnectionError("drop")
            return FakeResp(200, {"campaigns": [{"campaignId": "1"}]})

        with mock.patch.object(ads_client.requests, "request", flaky), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            items = c.list_all("/sp/campaigns/list", "ct", "campaigns")

        self.assertEqual(items, [{"campaignId": "1"}])
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
