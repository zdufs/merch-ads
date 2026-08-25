#!/usr/bin/env python3
"""The AWS account holding the Stream queues has a deadline, and it fails quietly.

It was opened 2026-08-21 on the free plan, which auto-closes six months in. The
bill for two SQS queues is about nothing either way, so this is paperwork — and
it is the most dangerous kind of deadline because of HOW it fails.

If the account lapses the queues go, Stream stops arriving, and Amazon carries on
reporting the subscription ACTIVE. Every screen still works. The Dashboard shows
a day that got quieter, and that is indistinguishable from a slow sales week
until someone compares it against the nightly report.

So it is an alert with two months of warning rather than a line in a document
nobody re-reads.

Run from the Ads folder:  python3 -m unittest tests.aws_plan_expiry_tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))
os.environ["ADS_MARKET"] = "US"

import appctl          # noqa: E402
import markets         # noqa: E402
import stream_config   # noqa: E402

EXPIRY = "2027-02-21"


class ItIsSilentUntilItMatters(unittest.TestCase):

    def test_nothing_is_said_five_months_out(self):
        self.assertEqual(
            appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2026-09-01"), [])

    def test_it_speaks_inside_the_warning_window(self):
        got = appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-01-05")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "aws_plan_expiry")
        self.assertIn(EXPIRY, got[0]["message"])
        self.assertIn("47 days", got[0]["message"])

    def test_the_day_before_still_counts_down(self):
        got = appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-02-20")
        self.assertIn("1 days", got[0]["message"])

    def test_after_the_date_it_says_so_rather_than_going_quiet(self):
        """An alert that stops the moment the thing happens is worse than none."""
        got = appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-03-10")
        self.assertEqual(len(got), 1)
        self.assertIn("was due to close", got[0]["message"])

    def test_the_message_says_how_it_will_fail(self):
        """A date with no consequence attached gets dismissed."""
        msg = appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-01-05")[0]["message"]
        for phrase in ("ACTIVE", "Stream", "quieter"):
            self.assertIn(phrase, msg)


class ItIsGlobalAndSwitchableOff(unittest.TestCase):

    def test_only_the_default_market_reports_it(self):
        """One AWS account serves every realm — one alert, not seven."""
        for other in ("UK", "DE", "USKDP"):
            with self.subTest(market=other):
                self.assertEqual(
                    appctl._aws_plan_expiry_alerts(other, today="2027-01-05"), [])

    def test_clearing_the_date_switches_it_off(self):
        real = stream_config.AWS_PLAN_EXPIRY
        self.addCleanup(setattr, stream_config, "AWS_PLAN_EXPIRY", real)
        stream_config.AWS_PLAN_EXPIRY = None
        self.assertEqual(
            appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-01-05"), [])

    def test_an_unreadable_date_is_REPORTED_rather_than_ignored(self):
        """This test used to assert the opposite, and the opposite was a bug.

        "Ignored rather than fatal" sounds like robustness. It is not: an empty
        list here is exactly what the feed carries when there is nothing to warn
        about, so a mistyped date switched off the alarm and looked like calm.
        And this particular alarm guards the AWS account holding the Stream
        queues, which fails by going quiet — the queues lapse, Stream stops, and
        Amazon carries on reporting the subscription ACTIVE.

        Not fatal is right. Silent is not. It says what it could not read.
        Found by review, 2026-08-23.
        """
        real = stream_config.AWS_PLAN_EXPIRY
        self.addCleanup(setattr, stream_config, "AWS_PLAN_EXPIRY", real)
        stream_config.AWS_PLAN_EXPIRY = "next February"
        got = appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-01-05")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "guard_check_failed")
        self.assertIn("next February", got[0]["message"],
                      "it must name the value it could not read")
        self.assertIn("ValueError", got[0]["key"],
                      "the exception type in the key means a persistent fault "
                      "alerts once, not on every poll")

    def test_an_unreadable_date_never_raises(self):
        """Not fatal is still the other half: the alerts feed must return."""
        real = stream_config.AWS_PLAN_EXPIRY
        self.addCleanup(setattr, stream_config, "AWS_PLAN_EXPIRY", real)
        for bad in ("next February", "2026/12/31", 20261231, "", "  "):
            with self.subTest(value=bad):
                stream_config.AWS_PLAN_EXPIRY = bad
                appctl._aws_plan_expiry_alerts(markets.DEFAULT, today="2027-01-05")

    def test_the_shipped_date_is_the_one_the_operator_gave(self):
        self.assertEqual(stream_config.AWS_PLAN_EXPIRY, EXPIRY)
