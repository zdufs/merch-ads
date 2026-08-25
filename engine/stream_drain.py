#!/usr/bin/env python3
"""Drain the Marketing Stream queues into stream_data.sqlite.

    python3 engine/stream_drain.py                  # ~60s across every configured queue
    python3 engine/stream_drain.py --seconds 300    # longer pass
    python3 engine/stream_drain.py --realm NA       # one realm only
    python3 engine/stream_drain.py --status         # read-only: what is configured, what arrived

This is a READ against AWS and a WRITE to a local database. It never touches the
Amazon Ads account, so it is safe to run at any time — including while the
nightly is mid-flight.

It also answers the SNS handshake. A brand-new subscription parks a
`SubscriptionConfirmation` message in the queue and sends nothing else until
that Token is confirmed. Running this once after subscribing is what turns the
tap on; there is no separate step and no Lambda.

Messages are deleted from SQS only after they are committed locally. A crash
therefore costs a redelivery, which the message-id primary key absorbs, rather
than an hour of data that Stream will not send again.
"""

import argparse
import json
import sys
import time

import ads_client
import stream_config
import stream_sqs
import stream_store


def _first(mapping, *names):
    """First present, non-empty value among several possible key spellings.

    Amazon's datasets are not consistent about snake_case vs camelCase between
    versions, and a field that is simply absent must stay absent — hence None
    rather than a default.
    """
    for n in names:
        v = mapping.get(n)
        if v not in (None, ""):
            return str(v)
    return None


def parse_message(raw_body, dataset, realm):
    """Classify one SQS message body.

    Returns (kind, data):
      "confirm"      data = {"topic_arn":…, "token":…}
      "notification" data = a LIST of rows ready for stream_store.store_messages
                     (one per record: a notification may carry several)
      "unknown"      data = a one-row LIST, banked verbatim under dataset "unknown"

    Raw-delivery is handled too: if SNS raw message delivery is ever switched on
    for a topic, the body IS the payload with no envelope around it. Guessing
    wrong there would bank an empty payload for every row of a real hour.
    """
    try:
        envelope = json.loads(raw_body)
    except ValueError:
        # A list, like every other notification result, so the drain loop has
        # one shape to handle. An unparseable body is still banked verbatim.
        return "unknown", [{"payload": raw_body, "dataset": "unknown",
                            "realm": realm}]

    if not isinstance(envelope, dict) or "Type" not in envelope:
        # No SNS envelope — the body is the dataset payload itself.
        inner = envelope
        return "notification", _rows(None, dataset, realm, None, None, inner)

    kind = envelope.get("Type")
    if kind == "SubscriptionConfirmation":
        return "confirm", {"topic_arn": envelope.get("TopicArn"),
                           "token": envelope.get("Token")}
    if kind != "Notification":
        # A LIST, like the other two "unknown" returns and like every
        # notification result. This one branch returned a bare dict, so the
        # drain loop iterated its string KEYS and called .get() on a str:
        # `AttributeError: 'str' object has no attribute 'get'`. The message is
        # not deleted when the drain raises, so one UnsubscribeConfirmation
        # parked in the queue could abort every later drain and Stream would
        # quietly stop arriving.
        return "unknown", [{"payload": raw_body, "dataset": "unknown", "realm": realm,
                            "message_id": envelope.get("MessageId"),
                            "topic_arn": envelope.get("TopicArn")}]

    body = envelope.get("Message")
    try:
        inner = json.loads(body) if isinstance(body, str) else body
    except ValueError:
        inner = body
    return "notification", _rows(envelope.get("MessageId"), dataset, realm,
                                 envelope.get("TopicArn"), envelope.get("Timestamp"),
                                 inner)


