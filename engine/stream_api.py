#!/usr/bin/env python3
"""The Amazon Ads half of Marketing Stream: subscriptions.

A subscription is a standing instruction — "for THIS advertising profile, push
THIS dataset to THIS queue, hourly, from now on". There is one per (market,
dataset). Creating one is the only write in this file; everything else reads.

Access was verified live on 2026-08-21: GET /streams/subscriptions answers 200
on US, UK, DE and USKDP with the existing credentials, so no separate Amazon
application is needed. Being allowed to LIST is not proof that a Merch/POD
profile may CREATE — that is settled by the first create, not by this comment.

Stream sends nothing about the past. A subscription starts the clock; it cannot
backfill. So this never replaces phase0_pull.py, it sits beside it.
"""

import json
import uuid

import requests

import ads_client
import markets
import stream_config

SUBSCRIPTION_CT = ("application/vnd.MarketingStreamSubscriptions."
                   "StreamSubscriptionResource.v1.0+json")

# A subscription Amazon has accepted but not yet wired up reports PENDING. It
# turns ACTIVE only after the SNS confirmation in our queue is answered — which
# stream_drain.py does — so PENDING that never moves is the signature of a queue
# nobody is draining.
PENDING = "PENDING"
ACTIVE = "ACTIVE"
ARCHIVED = "ARCHIVED"


class StreamAPI:
    """Subscription CRUD for one market."""

    def __init__(self, market=None):
        self.market = market or markets.current()
        self.client = ads_client.AdsClient(self.market)
        self.realm = stream_config.realm_for_endpoint(self.client.base)
        self.region = stream_config.REALM_REGION[self.realm]

    # ---- reads -----------------------------------------------------------
    def list_subscriptions(self):
        r = requests.get(self.client.base + "/streams/subscriptions",
                         headers=self.client._headers(SUBSCRIPTION_CT), timeout=60)
        return _unwrap(r).get("subscriptions", [])

    def get_subscription(self, subscription_id):
        r = requests.get(f"{self.client.base}/streams/subscriptions/{subscription_id}",
                         headers=self.client._headers(SUBSCRIPTION_CT), timeout=60)
        return _unwrap(r)

    def find(self, dataset):
        """The live (non-archived) subscription for one dataset, or None."""
        for s in self.list_subscriptions():
            if s.get("dataSetId") == dataset and s.get("status") != ARCHIVED:
                return s
        return None

    # ---- writes ----------------------------------------------------------
    def create_subscription(self, dataset, destination_arn, notes=""):
        """Start the hourly push for one dataset into one queue.

        Refuses a duplicate rather than creating a second subscription to the
        same dataset: two subscriptions mean every row arrives twice, and the
        drain would happily bank both.
        """
        if dataset not in stream_config.DATASETS:
            raise ValueError(f"Unknown dataset {dataset!r}. "
                             f"Known: {', '.join(stream_config.DATASETS)}")
        existing = self.find(dataset)
        if existing:
            raise ValueError(
                f"{self.market} is already subscribed to {dataset} "
                f"(subscription {existing.get('subscriptionId')}, "
                f"status {existing.get('status')}). Archive it first to change queue.")
        # clientRequestToken is REQUIRED and Amazon rejects the create without
        # it — "Value null at 'clientRequestToken' failed to satisfy
        # constraint". It is an idempotency key: the same token replayed does
        # not create a second subscription. A fresh random one per call is right
        # here, because create_subscription already refuses a duplicate by
        # looking one up first, and a token reused across an archive-and-recreate
        # would collide with the old request.
        body = {"dataSetId": dataset, "destinationArn": destination_arn,
                "clientRequestToken": uuid.uuid4().hex,
                "notes": notes or f"MerchAds {self.market} {dataset}"}
        r = self.client._send_retry("POST", "/streams/subscriptions",
                                    SUBSCRIPTION_CT, body)
        return _unwrap(r)

    def archive_subscription(self, subscription_id):
        """Stop the push. Amazon has no delete — ARCHIVED is the off switch."""
        r = self.client._send_retry("PUT", f"/streams/subscriptions/{subscription_id}",
                                    SUBSCRIPTION_CT, {"status": ARCHIVED})
        return _unwrap(r)


def _unwrap(resp):
    """One shape for every reply, and a readable sentence for every failure.

    Amazon's Stream errors come back in at least three shapes (`message`,
    `details`, bare text). Passing the raw body up would put an HTML error page
    into appctl's JSON envelope, so it is flattened here.
    """
    if resp is None:
        raise RuntimeError("Stream API: no response (connection dropped)")
    if resp.status_code >= 400:
        try:
            payload = resp.json()
            detail = payload.get("message") or payload.get("details") or json.dumps(payload)
        except ValueError:
            detail = (resp.text or "").strip()[:400]
        raise RuntimeError(f"Stream API HTTP {resp.status_code}: {detail}")
    if not (resp.text or "").strip():
        return {}
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(f"Stream API returned non-JSON: {resp.text[:200]!r}")
