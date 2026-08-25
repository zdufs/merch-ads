#!/usr/bin/env python3
"""Where Amazon Marketing Stream puts data, and where we pick it up.

Stream does not answer questions. It PUSHES rows into an SQS queue that we own,
about an hour after the hour they describe. So there are two halves to configure
and they live in different places:

  Amazon's half   a subscription per (profile, dataset), created through the
                  Ads API. See stream_api.py.
  Our half        an SQS queue per (realm, dataset), created once in AWS. Its
                  URL goes in .env and this module is the only reader of it.

ONE QUEUE SERVES A WHOLE REALM. The subscription is per profile, but the
destination is just a queue — UK, DE, FR, ES and IT can all point at the same EU
queue, and their messages arrive mixed together. US and KDP share the NA queue
the same way. That is why the drain has to sort messages out afterwards rather
than assuming the queue it read from names the market.

The publisher account ids below are Amazon's, copied verbatim from Amazon's own
CloudFormation template (amzn/ads-advanced-tools-docs, Stream_SQS_CF_Template).
Each dataset publishes from a DIFFERENT AWS account, which is the part that
surprises people: a queue policy written for sp-traffic silently drops every
sp-conversion message, and a dropped message is not retried or reported.
"""

import re
import urllib.parse

# The two datasets that matter for Sponsored Products performance. Amazon
# publishes many more (sb-*, sd-*, adsp-*); a Merch/POD account runs Sponsored
# Products only, so subscribing to the rest would buy noise.
TRAFFIC = "sp-traffic"
CONVERSION = "sp-conversion"
DATASETS = (TRAFFIC, CONVERSION)

# Realm is Amazon Ads' word for API region. It decides BOTH the Ads API host and
# the AWS region the queue has to live in — a NA subscription cannot deliver to
# an eu-west-1 queue.
REALM_REGION = {"NA": "us-east-1", "EU": "eu-west-1", "FE": "us-west-2"}

# Amazon's publishing accounts, per realm, per dataset. Verbatim from Amazon's
# CloudFormation template. Only the two SP datasets are kept; adding another
# means copying its account id from the same template, never guessing.
PUBLISHER_ACCOUNT = {
    "NA": {TRAFFIC: "906013806264", CONVERSION: "802324068763"},
    "EU": {TRAFFIC: "668473351658", CONVERSION: "562877083794"},
    "FE": {TRAFFIC: "074266271188", CONVERSION: "622939981599"},
}

# Amazon validates a destination queue before it will activate a subscription,
# and it does that from this role. Without the GetQueueAttributes grant the
# subscription is rejected and the reason is not obvious.
REVIEWER_ROLE_ARN = "arn:aws:iam::926844853897:role/ReviewerRole"


# --- the AWS account's own clock -------------------------------------------
# Stream's queues live in an AWS account opened 2026-08-21 on the FREE plan,
# which auto-closes six months in unless it is upgraded to a paid plan. The bill
# for these queues is about nothing either way, so this is paperwork rather than
# cost — and it is the most dangerous kind of deadline, because of HOW it fails.
#
# If the account lapses the queues go, Stream stops arriving, and Amazon carries
# on reporting the subscription ACTIVE. Every screen keeps working. The Dashboard
# simply shows a day that got quieter, and the drop is indistinguishable from a
# slow sales week until someone compares against the nightly report.
#
# So it is an alert with two months of warning, not a note in a document nobody
# re-reads. Change the date if the account is upgraded or replaced; set it to
# None to switch the warning off deliberately.
AWS_PLAN_EXPIRY = "2027-02-21"

# How early to start saying so. Two months is enough to do something about it
# without the alert becoming furniture.
AWS_PLAN_WARN_DAYS = 60


_QUEUE_URL_RE = re.compile(
    r"^https://sqs\.(?P<region>[a-z0-9-]+)\.amazonaws\.com/(?P<account>\d{12})/(?P<name>[\w.-]+)$")


def realm_for_endpoint(endpoint):
    """NA or EU, from the Ads API host a market already carries."""
    host = urllib.parse.urlsplit(endpoint).netloc
    if "-eu." in host or host.endswith("-eu.amazon.com"):
        return "EU"
    if "-fe." in host:
        return "FE"
    return "NA"


def env_key(realm, dataset):
    """The .env name holding one queue URL, e.g. STREAM_QUEUE_NA_SP_TRAFFIC."""
    return f"STREAM_QUEUE_{realm}_{dataset.replace('-', '_').upper()}"


def queue_url(env, realm, dataset):
    return (env.get(env_key(realm, dataset)) or "").strip()


def parse_queue_url(url):
    """{region, account, name, arn} from an SQS queue URL.

    The operator pastes ONE string — the queue URL straight out of the AWS
    console — and everything else is derived. Asking for the ARN separately is
    an invitation to paste a queue's URL beside another queue's ARN, and the
    subscription would then be created against a queue nothing drains.
    """
    m = _QUEUE_URL_RE.match((url or "").strip())
    if not m:
        raise ValueError(
            f"Not an SQS queue URL: {url!r}. Expected the form "
            "https://sqs.<region>.amazonaws.com/<12-digit-account>/<queue-name>")
    d = m.groupdict()
    d["arn"] = f"arn:aws:sqs:{d['region']}:{d['account']}:{d['name']}"
    return d


def aws_keys(env):
    """(access_key, secret_key) or None when AWS is not configured yet.

    Returning None rather than raising lets every read-only screen say "Stream is
    not set up" instead of failing, while the drain — which cannot do anything
    useful without keys — checks and stops.
    """
    a = (env.get("AWS_ACCESS_KEY_ID") or "").strip()
    s = (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    return (a, s) if a and s else None


def queue_policy(queue_arn, realm, dataset):
    """The SQS access policy for one (realm, dataset) queue, ready to paste.

    Generated rather than documented, because the publisher account differs per
    dataset and a hand-copied policy that names the wrong one fails silently:
    the subscription reports ACTIVE and no message ever arrives.
    """
    account = PUBLISHER_ACCOUNT[realm][dataset]
    region = REALM_REGION[realm]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowStreamSendMessage",
                "Effect": "Allow",
                "Principal": {"Service": "sns.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": queue_arn,
                "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:sns:{region}:{account}:*"}},
            },
            {
                "Sid": "AllowStreamReviewerGetQueueAttributes",
                "Effect": "Allow",
                "Principal": {"AWS": REVIEWER_ROLE_ARN},
                "Action": "sqs:GetQueueAttributes",
                "Resource": queue_arn,
            },
        ],
    }
