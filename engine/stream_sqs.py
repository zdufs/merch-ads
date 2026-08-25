#!/usr/bin/env python3
"""The AWS half of Marketing Stream: read the queue, answer the handshake.

Three operations, signed by aws_sigv4:

  SQS ReceiveMessage       long-poll for up to 10 messages
  SQS DeleteMessageBatch   acknowledge the ones we banked
  SNS ConfirmSubscription  answer the one-time handshake

THE HANDSHAKE IS NOT OPTIONAL. When a Stream subscription is created, Amazon's
SNS topic drops a `SubscriptionConfirmation` message into the queue carrying a
Token. Until something calls ConfirmSubscription with that Token, the topic is
not really subscribed and NO DATA EVER ARRIVES. The subscription sits at
PENDING and everything looks fine. Amazon's own reference implementation spends
a Lambda, a second queue and a CDK stack on this; it is four lines here because
our drain is a process we already run.

A message is deleted only AFTER it is safely banked. SQS redelivers anything
not deleted before its visibility timeout expires, so a crash mid-batch costs a
retry, never a row.
"""

import json
import sys
import urllib.parse

import requests

import aws_sigv4

JSON_CT = "application/x-amz-json-1.0"
FORM_CT = "application/x-www-form-urlencoded; charset=utf-8"

# Long-poll ceiling. AWS rejects anything above 20.
MAX_WAIT_SECONDS = 20
# How long a received message stays invisible to other readers while we bank it.
# Generous on purpose: a slow SQLite write must not make SQS hand the same
# message to the next poll and bank it twice.
VISIBILITY_TIMEOUT = 120


def _sqs_call(target, queue_url, payload, keys, region, timeout):
    endpoint = f"https://sqs.{region}.amazonaws.com/"
    body = json.dumps(dict(payload, QueueUrl=queue_url))
    headers = aws_sigv4.sign(
        "POST", endpoint, region, "sqs", keys[0], keys[1], body=body,
        headers={"Content-Type": JSON_CT, "X-Amz-Target": f"AmazonSQS.{target}"})
    resp = requests.post(endpoint, data=body.encode("utf-8"), headers=headers,
                         timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"SQS {target} HTTP {resp.status_code}: "
                           f"{(resp.text or '').strip()[:300]}")
    return resp.json() if (resp.text or "").strip() else {}


def receive(queue_url, keys, region, max_messages=10, wait=MAX_WAIT_SECONDS):
    """Long-poll one queue. Returns a list of {MessageId, ReceiptHandle, Body}.

    An empty list means the queue is empty right now, which after a long poll is
    real information: there is nothing pending, not "we did not wait".
    """
    wait = max(0, min(int(wait), MAX_WAIT_SECONDS))
    data = _sqs_call("ReceiveMessage", queue_url, {
        "MaxNumberOfMessages": max(1, min(int(max_messages), 10)),
        "WaitTimeSeconds": wait,
        "VisibilityTimeout": VISIBILITY_TIMEOUT,
    }, keys, region, timeout=wait + 30)
    return data.get("Messages") or []


def delete_batch(queue_url, keys, region, receipt_handles):
    """Acknowledge up to 10 messages. Returns the count actually deleted."""
    handles = list(receipt_handles)[:10]
    if not handles:
        return 0
    entries = [{"Id": str(i), "ReceiptHandle": h} for i, h in enumerate(handles)]
    data = _sqs_call("DeleteMessageBatch", queue_url, {"Entries": entries},
                     keys, region, timeout=30)
    failed = data.get("Failed") or []
    if failed:
        # Not fatal: an undeleted message simply reappears and is skipped by the
        # message-id dedupe. Worth saying out loud though — a persistent failure
        # here means the queue grows without bound.
        print(f"  SQS delete: {len(failed)} of {len(handles)} not acknowledged", file=sys.stderr)
    return len(data.get("Successful") or [])


def queue_depth(queue_url, keys, region):
    """{visible, in_flight} — the operator-facing "is anything arriving?" number."""
    data = _sqs_call("GetQueueAttributes", queue_url, {
        "AttributeNames": ["ApproximateNumberOfMessages",
                           "ApproximateNumberOfMessagesNotVisible"]},
        keys, region, timeout=30)
    attrs = data.get("Attributes") or {}
    return {"visible": int(attrs.get("ApproximateNumberOfMessages", 0) or 0),
            "in_flight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0) or 0)}


def confirm_subscription(topic_arn, token, keys, region):
    """Answer the SNS handshake. Without this, the queue stays empty forever.

    SNS speaks the older query protocol, not JSON — hence the form body. The
    Token is single-use and expires after a few days; if it does, the fix is to
    archive the Stream subscription and create it again.
    """
    endpoint = f"https://sns.{region}.amazonaws.com/"
    body = urllib.parse.urlencode({
        "Action": "ConfirmSubscription",
        "TopicArn": topic_arn,
        "Token": token,
        "Version": "2010-03-31",
    })
    headers = aws_sigv4.sign("POST", endpoint, region, "sns", keys[0], keys[1],
                             body=body, headers={"Content-Type": FORM_CT})
    resp = requests.post(endpoint, data=body.encode("utf-8"), headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"SNS ConfirmSubscription HTTP {resp.status_code}: "
                           f"{(resp.text or '').strip()[:300]}")
    return True
