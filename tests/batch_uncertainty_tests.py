#!/usr/bin/env python3
""""Amazon rejected it" and "nobody knows" are not the same answer.

The v3 write endpoints answer 207 MULTI-STATUS, which exists precisely because
some items in the batch may have failed. `_item_failures` reports an
unrecognised body as `failed_items: None` — correctly, it has no item
information. But `items_ok` tested `not b.get("failed_items")`, and `not None`
is True, so an unreadable 207 passed every test for total success.

`failed_ids` had the mirror image of the same hole: it returns the ids Amazon
NAMED as rejected, so a 500 and an unreadable 207 both produce an empty set —
and the callers all wrote `[i for i in ids if i not in rejected]`, which is
every id. The result reached three places at once: writes_log recorded
"submitted" for writes that never happened, the local ad-group and target
mirrors moved to a state Amazon might not hold, and Undo then offered to
restore from a previous state that was never true.

Local state that disagrees with the account is worse than local state that is a
day behind, because the next preview proposes from it.

Found by review, 2026-08-23.

Run from the Ads folder:
    python3 -m unittest tests.batch_uncertainty_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ.setdefault("ADS_MARKET", "US")

import ads_client as ac  # noqa: E402


def batch(http, failed_items, failed_ids=(), ids=("a", "b")):
    return {"http": http, "failed_items": failed_items,
            "failed_ids": list(failed_ids), "ids": list(ids),
            "count": len(ids), "body": {}}


class AnUnreadableMultiStatusIsNotSuccess(unittest.TestCase):

    def test_a_clean_207_is_success(self):
        res = [batch(207, 0)]
        self.assertTrue(ac.items_ok(res))
        self.assertEqual(ac.certain_ids(res), {"a", "b"})

    def test_a_207_we_cannot_read_is_not_success(self):
        """The bug. failed_items is None because the body had no item block."""
        res = [batch(207, None)]
        self.assertFalse(ac.items_ok(res))
        self.assertEqual(ac.batch_uncertain(res), res)

    def test_nothing_from_an_unreadable_batch_may_be_mirrored(self):
        """Not "assume they failed" — assume nothing. The point is that the
        local mirror must not move on a guess in either direction."""
        self.assertEqual(ac.certain_ids([batch(207, None)]), set())

    def test_a_plain_200_with_no_item_block_is_still_success(self):
        """The non-batch endpoints answer this way and it genuinely means the
        write landed. Failing closed on THIS would freeze ordinary writes, and a
        guard that stops normal work is a guard that gets removed."""
        res = [batch(200, None)]
        self.assertTrue(ac.items_ok(res))
        self.assertEqual(ac.certain_ids(res), {"a", "b"})


class AHardFailureSettlesNothingEither(unittest.TestCase):

    def test_a_500_mirrors_nothing(self):
        """`failed_ids` is empty for a 500 because Amazon named no items, and
        the old callers read an empty rejected set as "everything was accepted"."""
        self.assertEqual(ac.certain_ids([batch(500, None)]), set())
        self.assertFalse(ac.items_ok([batch(500, None)]))

    def test_a_dead_batch_does_not_poison_a_healthy_one(self):
        """Batches fail independently, so the judgement is per batch. Refusing
        the whole request would throw away writes that demonstrably landed."""
        res = [batch(500, None, ids=["a", "b"]),
               batch(207, 0, ids=["c", "d"])]
        self.assertEqual(ac.certain_ids(res), {"c", "d"})
        self.assertFalse(ac.items_ok(res))

    def test_named_rejections_are_still_honoured(self):
        res = [batch(207, 1, failed_ids=["b"])]
        self.assertEqual(ac.certain_ids(res), {"a"})


class TheParserItselfStillReportsHonestly(unittest.TestCase):

    def test_an_unrecognised_body_reports_no_item_information(self):
        self.assertEqual(ac._item_failures({"text": "<html>502</html>"}), (None, []))

    def test_a_real_v3_body_is_read(self):
        body = {"negativeKeywords": {"success": [{"index": 0}],
                                     "error": [{"index": 1}]}}
        failed, idx = ac._item_failures(body)
        self.assertEqual((failed, idx), (1, [1]))

    def test_a_batch_result_carries_its_own_ids(self):
        """Without them a caller holding several batches cannot say which ids a
        failed batch was even about, so it has to reason over the whole request."""
        class R:
            status_code = 207
            def json(self): return {"k": {"success": [], "error": [{"index": 0}]}}
        r = ac._batch_result(R(), ["x", "y"])
        self.assertEqual(r["ids"], ["x", "y"])
        self.assertEqual(r["failed_ids"], ["x"])


if __name__ == "__main__":
    unittest.main()
