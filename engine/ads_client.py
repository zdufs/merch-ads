#!/usr/bin/env python3
"""
Amazon Ads API client — Merch Ads automation.
Read-only helper used by Phase 0. Handles:
  - loading credentials from .env
  - refreshing the LWA access token (auto-renew)
  - authenticated calls to Sponsored Products v3 "list" endpoints (paginated)
  - the async Reporting v3 flow (create -> poll -> download -> parse)

US marketplace only (NA endpoint). Profile = US Merch (from .env).
"""

import gzip
import io
import json
import os
import sys

import paths
import time

import requests
import markets

ENV_PATH = os.path.join(paths.REPO_ROOT, ".env")

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


class CeilingReadError(RuntimeError):
    """The configured write ceiling could not be read safely."""


def load_env(path=ENV_PATH):
    """Read the credentials file. A MISSING one is a state, not an exception.

    `.env` is gitignored operator data and the app ships standalone, so a data
    folder may simply never have held one. The bare `FileNotFoundError` this
    used to raise came out two different ways, and both broke the same standing
    rule — never let a traceback reach the envelope:

      * IN-PROCESS (`stream-status`, `stream-setup`, `stream-drain`,
        `seasonal-apply`) the reply was
        `{"ok": false, "error": "[Errno 2] No such file or directory: '…/.env'"}`.
      * WRAPPED IN A SCRIPT (`status`, `backfill-daily`) the whole Python
        traceback was captured into the reply's `stderr` field, and `code: 1`
        was the only sign anything was wrong.

    A string SystemExit fixes both at the source: appctl's dispatcher turns it
    into a one-sentence envelope, and a wrapped script prints that sentence and
    exits 1 with no stack. Never read or print the file's CONTENTS — the path is
    what the reader needs to fix this, and the secrets stay unread.
    """
    env = {}
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            f"No .env found at {path} — Amazon credentials are not configured "
            f"yet, so nothing can talk to the Ads API. Copy .env.example to "
            f".env there and fill in your client id, secret, refresh token and "
            f"profile ids.")
    except PermissionError:
        raise SystemExit(f"Cannot read {path} — permission denied.")
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class AdsClient:

    # Class-level defaults so the budget exists on EVERY instance, including the
    # ones tests build without __init__. Reading them with getattr and a default
    # would have been the other option, and the wrong one: that turns a client
    # that somehow lacks the attribute into a client with no budget, silently.
    _auto = False
    _written = 0
    _write_cap = None
    _built = 0
    _build_cap = None
    _is_builder = False
    def __init__(self, market=None):
        self.env = load_env()
        self.client_id = self.env["AMZN_ADS_CLIENT_ID"]
        self.client_secret = self.env["AMZN_ADS_CLIENT_SECRET"]
        self.refresh_token = self.env["AMZN_ADS_REFRESH_TOKEN"]
        # market selects the profile + API region (default US = NA endpoint, unchanged)
        self.market = market or markets.current()
        mc = markets.cfg(self.market)
        self.base = mc["endpoint"]
        prof = self.env.get(mc["profile_env"], "").strip()
        if not prof:
            raise SystemExit(f"No {mc['profile_env']} in .env for market {self.market}.")
        self.profile_id = prof
        self._access_token = None
        self._token_expiry = 0
        self._ceilings = {}          # lazy per-surface bid-ceiling cache
        self.last_clamps = []        # populated per bid-write call
        # VOLUME BACKSTOP for unattended runs. The rules engine has its own cap
        # and it is better, because it counts the whole plan first and refuses
        # all-or-nothing. But that cap guards the DSL alone, and eight other
        # scripts run --apply --auto every night: phase2, phase3, preempt,
        # seasonal, harvest_prune, the two harvest builders and the two campaign
        # builders. None of them counted anything, so a threshold one character
        # too loose in any of them writes until it runs out of rows.
        #
        # This cannot refuse up front — at this layer the total is unknown — so
        # it is a STOP, not a plan check: past the cap the run raises and exits
        # non-zero, which the nightly's step tracker reports. Stopping at 500 in
        # an unattended run beats continuing to fifty thousand.
        #
        # It applies only to --auto. An operator-run bulk repair is deliberate
        # and supervised: rebid_clauses legitimately wrote 9,648 clauses once.
        #
        # There are TWO budgets, because 500 was measured over one of them. See
        # db.AUTO_BUILD_CAP_DEFAULT: counting the campaign builders against the
        # change cap stopped a legitimate US build at 475 of about 700 product
        # ads on the first night this guard was live.
        self._auto = "--auto" in sys.argv
        self._written = 0
        self._built = 0
        self.cap_stopped = None   # set when a write cap refuses a batch
        self._write_cap = None
        self._build_cap = None
        self._is_builder = False

    # ---- unattended write budget ------------------------------------------
    def declare_campaign_builder(self):
        """Say that this process is one of the two CAMPAIGN BUILDERS.

        Only lottery_build and scavenger_build. It is a declaration rather than
        something inferred, and it has to be, because the endpoints alone
        cannot tell the two cases apart: `phase4_harvest_create` and
        `phase4b_harvest_asins` also create ad groups, product ads and
        keywords, and they are threshold-driven — they promote what the harvest
        judged a winner. Those must keep the 500 cap the guard was measured
        for. The campaign builders instead enumerate the catalogue, and a
        normal night for them is thousands.

        A script that does not call this gets the STRICTER cap, so a builder
        added later stops loudly at 500 with a message naming the way out. That
        is the right way round: forgetting this costs one noisy night, and the
        opposite mistake would hand every future script a 50,000-write budget
        in silence.
        """
        self._is_builder = True

    def _surface(self, method, path):
        """Which budget this write counts against.

        "build" needs BOTH halves: the process declared itself a campaign
        builder, and the endpoint is one that POPULATES a campaign. Either half
        missing means "change".
        """
        if not self._is_builder:
            return "change"
        return "build" if (method.upper(), path) in _BUILD_ENDPOINTS else "change"

    def _load_caps(self):
        """Fill in whichever cap has not been read yet, from this market's DB.

        Only the missing ones: a cap already set on the instance was set
        deliberately, and re-reading it here would quietly overwrite it.
        """
        change = build = None
        try:
            import db
            conn = db.connect(ro=True)
            try:
                change = db.get_auto_change_cap(conn)
                build = db.get_auto_build_cap(conn)
            finally:
                conn.close()
        except Exception:
            # Not being able to read the settings is not permission to run
            # uncapped, so fall back to the shipped numbers, never to 0.
            change, build = db.AUTO_CHANGE_CAP_DEFAULT, db.AUTO_BUILD_CAP_DEFAULT
        if self._write_cap is None:
            self._write_cap = change
        if self._build_cap is None:
            self._build_cap = build

    def _budget(self, n, surface="change"):
        """Count n entity writes, and stop the run if an automatic one runs away.

        `surface` is "build" for entities created INSIDE a campaign — ad groups,
        product ads, keywords, targeting clauses — and "change" for everything
        else, including creating a campaign. They have separate budgets because
        they have separate normal volumes: a busy change night is tens of
        writes, a busy build night is thousands.
        """
        if not self._auto or not n:
            return
        build = surface == "build"
        if (self._build_cap if build else self._write_cap) is None:
            self._load_caps()
        cap = self._build_cap if build else self._write_cap
        done = self._built if build else self._written
        # CHECK, then count. This used to add first and report `self._written`,
        # which includes the batch that was refused — so a 600-entity request
        # stopped after 500 and announced "has written 600".
        if cap and done + n > cap:
            what = "created" if build else "written"
            knob = "--set-build" if build else "--set"
            self.cap_stopped = {"written": done, "cap": cap, "refused": n,
                                "market": self.market, "surface": surface}
            raise SystemExit(
                f"STOPPED: this automatic run has {what} {done} entities "
                f"in {self.market} and the next batch of {n} would pass the "
                f"{cap}-write limit. That batch was NOT sent.\n"
                f"The {done} already sent are ON THE ACCOUNT. This stop "
                f"unwinds past the calling method, so its collected batch results "
                f"are lost and those writes may be missing from writes_log and "
                f"from the local mirror — reconcile against Amazon before "
                f"assuming the audit trail is complete.\n"
                f"This is either a threshold matching far more than it was meant "
                f"to, or a real backlog that deserves a human. Raise the limit "
                f"with `appctl change-cap {knob} N` if it is the latter.")
        if build:
            self._built += n
        else:
            self._written += n

    # ---- auth -------------------------------------------------------------
    def access_token(self):
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        # The LWA token endpoint occasionally drops the connection mid-handshake
        # (RemoteDisconnected). A single blip used to fail a whole nightly step —
        # US preempt_negatives died this way (Aug 2026) because the token fetch,
        # unlike the data calls in _send_retry, had no retry. Retry transient
        # transport errors and 5xx (the fetch is idempotent); let a 4xx (a
        # bad or expired refresh token) raise at once — retrying that is pointless.
        for attempt in range(5):
            last = attempt == 4
            try:
                resp = requests.post(
                    LWA_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self.refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    timeout=30,
                )
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if last:
                    raise
            else:
                if resp.status_code < 500 or last:
                    resp.raise_for_status()
                    data = resp.json()
                    self._access_token = data["access_token"]
                    self._token_expiry = time.time() + int(data.get("expires_in", 3600))
                    return self._access_token
            wait = min(40, 5 * (2 ** attempt))   # 5,10,20,40
            print(f"  LWA token fetch failed — retrying in {wait}s "
                  f"(attempt {attempt + 1}/5)", file=sys.stderr)
            time.sleep(wait)
        raise RuntimeError("LWA token fetch: exhausted retries")   # unreachable

    def _headers(self, content_type=None):
        h = {
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Amazon-Advertising-API-Scope": self.profile_id,
            "Authorization": f"Bearer {self.access_token()}",
        }
        if content_type:
            h["Content-Type"] = content_type
            h["Accept"] = content_type
        return h

    # ---- shared retry wrapper (429 + 5xx with exponential backoff) --------
    def _send_retry(self, method, path, ct, payload, tries=5, retry_transport=False):
        """POST/PUT with backoff on 429 / 5xx. Returns the final response.

        retry_transport also retries a transient connection drop
        (RemoteDisconnected / timeout) — the same class of failure the token
        fetch guards against. It is OFF by default and turned on only for
        idempotent READS (list_all): re-fetching a list has no side effect. A
        mutating POST/PUT must NOT auto-retry a drop, because the server may have
        processed it before the socket closed and a re-send would double-apply.
        """
        # Count the entities this request would change, BEFORE sending. Hooking
        # here rather than in each of the eighteen write methods means a method
        # added later is covered without anyone remembering to cover it — which
        # is how every guard in this engine that relies on a list has eventually
        # been missed.
        self._budget(_entity_count(method, path, payload),
                     self._surface(method, path))
        resp = None
        for attempt in range(tries):
            last = attempt == tries - 1
            try:
                resp = requests.request(method, self.base + path,
                                        headers=self._headers(ct),
                                        data=json.dumps(payload), timeout=60)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if not retry_transport or last:
                    raise
                wait = min(40, 5 * (2 ** attempt))   # 5,10,20,40
                print(f"  {path} connection dropped — retrying in {wait}s "
                      f"(attempt {attempt + 1}/{tries})", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            # A 5xx on a CREATE is ambiguous in the one direction that costs
            # money. 429 means Amazon refused the request outright, so retrying
            # is safe and is why this loop exists. A 500 or 502 means the request
            # may have been PROCESSED before the answer came back — and a create
            # carries no idempotency key, so a retry can open a second campaign
            # with its own budget, or a duplicate ad group nothing will ever
            # look at. The same reasoning already stops `retry_transport` on a
            # dropped socket for writes; it simply never covered a 5xx.
            #
            # Updates are different: setting a bid to the same value twice is
            # the same as setting it once, so those keep retrying.
            if resp.status_code >= 500 and _is_create(method, path):
                print(f"  {path} HTTP {resp.status_code} — NOT retried: a create "
                      f"may already have been processed and there is no "
                      f"idempotency key, so a retry could duplicate it. Re-run "
                      f"the builder; it reuses what already exists.",
                      file=sys.stderr)
                return resp
            if not last:
                wait = min(40, 5 * (2 ** attempt))   # 5,10,20,40
                print(f"  {path} HTTP {resp.status_code} — backing off {wait}s "
                      f"(attempt {attempt + 1}/{tries})", file=sys.stderr)
                time.sleep(wait)
        return resp

    # ---- SP v3 list endpoints (paginated via nextToken) -------------------
    def list_all(self, path, content_type, result_key, extra_body=None):
        """POST a /sp/.../list endpoint, following nextToken. Returns list.
        Retries on 429/5xx so a transient rate limit doesn't crash the run."""
        items = []
        body = dict(extra_body or {})
        body.setdefault("maxResults", 500)
        while True:
            resp = self._send_retry("POST", path, content_type, body, retry_transport=True)
            if resp.status_code != 200:
                raise RuntimeError(f"{path} -> HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            items.extend(data.get(result_key, []))
            token = data.get("nextToken")
            if not token:
                break
            body["nextToken"] = token
        return items

    # ---- Reporting v3 (async, split so it can be resumed) ----------------
    def create_report(self, report_type_id, columns, group_by, start_date, end_date,
                      time_unit="SUMMARY"):
        """Create an async report. Returns reportId immediately (generation happens server-side)."""
        ct = "application/vnd.createasyncreportrequest.v3+json"
        body = {
            "name": f"{report_type_id}-{start_date}-{end_date}",
            "startDate": start_date,
            "endDate": end_date,
            "configuration": {
                "adProduct": "SPONSORED_PRODUCTS",
                "groupBy": group_by,
                "columns": columns,
                "reportTypeId": report_type_id,
                "timeUnit": time_unit,
                "format": "GZIP_JSON",
            },
        }
        resp = requests.post(self.base + "/reporting/reports",
                             headers=self._headers(ct), data=json.dumps(body), timeout=60)
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"create {report_type_id} -> HTTP {resp.status_code}: {resp.text[:600]}")
        return resp.json()["reportId"]

    def get_report_info(self, report_id):
        """The report's whole metadata dict, as Amazon returns it.

        `get_report` threw everything away except status and url. Amazon echoes
        the startDate and endDate it actually built the report over, and that
        echo is the only thing that can say whether a window we asked for is
        the window we got — see docs/open-eu-trailing30-window.md. It costs no
        extra call: this is the same GET the poll loop already makes.
        """
        s = requests.get(self.base + f"/reporting/reports/{report_id}",
                         headers=self._headers(), timeout=60)
        s.raise_for_status()
        return s.json()

    def get_report(self, report_id):
        """Returns (status, url). url is set only when COMPLETED."""
        info = self.get_report_info(report_id)
        return info.get("status"), info.get("url")

    @staticmethod
    def download_gzip_json(url):
        r = requests.get(url, timeout=300)   # presigned S3 URL, no auth header
        r.raise_for_status()
        raw = gzip.GzipFile(fileobj=io.BytesIO(r.content)).read()
        return json.loads(raw)

    # ---- WRITES (used only by phase2_apply, behind a confirm gate) --------
    def _chunks(self, items, n=100):
        for i in range(0, len(items), n):
            yield items[i:i + n]

    def create_negative_keywords(self, items):
        """items: list of {campaignId, adGroupId, keywordText[, matchType]}.
        matchType defaults to NEGATIVE_EXACT; pass NEGATIVE_PHRASE for a phrase
        negative. Returns list of per-item result dicts, each with `created_ids`
        (one entry per input item, in order; None where that item errored). The
        ids are what makes a negative reversible — Undo deletes it by id."""
        ct = "application/vnd.spNegativeKeyword.v3+json"
        results = []
        for batch in self._chunks(items, 100):
            payload = {"negativeKeywords": [
                {"campaignId": str(it["campaignId"]),
                 "adGroupId": str(it["adGroupId"]),
                 "keywordText": it["keywordText"],
                 "matchType": it.get("matchType", "NEGATIVE_EXACT"),
                 "state": "ENABLED"} for it in batch]}
            resp = self._send_retry("POST", "/sp/negativeKeywords", ct, payload)
            r = _batch_result(resp, [it["keywordText"] for it in batch])
            id_map = _created_ids(r["body"], "negativeKeywordId")
            r["created_ids"] = [id_map.get(i) for i in range(len(batch))]
            results.append(r)
        return results

    def delete_negative_keywords(self, negative_keyword_ids):
        """Permanently remove ad-group negative keywords by id — how a negative
        added by the engine is reverted. Like archiving, v3 deletes take an id
        FILTER rather than entity objects. Returns per-batch results."""
        ct = "application/vnd.spNegativeKeyword.v3+json"
        results = []
        for batch in self._chunks(list(negative_keyword_ids), 100):
            payload = {"negativeKeywordIdFilter": {"include": [str(n) for n in batch]}}
            resp = self._send_retry("POST", "/sp/negativeKeywords/delete", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    def create_campaign_negative_keywords(self, items):
        """items: {campaignId, keywordText, matchType}. CAMPAIGN-level negatives
        (apply to every ad group in the campaign). Used for preemptive format negatives."""
        ct = "application/vnd.spCampaignNegativeKeyword.v3+json"
        results = []
        for batch in self._chunks(items, 100):
            payload = {"campaignNegativeKeywords": [
                {"campaignId": str(it["campaignId"]), "keywordText": it["keywordText"],
                 "matchType": it.get("matchType", "NEGATIVE_PHRASE"), "state": "ENABLED"}
                for it in batch]}
            resp = self._send_retry("POST", "/sp/campaignNegativeKeywords", ct, payload)
            results.append(_batch_result(resp, [it["keywordText"] for it in batch]))
        return results

    def list_campaign_negative_keywords(self, campaign_ids):
        """List campaign-level negative keywords for the given campaigns."""
        ct = "application/vnd.spCampaignNegativeKeyword.v3+json"
        return self.list_all("/sp/campaignNegativeKeywords/list", ct, "campaignNegativeKeywords",
                             extra_body={"campaignIdFilter": {"include": [str(c) for c in campaign_ids]}})

    def pause_ad_groups(self, ad_group_ids):
        """Set state=PAUSED on the given ad group ids. Returns per-batch results."""
        ct = "application/vnd.spAdGroup.v3+json"
        results = []
        for batch in self._chunks(list(ad_group_ids), 100):
            payload = {"adGroups": [{"adGroupId": str(g), "state": "PAUSED"} for g in batch]}
            resp = self._send_retry("PUT", "/sp/adGroups", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    def set_ad_groups_state(self, ad_group_ids, state):
        """Generic state setter (used for rollback: state='ENABLED')."""
        ct = "application/vnd.spAdGroup.v3+json"
        results = []
        for batch in self._chunks(list(ad_group_ids), 100):
            payload = {"adGroups": [{"adGroupId": str(g), "state": state} for g in batch]}
            resp = self._send_retry("PUT", "/sp/adGroups", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    # ---- targets (auto targeting groups): read bids + update bids ---------
    def list_targets(self, campaign_ids):
        """List targeting clauses for the given campaigns. Returns clause dicts (incl. targetId, bid)."""
        ct = "application/vnd.spTargetingClause.v3+json"
        return self.list_all("/sp/targets/list", ct, "targetingClauses",
                             extra_body={"campaignIdFilter": {"include": [str(c) for c in campaign_ids]}})

    def list_product_ads(self, ad_group_ids):
        """List product ads (ASIN/SKU per ad) for the given ad groups."""
        ct = "application/vnd.spProductAd.v3+json"
        out = []
        for batch in self._chunks(list(ad_group_ids), 100):
            items = self.list_all("/sp/productAds/list", ct, "productAds",
                                  extra_body={"adGroupIdFilter": {"include": [str(g) for g in batch]}})
            out.extend(items)
        return out

    def list_campaigns_by_id(self, campaign_ids):
        """LIVE current state/name/budget for specific campaigns (real-time, not cached)."""
        ct = "application/vnd.spCampaign.v3+json"
        out = []
        for batch in self._chunks([str(c) for c in campaign_ids], 100):
            out.extend(self.list_all("/sp/campaigns/list", ct, "campaigns",
                                     extra_body={"campaignIdFilter": {"include": batch}}))
        return out

    def list_ad_groups_by_id(self, ad_group_ids):
        """LIVE current state/name/bid for specific ad groups (real-time, not cached)."""
        ct = "application/vnd.spAdGroup.v3+json"
        out = []
        for batch in self._chunks([str(g) for g in ad_group_ids], 100):
            out.extend(self.list_all("/sp/adGroups/list", ct, "adGroups",
                                     extra_body={"adGroupIdFilter": {"include": batch}}))
        return out

    # ---- max-bid ceiling (Spec A) ----------------------------------------
    def _ceiling(self, surface):
        """Per-market write ceiling for 'target'/'keyword'/'budget', cached.

        None means the ceiling was read successfully and is deliberately unset.
        A read failure raises CeilingReadError and blocks the write.
        """
        if surface not in self._ceilings:
            import db
            try:
                conn = db.connect(ro=True)
                try:
                    self._ceilings[surface] = db.get_bid_ceiling(conn, surface)
                finally:
                    conn.close()
            except Exception as e:
                print(f"  ERROR: could not read the {surface} ceiling ({e}) — "
                      "write blocked", file=sys.stderr)
                raise CeilingReadError(
                    f"could not read the {surface} ceiling") from e
        return self._ceilings[surface]

    def _apply_ceiling(self, surface, entity_id, bid):
        """Return the bid to actually write, clamped to the surface ceiling.
        Records any clamp in self.last_clamps for the caller to log."""
        cap = self._ceiling(surface)
        b = round(float(bid), 2)
        if cap is not None and b > cap:
            self.last_clamps.append({"id": str(entity_id), "requested": b, "cap": float(cap)})
            return round(float(cap), 2)
        return b

    def update_target_bids(self, items):
        """items: list of {targetId, bid}. PUT new bids. Returns per-batch results."""
        ct = "application/vnd.spTargetingClause.v3+json"
        results = []
        self.last_clamps = []
        for batch in self._chunks(items, 100):
            payload = {"targetingClauses": [
                {"targetId": str(it["targetId"]),
                 "bid": self._apply_ceiling("target", it["targetId"], it["bid"])} for it in batch]}
            resp = self._send_retry("PUT", "/sp/targets", ct, payload)
            results.append(_batch_result(resp, [it["targetId"] for it in batch]))
        return results


    # ---- CREATE (manual exact-match harvesting) --------------------------
    def create_campaigns(self, items):
        """items: dicts {name, budget, startDate, strategy?}. Returns (status, json).

        The daily budget is clamped exactly as `update_campaign_budgets` clamps
        it. The ceiling used to apply only to EDITS, so a builder could create a
        campaign at any budget it liked and the cap only began to bite the next
        time something changed it — which for a freshly created campaign may be
        never.
        """
        ct = "application/vnd.spCampaign.v3+json"
        payload = {"campaigns": [{
            "name": it["name"], "targetingType": it.get("targetingType", "MANUAL"),
            "state": "ENABLED",
            "budget": {"budgetType": "DAILY",
                       "budget": self._apply_ceiling("budget", it["name"], it["budget"])},
            "dynamicBidding": {"strategy": it.get("strategy", "LEGACY_FOR_SALES")},
            "startDate": it["startDate"],
        } for it in items]}
        r = self._send_retry("POST", "/sp/campaigns", ct, payload)
        return r.status_code, _safe_json(r)

    def create_ad_groups(self, items):
        """items: dicts {name, campaignId, defaultBid}.

        The default bid is clamped to the TARGET ceiling, because that is the bid
        it stands in for: an ad group's default rules the auction for every clause
        underneath it that carries no bid of its own. Creation used to skip the
        clamp that every bid EDIT goes through.
        """
        ct = "application/vnd.spAdGroup.v3+json"
        payload = {"adGroups": [{
            "name": it["name"], "campaignId": str(it["campaignId"]),
            "state": "ENABLED",
            "defaultBid": self._apply_ceiling("target", it["name"], it["defaultBid"]),
        } for it in items]}
        r = self._send_retry("POST", "/sp/adGroups", ct, payload)
        return r.status_code, _safe_json(r)

    def create_product_ads(self, items):
        """items: dicts {campaignId, adGroupId, asin}."""
        ct = "application/vnd.spProductAd.v3+json"
        payload = {"productAds": [{
            "campaignId": str(it["campaignId"]), "adGroupId": str(it["adGroupId"]),
            "asin": it["asin"], "state": "ENABLED",
        } for it in items]}
        r = self._send_retry("POST", "/sp/productAds", ct, payload)
        return r.status_code, _safe_json(r)

    def create_keywords(self, items):
        """items: dicts {campaignId, adGroupId, keywordText, bid}."""
        ct = "application/vnd.spKeyword.v3+json"
        self.last_clamps = []
        payload = {"keywords": [{
            "campaignId": str(it["campaignId"]), "adGroupId": str(it["adGroupId"]),
            "keywordText": it["keywordText"], "matchType": it.get("matchType", "EXACT"),
            "bid": self._apply_ceiling("keyword", it["keywordText"], it["bid"]),
            "state": "ENABLED",
        } for it in items]}
        r = self._send_retry("POST", "/sp/keywords", ct, payload)
        return r.status_code, _safe_json(r)


    def list_keywords(self, campaign_ids):
        """List keywords for campaigns. Returns dicts (keywordId, adGroupId, campaignId, bid, keywordText)."""
        ct = "application/vnd.spKeyword.v3+json"
        return self.list_all("/sp/keywords/list", ct, "keywords",
                             extra_body={"campaignIdFilter": {"include": [str(c) for c in campaign_ids]}})

    def update_keyword_bids(self, items):
        """items: {keywordId, bid}. PUT new bids. Returns per-batch results."""
        ct = "application/vnd.spKeyword.v3+json"
        results = []
        self.last_clamps = []
        for batch in self._chunks(items, 100):
            payload = {"keywords": [
                {"keywordId": str(it["keywordId"]),
                 "bid": self._apply_ceiling("keyword", it["keywordId"], it["bid"])} for it in batch]}
            resp = self._send_retry("PUT", "/sp/keywords", ct, payload)
            results.append(_batch_result(resp, [it["keywordId"] for it in batch]))
        return results

    def update_campaign_budgets(self, items):
        """items: list of {campaignId, budget}. Sets DAILY budget. Returns per-batch results."""
        ct = "application/vnd.spCampaign.v3+json"
        results = []
        self.last_clamps = []
        for batch in self._chunks(items, 100):
            payload = {"campaigns": [
                {"campaignId": str(it["campaignId"]),
                 # bids had a ceiling; budgets had NOTHING between a typo'd
                 # rule and a $400/day campaign. Same clamp, surface "budget".
                 "budget": {"budgetType": "DAILY",
                            "budget": self._apply_ceiling("budget", it["campaignId"], it["budget"])}}
                for it in batch]}
            resp = self._send_retry("PUT", "/sp/campaigns", ct, payload)
            results.append(_batch_result(resp, [it["campaignId"] for it in batch]))
        return results

    def set_campaigns_state(self, campaign_ids, state):
        """Set state (e.g. 'PAUSED'/'ENABLED') on campaigns. Returns per-batch results."""
        ct = "application/vnd.spCampaign.v3+json"
        results = []
        for batch in self._chunks(list(campaign_ids), 100):
            payload = {"campaigns": [{"campaignId": str(c), "state": state} for c in batch]}
            resp = self._send_retry("PUT", "/sp/campaigns", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    def archive_campaigns(self, campaign_ids):
        """Archive campaigns. PERMANENT — there is no un-archive.

        Not a state write. `PUT /sp/campaigns` rejects ARCHIVED outright:
          "Value 'ARCHIVED' ... must satisfy enum value set:
           [ENABLED, PROPOSED, PAUSED]"
        v3 moved archiving to its own delete endpoint, which takes an id
        FILTER rather than a list of entity objects, and ARCHIVED became a
        read-only state that only comes back from list calls."""
        ct = "application/vnd.spCampaign.v3+json"
        results = []
        for batch in self._chunks(list(campaign_ids), 100):
            payload = {"campaignIdFilter": {"include": [str(c) for c in batch]}}
            resp = self._send_retry("POST", "/sp/campaigns/delete", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    def set_targets_state(self, target_ids, state):
        """Set state (e.g. 'PAUSED') on targeting clauses. Returns per-batch results."""
        ct = "application/vnd.spTargetingClause.v3+json"
        results = []
        for batch in self._chunks(list(target_ids), 100):
            payload = {"targetingClauses": [{"targetId": str(t), "state": state} for t in batch]}
            resp = self._send_retry("PUT", "/sp/targets", ct, payload)
            results.append(_batch_result(resp, batch))
        return results

    def set_keywords_state(self, keyword_ids, state):
        """Set state (e.g. 'PAUSED'/'ENABLED') on keywords. Returns per-batch results."""
        ct = "application/vnd.spKeyword.v3+json"
        results = []
        for batch in self._chunks(list(keyword_ids), 100):
            payload = {"keywords": [{"keywordId": str(k), "state": state} for k in batch]}
            resp = self._send_retry("PUT", "/sp/keywords", ct, payload)
            # `batch` is already a list of ids — unlike update_keyword_bids,
            # which is handed {keywordId, bid} dicts. Indexing into these
            # strings raised TypeError on every call.
            results.append(_batch_result(resp, batch))
        return results

    def get_keyword_recommendations(self, asins, max_recommendations=200):
        """Amazon's suggested keywords for a set of ad ASINs (SP keyword
        recommendations, v5: POST /sp/targets/keywords/recommendations).
        Returns a de-duped list of lowercase keyword strings (best-effort).
        Returns [] on any non-200 so the caller can fall back to title keywords.
        Note: the exact vendor media type / field names need a live check on the
        Mac (the sandbox can't reach the API).

        THE LOCALE IS THE MARKET'S. It was "en_US" for every marketplace, while
        the scavenger builder runs nightly in UK, DE, FR, ES and IT — so those
        campaigns were offered English discovery keywords, and the DE, ES and IT
        snapshots hold plenty of English broad scavenger targets to show for it.

        If Amazon rejects the market's locale the request is retried once with
        en_US, because returning nothing here is not neutral: the caller falls
        back to title keywords and the wrong-language set is replaced by a
        thinner one with no explanation."""
        ct = "application/vnd.spkeywordsrecommendation.v5+json"
        locale = markets.cfg(self.market).get("locale", "en_US")
        body = {
            "recommendationType": "KEYWORDS_FOR_ASINS",
            "asins": [a.upper() for a in asins][:500],
            "maxRecommendations": max_recommendations,
            "sortDimension": "CLICKS",
            "locale": locale,
        }
        resp = None
        for attempt in range(3):   # retry on 429 with backoff before giving up
            try:
                resp = requests.post(self.base + "/sp/targets/keywords/recommendations",
                                     headers=self._headers(ct), data=json.dumps(body), timeout=60)
            except Exception as e:
                print(f"  keyword-recs request failed: {e}", file=sys.stderr)
                return []
            if resp.status_code == 200:
                break
            if resp.status_code == 429 and attempt < 2:
                wait = 20 * (attempt + 1)
                print(f"  keyword-recs HTTP 429 — backing off {wait}s (attempt {attempt + 1}/3)", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code == 400 and locale != "en_US":
                # The locale is the only thing that changed here, so try the
                # one that always worked rather than silently returning nothing.
                print(f"  keyword-recs HTTP 400 with locale {locale} — retrying "
                      f"once as en_US", file=sys.stderr)
                body["locale"] = "en_US"
                locale = "en_US"
                try:
                    resp = requests.post(self.base + "/sp/targets/keywords/recommendations",
                                         headers=self._headers(ct), data=json.dumps(body),
                                         timeout=60)
                except Exception as e:
                    print(f"  keyword-recs retry failed: {e}", file=sys.stderr)
                    return []
                if resp.status_code == 200:
                    break
            print(f"  keyword-recs HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return []
        if resp is None or resp.status_code != 200:
            return []
        data = _safe_json(resp)
        # response shape varies by version; pull keywordText from common layouts
        recs = []
        candidates = []
        if isinstance(data, dict):
            candidates = (data.get("keywordTargetList") or data.get("recommendations")
                          or data.get("keywords") or [])
        elif isinstance(data, list):
            candidates = data
        for r in candidates:
            if isinstance(r, dict):
                kw = r.get("keyword") or r.get("keywordText") or r.get("bid", {}).get("keyword")
            else:
                kw = r
            if kw:
                recs.append(str(kw).strip().lower())
        seen, out = set(), []
        for k in recs:
            if k and k not in seen:
                seen.add(k); out.append(k)
        return out

    def product_metadata(self, asins):
        """Book/product titles by ASIN, from the advertiser's own catalogue
        (SP product metadata: POST /product/metadata). Returns {asin: {title,
        brand, image_url}} for the ASINs Amazon recognises. This is the only way
        to get a title for a book that is NOT advertised — an un-advertised book
        has no ad group, so its name is nowhere in the pulled data. Best-effort
        and read-only: a non-200 chunk is skipped, never fatal."""
        ct = "application/vnd.spproductmetadata.v1+json"
        result = {}
        for chunk in self._chunks([a.upper() for a in asins], 100):
            body = {"adType": "SP", "pageIndex": 0, "pageSize": 100,
                    "asins": chunk, "checkItemDetails": True}
            try:
                resp = self._send_retry("POST", "/product/metadata", ct, body,
                                        retry_transport=True)
            except Exception as e:
                print(f"  product-metadata request failed: {e}", file=sys.stderr)
                continue
            if resp is None or resp.status_code != 200:
                code = getattr(resp, "status_code", "?")
                print(f"  product-metadata HTTP {code}: {getattr(resp, 'text', '')[:200]}", file=sys.stderr)
                continue
            data = _safe_json(resp) or {}
            for p in data.get("ProductMetadataList", []):
                asin = (p.get("asin") or "").upper()
                if not asin:
                    continue
                result[asin] = {
                    "title": p.get("title"),
                    "brand": p.get("brand"),
                    "image_url": p.get("imageUrl"),
                }
        return result

    def create_product_targets(self, items):
        """items: {campaignId, adGroupId, asin, bid} -> ASIN_SAME_AS product targets."""
        ct = "application/vnd.spTargetingClause.v3+json"
        self.last_clamps = []
        payload = {"targetingClauses": [{
            "campaignId": str(it["campaignId"]), "adGroupId": str(it["adGroupId"]),
            "expression": [{"type": "ASIN_SAME_AS", "value": it["asin"].upper()}],
            "expressionType": "MANUAL",
            "bid": self._apply_ceiling("target", it["asin"], it["bid"]), "state": "ENABLED",
        } for it in items]}
        r = self._send_retry("POST", "/sp/targets", ct, payload)
        return r.status_code, _safe_json(r)

    def create_negative_product_targets(self, items):
        """items: {campaignId, adGroupId, asin} -> negative ASIN target in the source."""
        ct = "application/vnd.spNegativeTargetingClause.v3+json"
        payload = {"negativeTargetingClauses": [{
            "campaignId": str(it["campaignId"]), "adGroupId": str(it["adGroupId"]),
            "expression": [{"type": "ASIN_SAME_AS", "value": it["asin"].upper()}],
            "state": "ENABLED",
        } for it in items]}
        r = self._send_retry("POST", "/sp/negativeTargets", ct, payload)
        return r.status_code, _safe_json(r)


# Report creation is not an account change, and neither is any GET-shaped list
# call that happens to POST a filter. Only the mutating endpoints count.
_NON_WRITE_PATHS = ("/reporting/reports", "/list", "/recommendations",
                    "/product/metadata")


# The endpoints that CREATE something new. A POST to one of these makes an
# entity; a PUT to the same path updates one. Amazon's v3 write API is shaped
# that way throughout, so the method plus the absence of a list/report suffix is
# the whole test.
def _is_create(method, path):
    if method.upper() != "POST":
        return False
    return not any(p in path for p in _NON_WRITE_PATHS)


# POPULATING a campaign. These three endpoints are how the two campaign
# builders do their whole job, and their volume is bounded by the builders' own
# structural caps rather than by a threshold. They reach the build budget only
# in a process that called `declare_campaign_builder` — see `_surface`.
#
# Creating a CAMPAIGN is deliberately NOT here: a campaign is the unit of daily
# spend at $5/day, and fifty a night is already a lot. Neither is
# POST /sp/targets, which no builder uses — only `phase4b_harvest_asins`, which
# promotes what a threshold judged a winner and belongs on the 500.
#
# The list fails SAFE. An endpoint nobody adds here gets the stricter change
# cap, so forgetting one stops a run loudly instead of letting it run away.
_BUILD_ENDPOINTS = {
    ("POST", "/sp/adGroups"),
    ("POST", "/sp/productAds"),
    ("POST", "/sp/keywords"),
    # PUT /sp/targets is here for lottery_build alone, and it is not a bid
    # decision. Amazon auto-generates four clauses under every new AUTO ad
    # group, and `set_clause_bids` then writes the three bids and pauses
    # complements — FOUR target writes per ad group it just created, on new ad
    # groups only. So 125 new ASINs is 500 writes and a routine build would
    # stop with its ad groups and product ads already on the account and their
    # clauses half-configured, which the next run does not redo because those
    # ASINs now read as placed. Reachable only from a declared builder;
    # phase3_bids and the DSL keep the 500.
    ("PUT", "/sp/targets"),
}


def _longest_list(value, depth=2):
    """Longest list anywhere in the payload, up to `depth` dicts down.

    A top-level list is not enough. v3 DELETES take an id FILTER rather than
    entity objects — `{"campaignIdFilter": {"include": ["1", …]}}` — so
    `archive_campaigns` and `delete_negative_keywords` sent a hundred ids and
    counted as ONE. That is a guard a hundred times weaker than it reads, on
    the one action that cannot be undone: Amazon has no un-archive.
    """
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict) and depth > 0:
        return max((_longest_list(v, depth - 1) for v in value.values()),
                   default=0)
    return 0


def _entity_count(method, path, payload):
    """How many account entities this request would create or change.

    Every v3 write payload names its entities in a list, either at the top
    level ({"campaigns": [ … ]}) or inside an id filter, so the count is the
    longest list in it. A shape with no list counts as one.
    """
    if method.upper() not in ("POST", "PUT", "DELETE"):
        return 0
    if any(p in path for p in _NON_WRITE_PATHS):
        return 0
    if not isinstance(payload, dict):
        return 1
    return _longest_list(payload) or 1


def _item_errors(body):
    """The v3 error entries from a recognised batch body."""
    if not isinstance(body, dict):
        return None
    for value in body.values():
        if isinstance(value, dict) and ("success" in value or "error" in value):
            return value.get("error") or []
    return None


def _item_failures(body):
    """(failed_count, failed_indexes) from a v3 batch body.

    The v3 write endpoints answer 207 with {"<entities>": {"success": [...],
    "error": [...]}} — a batch where EVERY item errored is still HTTP 207, so
    the status code alone cannot mean success. Error entries carry the request
    'index'. Unrecognized shapes report (None, []): no item info, not failure."""
    errors = _item_errors(body)
    if errors is None:
        return None, []
    idx = [e.get("index") for e in errors if isinstance(e, dict)]
    return len(errors), [i for i in idx if i is not None]


def _duplicate_error(entry):
    """True only for Amazon's explicit duplicateValueError outcome."""
    if not isinstance(entry, dict):
        return False
    values = [entry.get("errorType")]
    values.extend(e.get("errorType") for e in (entry.get("errors") or [])
                  if isinstance(e, dict))
    return any(str(value or "").lower() == "duplicatevalueerror"
               for value in values)


def _batch_outcomes(http, body, count):
    """One accepted, duplicate, failed, or uncertain status per request index."""
    if http not in (200, 207):
        return [{"status": "failed"} for _ in range(count)]
    errors = _item_errors(body)
    if errors is None:
        status = "uncertain" if http == 207 else "accepted"
        return [{"status": status} for _ in range(count)]
    outcomes = [{"status": "accepted"} for _ in range(count)]
    mapped = 0
    for error in errors:
        index = error.get("index") if isinstance(error, dict) else None
        if not isinstance(index, int) or not 0 <= index < count:
            continue
        mapped += 1
        status = "duplicate" if _duplicate_error(error) else "failed"
        outcomes[index] = {"status": status}
    if mapped < len(errors):
        return [{"status": "uncertain"} for _ in range(count)]
    return outcomes


def _created_ids(body, id_key):
    """{request index -> created entity id} from a v3 create response's success
    array. `id_key` is the id field name, e.g. 'negativeKeywordId'. Unrecognized
    shapes return {} — no ids, so the caller records nothing to revert by."""
    if not isinstance(body, dict):
        return {}
    for v in body.values():
        if isinstance(v, dict) and "success" in v:
            out = {}
            for s in v.get("success") or []:
                if isinstance(s, dict) and s.get("index") is not None and s.get(id_key):
                    out[s["index"]] = str(s[id_key])
            return out
    return {}


def _batch_result(resp, ids):
    """One result dict per batch, item-aware: failed_items counts the v3 error
    entries and failed_ids maps their indexes back onto this batch's ids."""
    body = _safe_json(resp)
    failed, idx = _item_failures(body)
    return {"http": resp.status_code, "body": body, "count": len(ids),
            # The ids THIS batch carried. Without them a caller holding several
            # batches cannot say which ids a failed batch was even about, so it
            # had to reason over the whole request and either mirror too much or
            # too little.
            "ids": [str(i) for i in ids],
            "failed_items": failed,
            "failed_ids": [str(ids[i]) for i in idx if i < len(ids)],
            "outcomes": _batch_outcomes(resp.status_code, body, len(ids))}


def failed_ids(results):
    """Every item id any batch reported as rejected."""
    return {i for b in results for i in b.get("failed_ids", [])}


def batch_uncertain(results):
    """Batches whose per-item outcome could not be read.

    207 is MULTI-STATUS: it exists precisely because some items may have failed,
    so a 207 whose body we cannot parse tells us nothing about any single item.
    `_item_failures` reports that as `failed_items: None`, and `not None` is
    True — so "nobody knows" was passing every test for "all fine", and the
    callers then mirrored and logged every item as accepted.

    A plain 200 with no per-item block is a different thing: the non-batch
    endpoints answer that way and it genuinely means success.
    """
    return [b for b in results
            if b.get("http") == 207 and b.get("failed_items") is None]


def item_outcomes(results, ids=None):
    """Flatten per-request outcomes from a list of batch results."""
    outcomes = []
    batches = results or []
    for batch in batches:
        known = batch.get("outcomes")
        if known is not None:
            outcomes.extend(str(o.get("status") if isinstance(o, dict) else o)
                            for o in known)
            continue
        batch_ids = [str(i) for i in (batch.get("ids") or [])]
        if not batch_ids and len(batches) == 1:
            batch_ids = [str(i) for i in (ids or [])]
        count = int(batch.get("count") or len(batch_ids))
        if batch.get("http") not in (200, 207):
            outcomes.extend(["failed"] * count)
            continue
        if batch.get("http") == 207 and batch.get("failed_items") is None:
            outcomes.extend(["uncertain"] * count)
            continue
        rejected = {str(i) for i in (batch.get("failed_ids") or [])}
        failed = batch.get("failed_items")
        if failed is not None and len(rejected) < failed:
            outcomes.extend(["uncertain"] * count)
            continue
        outcomes.extend("failed" if item_id in rejected else "accepted"
                        for item_id in batch_ids)
        if count > len(batch_ids):
            outcomes.extend(["uncertain"] * (count - len(batch_ids)))
    return outcomes


def items_ok(results):
    """Batch success the honest way: 2xx/207, zero item-level errors, AND a
    body we could actually read. An unreadable 207 is not success."""
    if not results:
        return False
    if batch_uncertain(results):
        return False
    return all(b.get("http") in (200, 207) and not b.get("failed_items")
               for b in results)


def certain_ids(results, ids=None):
    """The ids this response proves Amazon ACCEPTED.

    Judged per batch, because batches fail independently. A batch contributes
    nothing when its HTTP status is not 2xx/207, and nothing when it is a 207
    whose body could not be read — with a multi-status we cannot parse, we do
    not know which of its items landed. Local state that disagrees with Amazon
    is worse than local state that is a day behind, because the next preview
    proposes from it and Undo restores from it.

    `ids` is accepted for older callers that pass the whole request; a batch's
    own ids are used when it carries them.
    """
    accepted = set()
    for b in results or []:
        if b.get("http") not in (200, 207):
            continue
        if b.get("http") == 207 and b.get("failed_items") is None:
            continue
        rejected = {str(i) for i in b.get("failed_ids") or []}
        batch_ids = b.get("ids")
        if batch_ids is None:
            batch_ids = [str(i) for i in (ids or [])]
        accepted |= {str(i) for i in batch_ids if str(i) not in rejected}
    return accepted


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text[:500]}


def success_ids(resp_json, key, id_field):
    """Parse SP v3 create response -> {input_index: created_id}."""
    out = {}
    block = (resp_json or {}).get(key, {})
    for item in block.get("success", []):
        if "index" in item and item.get(id_field):
            out[item["index"]] = item[id_field]
    return out


# How much of the error block one refusal line may carry. Long enough for five
# reasons with an ASIN each, short enough that a night of them does not bury the
# rest of the run log.
REFUSAL_LINE_CHARS = 800
REFUSAL_SAMPLE = 5


def _reason_text(value, depth=0):
    """The human sentence inside an errorValue, wherever Amazon nested it.

    The documented shape is `errorValue.<some type>.reason`, and the type name
    in the middle changes per error. So this looks for the words rather than
    for a fixed path, and gives up quietly instead of raising.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for field in ("reason", "message", "description", "detail"):
            got = value.get(field)
            if isinstance(got, str) and got:
                return got
        if depth < 2:
            for nested in value.values():
                got = _reason_text(nested, depth + 1)
                if got:
                    return got
    return ""


def _item_label(items, index):
    """The submitted item at `index`, named the way a reader needs it.

    Amazon's 207 response identifies a refused entry by its POSITION in the
    batch we sent, so the batch is the only thing that can turn that back into
    an ASIN. Measured on the 2026-08-25 run: all 164 reasons printed carried an
    `index` and not one carried `cause.trigger`, so every line read
    "0: adEligibilityError AD_INELIGIBLE" — the right reason attached to no
    item. That is enough to confirm a whole cohort is refused, and not enough
    to say WHICH design inside a mixed batch was, which is exactly what the
    open apparel question needs.
    """
    if not isinstance(items, (list, tuple)):
        return None
    try:
        item = items[int(index)]
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(item, dict):
        return str(item)[:60]
    for field in ("asin", "keywordText", "name", "adGroupId", "campaignId"):
        if item.get(field):
            return str(item[field])[:60]
    return None


def _refusal_reason(entry, items=None):
    """One refused entry, read down to the ASIN and the cause.

    A create response names the item in `cause.trigger` — for a product ad that
    is the ASIN — and the cause in `errors[].errorType`. Nothing here insists on
    that shape: an entry we cannot read is printed raw rather than dropped,
    because a reason we did not expect is exactly the one worth having.

    Amazon in fact sends `index` and no `cause.trigger`, so `items` — the batch
    that was submitted — is resolved against that index to recover the ASIN.
    The index is still printed beside it, because the mapping is only as good
    as the caller passing the batch it actually sent.
    """
    if not isinstance(entry, dict):
        return str(entry)[:160]
    cause = entry.get("cause")
    who = cause.get("trigger") if isinstance(cause, dict) else None
    if not who:
        idx = entry.get("index")
        named = _item_label(items, idx)
        who = f"{named} (#{idx})" if named else idx
    inner_errors = entry.get("errors")
    parts = []
    for inner in (inner_errors if isinstance(inner_errors, list) else [])[:2]:
        if not isinstance(inner, dict):
            parts.append(str(inner)[:160])
            continue
        etype = str(inner.get("errorType") or "").strip()
        text = _reason_text(inner.get("errorValue")).strip()
        parts.append(" ".join(p for p in (etype, text) if p)[:200] or str(inner)[:160])
    if not parts:
        parts = [str({k: v for k, v in entry.items() if k != "cause"})[:200]]
    return f"{who if who is not None else '?'}: {' / '.join(parts)}"


def refusal_reasons(resp_json, key, limit=REFUSAL_SAMPLE, items=None):
    """Up to `limit` short reasons out of a create response's error block.

    `items` is the batch that was submitted, used to turn Amazon's positional
    `index` back into an ASIN.
    """
    block = (resp_json or {}).get(key) if isinstance(resp_json, dict) else None
    errs = block.get("error") if isinstance(block, dict) else None
    if not isinstance(errs, list):
        errs = []
    return [_refusal_reason(e, items) for e in errs[:limit]]


def report_refused(key, submitted, accepted, status, resp_json, label="", items=None):
    """Say on STDERR how many items Amazon turned down and why. Returns the count.

    Every create in this engine read only the `success` block and threw the
    response away. So a batch Amazon refused left a count and no reason: the
    scavenger and lottery builders re-submitted the same refused ASINs every
    night from 2026-06-25, about 873 of them across six markets, and sixty
    nights went by with nothing on disk saying why. This is the missing half.

    STDERR, for two reasons. `appctl` promises exactly one JSON object on
    stdout, and run_scheduled.sh sends stderr into outputs/scheduled_runs.log,
    which is where these lines are meant to be read in the morning.

    Pass `items` — the batch that was submitted — so a refusal Amazon identifies
    only by position can still name its ASIN.
    """
    refused = max(0, submitted - accepted)
    if not refused:
        return 0
    reasons = refusal_reasons(resp_json, key, items=items)
    detail = (" | ".join(reasons) if reasons
              else f"no error block in the response: {str(resp_json)[:300]}")
    where = f"[{label}] " if label else ""
    print(f"  {where}{key}: {refused} of {submitted} REFUSED (HTTP {status}). "
          f"First reasons: {detail[:REFUSAL_LINE_CHARS]}", file=sys.stderr)
    return refused