def _rows(message_id, dataset, realm, topic_arn, published_at, inner):
    """One banked row per RECORD, not per SQS message.

    Amazon may put several records in one notification. The old code kept the
    array whole and identified the message by its first row, which meant every
    downstream query — all of which read `json_extract(payload,'$.advertiser_id')`
    and friends — got NULL for the entire message. The message still counted
    towards the banked total and towards coverage, so Stream health went UP by
    one while both records vanished from every figure on the panel. That is the
    exact failure this pipeline is built to refuse: an undercount that stays
    internally consistent all the way to the screen.

    Each record keeps its own row and its own id, suffixed by position, so the
    message_id primary key still de-duplicates a redelivered SQS message. The
    per-record dedupe that matters — idempotency_id for traffic, the natural
    grain for conversions — happens later in stream_map and is unaffected.

    No real message has ever been an array: 45,801 banked on 2026-08-23, none of
    them. This is a hole, not an incident.
    """
    if isinstance(inner, list):
        records = [r for r in inner if isinstance(r, dict)]
        if not records:
            return [_one(message_id, dataset, realm, topic_arn, published_at, inner)]
        if len(records) == 1:
            return [_one(message_id, dataset, realm, topic_arn, published_at, records[0])]
        return [_one(f"{message_id}#{i}" if message_id else None,
                     dataset, realm, topic_arn, published_at, r)
                for i, r in enumerate(records)]
    return [_one(message_id, dataset, realm, topic_arn, published_at, inner)]


def _one(message_id, dataset, realm, topic_arn, published_at, inner):
    record = inner if isinstance(inner, dict) else {}
    return {
        "message_id": message_id,
        "dataset": dataset,
        "realm": realm,
        "topic_arn": topic_arn,
        "published_at": published_at,
        "time_window_start": _first(record, "time_window_start", "timeWindowStart"),
        "profile_id": _first(record, "profile_id", "profileId"),
        "payload": json.dumps(inner) if not isinstance(inner, str) else inner,
    }


def configured_queues(env, realm_filter=None):
    """[(realm, dataset, queue_url, region)] for every queue named in .env."""
    out = []
    for realm in ("NA", "EU", "FE"):
        if realm_filter and realm != realm_filter:
            continue
        for dataset in stream_config.DATASETS:
            url = stream_config.queue_url(env, realm, dataset)
            if not url:
                continue
            info = stream_config.parse_queue_url(url)
            out.append((realm, dataset, url, info["region"]))
    return out


def drain_queue(conn, keys, realm, dataset, queue_url, region, budget_seconds,
                verbose=True):
    """Long-poll one queue until it is empty or the time budget runs out.

    The return value says WHICH of those two happened, because they mean
    opposite things. Emptying the queue is the healthy end. Running out of time
    means messages are arriving faster than this is reading them, and a backlog
    that grows every hour ends at SQS's retention limit with data Amazon will
    not resend. That failure is completely silent otherwise: every drain still
    reports a healthy count of messages banked.
    """
    deadline = time.monotonic() + max(1, budget_seconds)
    received = banked = duplicates = confirmations = 0
    empty_polls = 0

    while time.monotonic() < deadline and empty_polls < 2:
        remaining = max(0, int(deadline - time.monotonic()))
        messages = stream_sqs.receive(queue_url, keys, region,
                                      wait=min(stream_sqs.MAX_WAIT_SECONDS, remaining))
        if not messages:
            empty_polls += 1
            continue
        empty_polls = 0
        received += len(messages)

        rows, handles = [], []
        for m in messages:
            kind, data = parse_message(m.get("Body") or "", dataset, realm)
            if kind == "confirm":
                if data.get("token") and data.get("topic_arn"):
                    stream_sqs.confirm_subscription(data["topic_arn"], data["token"],
                                                    keys, region)
                    confirmations += 1
                    if verbose:
                        # STDERR even behind the flag. appctl passes
                        # verbose=False today, so this is safe by luck rather
                        # than by rule — and a future caller that passes True
                        # would break the envelope with no test to catch it.
                        print(f"  {realm}/{dataset}: SNS handshake confirmed — "
                              "data will start arriving within the hour",
                              file=sys.stderr)
                handles.append(m["ReceiptHandle"])
                continue
            # `data` is a LIST of rows now: one notification may carry several
            # records, and each has to be banked on its own or its numbers reach
            # nothing. A single-record message is a list of one, so the common
            # path is unchanged.
            for i, row in enumerate(data):
                # Fall back to the SQS message id when the envelope carried none,
                # so the dedupe key is never null. The suffix keeps a multi-record
                # message from collapsing onto one primary key.
                if not row.get("message_id"):
                    mid = m.get("MessageId")
                    row["message_id"] = (f"{mid}#{i}" if mid and len(data) > 1
                                         else mid)
                row.setdefault("realm", realm)
                rows.append(row)
            handles.append(m["ReceiptHandle"])

        if rows:
            b, d = stream_store.store_messages(conn, rows)
            banked += b
            duplicates += d
        # Deleted only now, after the commit above.
        for i in range(0, len(handles), 10):
            stream_sqs.delete_batch(queue_url, keys, region, handles[i:i + 10])

    # Two empty polls in a row is the only proof the queue is actually empty;
    # anything else means the clock stopped us with messages still waiting.
    exhausted = empty_polls >= 2
    note = "" if exhausted else f"time budget {budget_seconds}s ran out, queue not empty"
    stream_store.log_drain(conn, realm, dataset, received, banked, duplicates,
                           confirmations, note=note)
    return {"realm": realm, "dataset": dataset, "received": received,
            "banked": banked, "duplicates": duplicates,
            "confirmations": confirmations, "exhausted": exhausted}


