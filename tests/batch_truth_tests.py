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
sys.path.insert(0, os.path.join(HERE, "engine"))
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


class AppliedSubset(unittest.TestCase):
    """A multi-item write is judged PER ITEM, not all-or-nothing.

    `_http_ok` answers "did every item go through", which is right for the
    single-entity writes that make up almost every command. Applied to a batch
    of thirty it is a lie in the dangerous direction: Amazon rejects individual
    items inside a 207 routinely, and one duplicate negative used to turn
    twenty-nine live keywords into a reply saying nothing was applied, thirty
    Audit Trail rows saying `failed`, and a local mirror left describing ad
    groups Amazon had already paused.
    """

    def test_one_rejected_item_does_not_reject_the_rest(self):
        batches = [{"http": 207, "failed_items": 1, "failed_ids": ["g2"]}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2", "g3"]),
                         ["g1", "g3"])

    def test_a_clean_batch_applies_everything(self):
        batches = [{"http": 207, "failed_items": 0, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2"]),
                         ["g1", "g2"])

    def test_a_wholly_rejected_batch_applies_nothing(self):
        batches = [{"http": 207, "failed_items": 2, "failed_ids": ["g1", "g2"]}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2"]), [])

    def test_a_transport_failure_counts_nothing(self):
        """A 500 has no body to parse, so `failed_ids` is empty — which reads
        exactly like a clean run. Refusing the whole call is the only safe
        reading, and it is also what the code did before this existed."""
        batches = [{"http": 500, "failed_items": 0, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2"]), [])

    def test_one_bad_batch_poisons_the_call_even_beside_a_good_one(self):
        batches = [{"http": 200, "failed_items": 0, "failed_ids": []},
                   {"http": 503, "failed_items": 0, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2"]), [])

    def test_a_failure_amazon_could_not_NAME_counts_nothing(self):
        """The hole this function shipped with, found by review 2026-08-23.

        `failed_items` counts the v3 error entries. `failed_ids` carries only
        the ones whose `index` mapped back onto an id we sent. An error entry
        without a usable index leaves us knowing something was refused and not
        knowing WHAT — and subtracting only the named ids then counted a
        REPORTED FAILURE AS ACCEPTED. The local mirror said PAUSED for an ad
        group Amazon had left ENABLED, so it kept spending while the screen
        said it had stopped: the exact desync this whole family exists to stop,
        reached from the other side.
        """
        batches = [{"http": 207, "failed_items": 1, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2"]), [],
                         "one unnamed rejection makes the whole call unknowable")

    def test_a_partly_named_failure_also_counts_nothing(self):
        """Two refused, one named. The named one is not the whole story, so
        subtracting it and keeping the rest is still a guess."""
        batches = [{"http": 207, "failed_items": 2, "failed_ids": ["g2"]}]
        self.assertEqual(appctl._applied_subset(batches, ["g1", "g2", "g3"]), [])

    def test_no_item_information_at_all_is_not_treated_as_failure(self):
        """`_item_failures` answers (None, []) for a body it does not
        recognise: no item info, which is NOT the same as reporting failures.
        A 200 with an unparseable body keeps the old reading — otherwise every
        unrecognised success shape would start reporting nothing applied."""
        batches = [{"http": 200, "failed_items": None, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["g1"]), ["g1"])

    def test_no_batches_at_all_applies_nothing(self):
        self.assertEqual(appctl._applied_subset([], ["g1"]), [])

    def test_ids_are_compared_as_strings(self):
        """writes_log and the Amazon payloads disagree about int vs str, so a
        numeric id must still match the string Amazon rejected."""
        batches = [{"http": 207, "failed_items": 1, "failed_ids": ["22"]}]
        self.assertEqual(appctl._applied_subset(batches, [11, 22, 33]),
                         ["11", "33"])

    def test_order_follows_the_request(self):
        batches = [{"http": 200, "failed_items": 0, "failed_ids": []}]
        self.assertEqual(appctl._applied_subset(batches, ["c", "a", "b"]),
                         ["c", "a", "b"])


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


class AppliedSubsetAgreesWithTheRealParser(unittest.TestCase):
    """Driven through `ads_client._batch_result`, not through hand-written dicts.

    Every existing test above builds its own batch dict, so all of them agree
    with whatever the test author believed the parser produces. That is how the
    unnamed-failure case survived: no fixture could produce it, because the
    author wrote `failed_ids` in by hand. These start from an Amazon-shaped
    response body and let the real parser make the dict.
    """

    class _Resp:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, ""

        def json(self):
            return self._body

    def _batch(self, status, body, ids):
        import ads_client
        return ads_client._batch_result(self._Resp(status, body), ids)

    def test_an_error_entry_with_no_index_is_not_an_acceptance(self):
        body = {"adGroups": {"success": [], "error": [{"errors": [{"errorType": "X"}]}]}}
        b = self._batch(207, body, ["g1"])
        self.assertEqual(b["failed_items"], 1)
        self.assertEqual(b["failed_ids"], [],
                         "the parser could not place it — this is the premise")
        self.assertEqual(appctl._applied_subset([b], ["g1"]), [])

    def test_it_agrees_with_items_ok_on_that_body(self):
        """`ads_client.items_ok` has always read `failed_items` and answered
        False here. `_applied_subset` answered "accepted". Two helpers over one
        response, disagreeing about whether a write happened."""
        import ads_client
        body = {"adGroups": {"success": [], "error": [{"errors": [{"errorType": "X"}]}]}}
        b = self._batch(207, body, ["g1"])
        self.assertFalse(ads_client.items_ok([b]))
        self.assertEqual(appctl._applied_subset([b], ["g1"]), [])

    def test_a_properly_indexed_rejection_still_spares_the_others(self):
        """The fix must not make a normal partial failure refuse everything —
        that was the original bug, in the opposite direction."""
        body = {"adGroups": {"success": [{"index": 0, "adGroupId": "g1"},
                                         {"index": 2, "adGroupId": "g3"}],
                             "error": [{"index": 1, "errors": [{"errorType": "DUP"}]}]}}
        b = self._batch(207, body, ["g1", "g2", "g3"])
        self.assertEqual(b["failed_items"], 1)
        self.assertEqual(b["failed_ids"], ["g2"])
        self.assertEqual(appctl._applied_subset([b], ["g1", "g2", "g3"]),
                         ["g1", "g3"])


class UnknowableIsNotTheSameAsRejected(unittest.TestCase):
    """`_applied_subset` answering [] meant two different things.

    Amazon refused everything, and nobody knows what Amazon did, produced the
    identical empty list — and both callers then computed
    `rejected = len(requested) - 0` and told the operator "Amazon refused all
    40 of them". That is a claim about Amazon that nothing in the reply
    supports: on a 500, or on an error entry with no usable index, some of
    those writes may well have landed. Saying they were refused invites the
    operator to run it again.

    `_applied_outcome` answers (accepted, confirmed), and the callers report
    `rejected_count: null` when confirmed is False.
    Found by the second review pass, 2026-08-23.
    """

    def test_a_real_rejection_is_confirmed(self):
        b = [{"http": 207, "failed_items": 1, "failed_ids": ["g2"]}]
        applied, confirmed = appctl._applied_outcome(b, ["g1", "g2"])
        self.assertEqual(applied, ["g1"])
        self.assertTrue(confirmed)

    def test_everything_refused_is_still_confirmed(self):
        b = [{"http": 207, "failed_items": 2, "failed_ids": ["g1", "g2"]}]
        applied, confirmed = appctl._applied_outcome(b, ["g1", "g2"])
        self.assertEqual(applied, [])
        self.assertTrue(confirmed, "Amazon named both — that IS an answer")

    def test_a_transport_failure_is_not_confirmed(self):
        b = [{"http": 500, "failed_items": 0, "failed_ids": []}]
        applied, confirmed = appctl._applied_outcome(b, ["g1", "g2"])
        self.assertEqual(applied, [])
        self.assertFalse(confirmed, "nothing came back to read")

    def test_an_unnamed_failure_is_not_confirmed(self):
        b = [{"http": 207, "failed_items": 1, "failed_ids": []}]
        applied, confirmed = appctl._applied_outcome(b, ["g1", "g2"])
        self.assertEqual(applied, [])
        self.assertFalse(confirmed)

    def test_no_batches_is_not_confirmed(self):
        self.assertEqual(appctl._applied_outcome([], ["g1"]), ([], False))

    def test_the_old_helper_still_answers_the_same_list(self):
        for b in ([{"http": 207, "failed_items": 1, "failed_ids": ["g2"]}],
                  [{"http": 500, "failed_items": 0, "failed_ids": []}],
                  []):
            with self.subTest(batches=b):
                self.assertEqual(appctl._applied_subset(b, ["g1", "g2"]),
                                 appctl._applied_outcome(b, ["g1", "g2"])[0])


class _RefusedResponse:
    """A whole batch Amazon turned down. `.text` is present because
    ads_client._safe_json falls back to it when .json() raises."""

    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else {"message": "invalid"}
        self.text = ""

    def json(self):
        return self._body


class WholeBatchRejection(unittest.TestCase):
    """A batch Amazon refused outright must mark every item failed.

    Found by mutation on 2026-08-24. Adding 400 to the accepted set in
    `_batch_outcomes` broke nothing in the whole suite, and the behaviour it
    breaks is the one this engine spent a commit on: recording a write Amazon
    never made.

    Why nothing caught it is worth keeping. `item_outcomes` carries its OWN
    `http not in (200, 207)` check, so a reader comparing the two concludes the
    batch-level one is a redundant second belt. It is not. `_batch_result`
    stores `_batch_outcomes(...)` under "outcomes", and `item_outcomes` returns
    that stored list and continues before its own check is ever reached. The
    check inside `_batch_outcomes` is the only one that runs on this path.
    """

    def test_a_refused_batch_marks_every_item_failed(self):
        for code in (400, 401, 403, 404, 422, 429, 500, 503):
            with self.subTest(http=code):
                outcomes = ads_client._batch_outcomes(code, {}, 3)
                self.assertEqual([o["status"] for o in outcomes],
                                 ["failed"] * 3,
                                 f"http {code} must not read as accepted")

    def test_a_refused_batch_yields_no_certain_ids(self):
        """The end of the chain: nothing from a refused batch may be logged as
        written. Asserted directly rather than inferred from the statuses."""
        batch = ads_client._batch_result(_RefusedResponse(400), ["t1", "t2"])
        # certain_ids answers with a SET, so this asserts emptiness rather than
        # equality with a list — the first draft of this test failed on correct
        # code for that reason alone.
        self.assertFalse(ads_client.certain_ids([batch], ["t1", "t2"]))
        self.assertFalse(ads_client.items_ok([batch]))

    def test_the_stored_outcomes_are_what_item_outcomes_returns(self):
        """Pins WHY the mutation survived, so a future reader does not delete
        the batch-level check believing it duplicated the one below it."""
        batch = ads_client._batch_result(_RefusedResponse(400), ["a"])
        batch["http"] = 200          # only the stored outcomes should count
        self.assertEqual(ads_client.item_outcomes([batch], ["a"]), ["failed"])


if __name__ == "__main__":
    unittest.main()
