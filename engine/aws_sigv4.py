#!/usr/bin/env python3
"""AWS Signature Version 4 — the whole of it, in the standard library.

WHY NOT boto3. The Mac app ships its own CPython at
``Contents/Resources/python`` and that interpreter carries exactly one
third-party package: ``requests``. The point of the bundle is that a bare Mac
with no Homebrew and no pip can run the engine. botocore would add tens of
megabytes of service models to it, and the Stream work needs precisely three
AWS operations: SQS ReceiveMessage, SQS DeleteMessageBatch, and SNS
ConfirmSubscription. Signing those is this file.

Correctness is NOT taken on trust. ``tests/stream_tests.py`` runs AWS's own
published "get-vanilla" vector from the Signature Version 4 test suite through
``sign()`` and compares the canonical request, the string to sign and the
finished Authorization header byte for byte. If this file ever drifts, that
test says so before any credential reaches Amazon.

Nothing here ever prints a key. The secret is used to derive a signing key and
is never placed in a header, a log line or an exception message.
"""

import datetime
import hashlib
import hmac
import urllib.parse

ALGORITHM = "AWS4-HMAC-SHA256"

# An unsigned-payload request still hashes an empty body, so this constant shows
# up in every GET canonical request.
EMPTY_PAYLOAD_HASH = hashlib.sha256(b"").hexdigest()


def _sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key, datestamp, region, service):
    """The four-step derived key. Scoped to one day, one region, one service, so
    a leaked signing key expires on its own — that is the whole idea of SigV4."""
    k = _hmac(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def canonical_query(query):
    """Sort and re-encode the query string the way SigV4 requires.

    The sort is on the ENCODED key and value, not the raw ones, and the safe set
    is deliberately narrow (RFC 3986 unreserved). urllib's default safe set
    keeps "/" unescaped, which would sign a different string than AWS computes.
    """
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    encoded = sorted(
        (urllib.parse.quote(str(k), safe="-_.~"), urllib.parse.quote(str(v), safe="-_.~"))
        for k, v in pairs
    )
    return "&".join(f"{k}={v}" for k, v in encoded)


def canonical_request(method, url, headers, body):
    """Return (canonical_request_text, signed_headers). Headers arrive already
    lowercased and stripped — see sign()."""
    parts = urllib.parse.urlsplit(url)
    # Empty path means "/" to AWS. The path is signed already-encoded, so quote
    # only what a bare path could legally contain.
    path = urllib.parse.quote(parts.path or "/", safe="/-_.~")
    names = sorted(headers)
    canonical_headers = "".join(f"{n}:{headers[n]}\n" for n in names)
    signed_headers = ";".join(names)
    text = "\n".join([
        method.upper(),
        path,
        canonical_query(parts.query),
        canonical_headers,
        signed_headers,
        _sha256_hex(body),
    ])
    return text, signed_headers


def sign(method, url, region, service, access_key, secret_key,
         body=b"", headers=None, session_token=None, now=None):
    """Return the headers to SEND: whatever was passed in, plus the auth ones.

    ``now`` is injectable so the test vector can pin 2015-08-30 — never pass it
    in production, where it must be the real clock (AWS rejects a signature more
    than five minutes out).
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    host = urllib.parse.urlsplit(url).netloc

    # What gets signed: lowercase names, collapsed values. What gets sent must
    # carry the SAME values, so the outgoing dict is rebuilt from these.
    to_sign = {k.lower(): " ".join(str(v).split()) for k, v in (headers or {}).items()}
    to_sign["host"] = host
    to_sign["x-amz-date"] = amz_date
    if session_token:
        to_sign["x-amz-security-token"] = session_token

    creq, signed_headers = canonical_request(method, url, to_sign, body)
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([ALGORITHM, amz_date, scope, _sha256_hex(creq)])
    signature = hmac.new(signing_key(secret_key, datestamp, region, service),
                         string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = {k: v for k, v in to_sign.items() if k != "host"}
    out["Authorization"] = (f"{ALGORITHM} Credential={access_key}/{scope}, "
                            f"SignedHeaders={signed_headers}, Signature={signature}")
    return out


def parts_for_test(method, url, region, service, access_key, secret_key,
                   body=b"", headers=None, now=None):
    """The three intermediate strings, so a test can say WHICH step drifted.

    A wrong signature alone tells you nothing about where the mistake is; the
    canonical request is where nearly every SigV4 bug actually lives.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    to_sign = {k.lower(): " ".join(str(v).split()) for k, v in (headers or {}).items()}
    to_sign["host"] = urllib.parse.urlsplit(url).netloc
    to_sign["x-amz-date"] = amz_date
    creq, _ = canonical_request(method, url, to_sign, body)
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    sts = "\n".join([ALGORITHM, amz_date, scope, _sha256_hex(creq)])
    sig = hmac.new(signing_key(secret_key, datestamp, region, service),
                   sts.encode("utf-8"), hashlib.sha256).hexdigest()
    return creq, sts, sig