def status(env):
    """Read-only: what is configured, what is in the queues, what is banked."""
    keys = stream_config.aws_keys(env)
    queues = []
    for realm, dataset, url, region in configured_queues(env):
        entry = {"realm": realm, "dataset": dataset, "region": region,
                 "queue": stream_config.parse_queue_url(url)["name"]}
        if keys:
            try:
                entry.update(stream_sqs.queue_depth(url, keys, region))
            except Exception as e:
                entry["error"] = str(e)
        queues.append(entry)
    conn = stream_store.connect(ro=True)
    banked = stream_store.coverage(conn) if conn else []
    if conn:
        conn.close()
    return {"aws_configured": bool(keys), "queues": queues, "banked": banked}


def main():
    ap = argparse.ArgumentParser(description="Drain Amazon Marketing Stream queues")
    # 60 was the original budget and it was too small. Stream sends roughly one
    # message per impression, so a day of ~25,000 impressions is thousands of
    # messages, and SQS hands them over about ten at a time. 60 seconds read
    # about 480 of them — under one hour's arrivals, so the backlog only ever
    # grew. The loop still exits the moment the queue is empty, so a quiet hour
    # costs about 40 seconds either way; this only changes what a BUSY hour can
    # finish.
    ap.add_argument("--seconds", type=int, default=300,
                    help="time budget PER QUEUE (default 300)")
    ap.add_argument("--realm", choices=["NA", "EU", "FE"],
                    help="limit to one realm")
    ap.add_argument("--status", action="store_true",
                    help="read-only summary, drain nothing")
    args = ap.parse_args()

    env = ads_client.load_env()
    if args.status:
        print(json.dumps(status(env), indent=2))
        return

    keys = stream_config.aws_keys(env)
    if not keys:
        raise SystemExit(
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not in .env, so there "
            "is no way to read the queue. See docs/marketing-stream.md.")

    queues = configured_queues(env, args.realm)
    if not queues:
        raise SystemExit(
            "No STREAM_QUEUE_* entries in .env — nothing to drain. "
            "See docs/marketing-stream.md for the two lines to add.")

    conn = stream_store.connect()
    totals = {"received": 0, "banked": 0, "duplicates": 0, "confirmations": 0}
    unfinished = []
    for realm, dataset, url, region in queues:
        print(f"draining {realm}/{dataset} ({region}) for up to {args.seconds}s")
        summary = drain_queue(conn, keys, realm, dataset, url, region, args.seconds)
        for k in totals:
            totals[k] += summary[k]
        print(f"  received {summary['received']}, banked {summary['banked']}, "
              f"duplicate {summary['duplicates']}")
        if not summary["exhausted"]:
            unfinished.append(f"{realm}/{dataset}")
            print(f"  WARNING: {realm}/{dataset} still had messages when the "
                  f"{args.seconds}s budget ran out.")
    conn.close()

    print(f"\ntotal: banked {totals['banked']} new messages "
          f"({totals['duplicates']} already had), "
          f"{totals['confirmations']} handshake(s) confirmed")
    if unfinished:
        print(f"\nBACKLOG: {', '.join(unfinished)} did not drain to empty. Messages "
              "are arriving faster than this job reads them, so today's totals are "
              "an undercount and the backlog will keep growing. Raise --seconds, or "
              "run the drain more often than hourly.")
    if totals["banked"] == 0 and totals["confirmations"] == 0:
        print("Nothing arrived. Stream publishes about an hour behind the hour it "
              "describes, so an empty first pass right after subscribing is normal.")


if __name__ == "__main__":
    sys.exit(main())
