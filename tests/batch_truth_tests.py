#!/usr/bin/env python3
"""Item-level 207 truthfulness + the budget ceiling + honest phase0 exits.

Amazon's v3 batch writes return HTTP 207 even when every item in the batch
errored. The engine treated 207 as success in ~10 modules, so a wholly
rejected batch was reported "submitted" and the local mirror desynced. These
tests pin: per-item failure parsing in ads_client, _http_ok refusing
item-level failures, and the campaign-budget ceiling (bids had one; budgets
had none).

Run from the Ads folder:  python3 -m unittest tests.batch_truth_tests -v"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["ADS_MARKET"] = "US"

import ads_client  # noqa: E402
import appctl      # noqa: E402


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def stub_client(responses, target_cap=None, keyword_cap=None, budget_cap=None):
    """AdsClient without .env/Amazon: __new__ + a canned _send_retry."""
    c = ads_client.AdsClient.__new__(ads_client.AdsClient)
    c._ceilings = {"target": target_cap, "keyword": keyword_cap, "budget": budget_cap}
    c.last_clamps = []
    queue = list(responses)
    c._send_retry = lambda *a, **k: queue.pop(0)
    return c


PARTIAL_207 = {"adGroups": {
    "success": [{"index": 0, "adGroupId": "g1"}],
    "error": [{"index": 1, "errors": [{"errorType": "ENTITY_NOT_FOUND"}]}],
}}


class ItemFailures(unittest.TestCase):
    def test_error_entries_are_counted_with_indexes(self):
        failed, idx = ads_client._item_failures(PARTIAL_207)
        self.assertEqual(failed, 1)
        self.assertEqual(idx, [1])

    def test_clean_body_reports_zero(self):
        failed, idx = ads_client._item_failures(
            {"targetingClauses": {"success": [{"index": 0}], "error": []}})
        self.assertEqual(failed, 0)
        self.assertEqual(idx, [])

    def test_unrecognized_body_reports_none_not_crash(self):
        self.assertEqual(ads_client._item_failures(None), (None, []))
        self.assertEqual(ads_client._item_failures({"message": "throttled"}), (None, []))


class FailedIds(unittest.TestCase):
    def test_state_setter_maps_indexes_to_ids(self):
        c = stub_client([FakeResp(207, PARTIAL_207)])
        res = c.set_ad_groups_state(["g1", "g2"], "PAUSED")
        self.assertEqual(res[0]["failed_items"], 1)
        self.assertEqual(res[0]["failed_ids"], ["g2"])

    def test_bid_update_maps_indexes_to_target_ids(self):
        body = {"targetingClauses": {"success": [{"index": 1}],
                                     "error": [{"index": 0}]}}
        c = stub_client([FakeResp(207, body)])
        res = c.update_target_bids([{"targetId": "t9", "bid": 0.3},
                                    {"targetId": "t2", "bid": 0.4}])
        self.assertEqual(res[0]["failed_ids"], ["t9"])

    def test_pausing_keywords_takes_plain_ids_like_its_siblings(self):
        # set_keywords_state receives a list of id STRINGS, exactly like
        # set_targets_state and set_ad_groups_state. It re-indexed them as if
        # they were request dicts, so every call raised TypeError before any
        # result came back. That crashed FR's scavenger_optimize on 2026-08-06
        # and failed the whole nightly, leaving the wasteful keywords running.
        body = {"keywords": {"success": [{"index": 0}], "error": [{"index": 1}]}}
        c = stub_client([FakeResp(207, body)])
        res = c.set_keywords_state(["k1", "k2"], "PAUSED")
        self.assertEqual(res[0]["count"], 2)
        self.assertEqual(res[0]["failed_ids"], ["k2"])


class ArchiveEndpoint(unittest.TestCase):
    """Archiving is NOT a state write.

    `PUT /sp/campaigns` with state ARCHIVED is rejected by Amazon:
      "Value 'ARCHIVED' at 'campaigns.1.member.state' failed to satisfy
       constraint: Member must satisfy enum value set: [ENABLED, PROPOSED,
       PAUSED]"
    In v3 archiving moved to a separate delete endpoint, and ARCHIVED became a
    read-only state that only comes back from list calls."""

    def sent(self, responses):
        calls = []
        c = stub_client(responses)
        real = c._send_retry
        def spy(method, path, ct, payload, **kw):
            calls.append((method, path, payload))
            return real(method, path, ct, payload, **kw)
        c._send_retry = spy
        return c, calls

    def test_it_posts_to_the_delete_endpoint_with_an_id_filter(self):
        c, calls = self.sent([FakeResp(200, {"campaigns": {"success": [{"index": 0}],
                                                           "error": []}})])
        c.archive_campaigns(["c1"])
        method, path, payload = calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/sp/campaigns/delete")
        self.assertEqual(payload, {"campaignIdFilter": {"include": ["c1"]}})

    def test_rejected_ids_come_back_named(self):
        body = {"campaigns": {"success": [{"index": 0}], "error": [{"index": 1}]}}
        c, _ = self.sent([FakeResp(207, body)])
        res = c.archive_campaigns(["c1", "c2"])
        self.assertEqual(res[0]["failed_ids"], ["c2"])

    def test_state_writes_still_refuse_to_carry_archived(self):
        # Belt and braces: nothing should route ARCHIVED back through the state
        # setter, because Amazon's enum does not contain it.
        import inspect
        src = inspect.getsource(appctl.cmd_archive_campaign)
        self.assertNotIn("_set_campaign_state", src)


class ApiErrors(unittest.TestCase):
    """A rejected write must say WHY.

    Archiving 28 campaigns returned `applied: false, http: [400]` and nothing
    else, so there was no way to tell a bad payload from a bad id from an
    endpoint Amazon does not support. The response body was parsed and then
    discarded."""

    def test_item_level_error_messages_are_surfaced(self):
        body = {"campaigns": {"success": [], "error": [
            {"index": 0, "errors": [{"errorType": "INVALID_ARGUMENT",
                                     "errorValue": {"message": "state is not modifiable"}}]}]}}
        msg = appctl._api_errors([{"http": 207, "body": body}])
        self.assertIn("state is not modifiable", msg)

    def test_a_plain_400_envelope_is_surfaced(self):
        msg = appctl._api_errors([{"http": 400, "body": {"message": "Unsupported state"}}])
        self.assertIn("Unsupported state", msg)

    def test_a_clean_result_says_nothing(self):
        self.assertIsNone(appctl._api_errors([{"http": 200, "body": {}}]))


class HttpOk(unittest.TestCase):
    def test_207_with_item_errors_is_not_ok(self):
        self.assertFalse(appctl._http_ok(
            [{"http": 207, "failed_items": 1, "failed_ids": ["g2"]}]))

    def test_clean_207_stays_ok(self):
        self.assertTrue(appctl._http_ok([{"http": 207, "failed_items": 0}]))

    def test_legacy_result_without_item_info_stays_ok(self):
        # older shapes (no failed_items key) must not start failing
        self.assertTrue(appctl._http_ok([{"http": 200}]))


class BudgetCeiling(unittest.TestCase):
    def test_budget_above_ceiling_is_clamped_and_recorded(self):
        body = {"campaigns": {"success": [{"index": 0}], "error": []}}
        c = stub_client([FakeResp(207, body)], budget_cap=15.0)
        c.update_campaign_budgets([{"campaignId": "c1", "budget": 40.0}])
        self.assertEqual(c.last_clamps,
                         [{"id": "c1", "requested": 40.0, "cap": 15.0}])

    def test_no_budget_ceiling_passes_through(self):
        body = {"campaigns": {"success": [{"index": 0}], "error": []}}
        c = stub_client([FakeResp(207, body)], budget_cap=None)
        c.update_campaign_budgets([{"campaignId": "c1", "budget": 40.0}])
        self.assertEqual(c.last_clamps, [])


class Phase0Exit(unittest.TestCase):
    def test_pull_structure_reports_failures_upward(self):
        import phase0_pull

        class ExplodingClient:
            def list_all(self, *a, **k):
                raise RuntimeError("boom")

        class NullConn:
            def execute(self, *a, **k):
                raise RuntimeError("never reached")

        failures = phase0_pull.pull_structure(ExplodingClient(), NullConn())
        self.assertTrue(failures)          # campaigns + ad groups + mirror all failed
        self.assertTrue(any("campaign" in f for f in failures))


if __name__ == "__main__":
    unittest.main()
