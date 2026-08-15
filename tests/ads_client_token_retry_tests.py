#!/usr/bin/env python3
"""The LWA token fetch must survive a transient connection drop.

Amazon's Login-with-Amazon token endpoint occasionally closes the connection
mid-handshake (RemoteDisconnected). Before this fix, access_token() did a single
POST with no retry, so one blip failed a whole nightly step — US preempt_negatives
died this way (Aug 2026). The data calls (_send_retry) already retried; the token
fetch, which precedes every call, did not.

The fix retries transient transport errors and 5xx (the token fetch is
idempotent), but must NOT retry a 4xx: a bad or expired refresh token is
permanent, so retrying only wastes a minute and buries the real error.

Run from the Ads folder:  python3 -m unittest tests.ads_client_token_retry_tests -v"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import requests  # noqa: E402
import ads_client  # noqa: E402


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _client():
    """An AdsClient without going through __init__ (no .env needed) — just the
    attributes access_token() reads."""
    c = ads_client.AdsClient.__new__(ads_client.AdsClient)
    c._access_token = None
    c._token_expiry = 0
    c.refresh_token = "rt"
    c.client_id = "cid"
    c.client_secret = "sec"
    return c


class TokenFetchRetry(unittest.TestCase):
    def test_retries_transient_drop_then_succeeds(self):
        c = _client()
        calls = {"n": 0}

        def flaky_post(url, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ConnectionError(
                    "('Connection aborted.', RemoteDisconnected(...))")
            return FakeResp(200, {"access_token": "TOKEN", "expires_in": 3600})

        with mock.patch.object(ads_client.requests, "post", flaky_post), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            tok = c.access_token()

        self.assertEqual(tok, "TOKEN")
        self.assertEqual(calls["n"], 3)   # 2 drops + 1 success

    def test_retries_transient_5xx_then_succeeds(self):
        c = _client()
        calls = {"n": 0}

        def flaky_post(url, **kw):
            calls["n"] += 1
            if calls["n"] < 2:
                return FakeResp(503)
            return FakeResp(200, {"access_token": "TOKEN", "expires_in": 3600})

        with mock.patch.object(ads_client.requests, "post", flaky_post), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            tok = c.access_token()

        self.assertEqual(tok, "TOKEN")
        self.assertEqual(calls["n"], 2)

    def test_permanent_4xx_raises_without_retry(self):
        c = _client()
        calls = {"n": 0}

        def bad_creds(url, **kw):
            calls["n"] += 1
            return FakeResp(401, {"error": "invalid_grant"})

        with mock.patch.object(ads_client.requests, "post", bad_creds), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            with self.assertRaises(requests.exceptions.HTTPError):
                c.access_token()

        self.assertEqual(calls["n"], 1)   # 401 is permanent — no retry

    def test_gives_up_after_five_drops(self):
        c = _client()
        calls = {"n": 0}

        def always_drop(url, **kw):
            calls["n"] += 1
            raise requests.exceptions.ConnectionError("drop")

        with mock.patch.object(ads_client.requests, "post", always_drop), \
                mock.patch.object(ads_client.time, "sleep", lambda *_a, **_k: None):
            with self.assertRaises(requests.exceptions.ConnectionError):
                c.access_token()

        self.assertEqual(calls["n"], 5)   # tries=5, then re-raises

    def test_a_fresh_cached_token_short_circuits(self):
        # Sanity: a still-valid cached token must NOT hit the network at all.
        c = _client()
        c._access_token = "CACHED"
        c._token_expiry = 10 ** 12   # far future

        def explode(url, **kw):
            raise AssertionError("should not fetch when a fresh token is cached")

        with mock.patch.object(ads_client.requests, "post", explode):
            self.assertEqual(c.access_token(), "CACHED")


if __name__ == "__main__":
    unittest.main()
