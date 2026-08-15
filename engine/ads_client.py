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

import paths
import time

import requests
import markets

ENV_PATH = os.path.join(paths.REPO_ROOT, ".env")

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def load_env(path=ENV_PATH):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class AdsClient:
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
                  f"(attempt {attempt + 1}/5)")
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
                      f"(attempt {attempt + 1}/{tries})")
                time.sleep(wait)
                continue
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            if not last:
                wait = min(40, 5 * (2 ** attempt))   # 5,10,20,40
                print(f"  {path} HTTP {resp.status_code} — backing off {wait}s "
                      f"(attempt {attempt + 1}/{tries})")
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

    def get_report(self, report_id):
        """Returns (status, url). url is set only when COMPLETED."""
        s = requests.get(self.base + f"/reporting/reports/{report_id}",
                         headers=self._headers(), timeout=60)
        s.raise_for_status()
        info = s.json()
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
        """Per-market bid ceiling for 'target'/'keyword', cached. Read once from
        this market's DB (read-only). Fail-open only if the DB is absent."""
        if surface not in self._ceilings:
            import db
            try:
                conn = db.connect(ro=True)
                try:
                    self._ceilings[surface] = db.get_bid_ceiling(conn, surface)
                finally:
                    conn.close()
            except Exception:
                self._ceilings[surface] = None
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
        """items: dicts {name, budget, startDate, strategy?}. Returns (status, json)."""
        ct = "application/vnd.spCampaign.v3+json"
        payload = {"campaigns": [{
            "name": it["name"], "targetingType": it.get("targetingType", "MANUAL"),
            "state": "ENABLED",
            "budget": {"budgetType": "DAILY", "budget": it["budget"]},
            "dynamicBidding": {"strategy": it.get("strategy", "LEGACY_FOR_SALES")},
            "startDate": it["startDate"],
        } for it in items]}
        r = self._send_retry("POST", "/sp/campaigns", ct, payload)
        return r.status_code, _safe_json(r)

    def create_ad_groups(self, items):
        """items: dicts {name, campaignId, defaultBid}."""
        ct = "application/vnd.spAdGroup.v3+json"
        payload = {"adGroups": [{
            "name": it["name"], "campaignId": str(it["campaignId"]),
            "state": "ENABLED", "defaultBid": it["defaultBid"],
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
        Mac (the sandbox can't reach the API)."""
        ct = "application/vnd.spkeywordsrecommendation.v5+json"
        body = {
            "recommendationType": "KEYWORDS_FOR_ASINS",
            "asins": [a.upper() for a in asins][:500],
            "maxRecommendations": max_recommendations,
            "sortDimension": "CLICKS",
            "locale": "en_US",
        }
        resp = None
        for attempt in range(3):   # retry on 429 with backoff before giving up
            try:
                resp = requests.post(self.base + "/sp/targets/keywords/recommendations",
                                     headers=self._headers(ct), data=json.dumps(body), timeout=60)
            except Exception as e:
                print(f"  keyword-recs request failed: {e}")
                return []
            if resp.status_code == 200:
                break
            if resp.status_code == 429 and attempt < 2:
                wait = 20 * (attempt + 1)
                print(f"  keyword-recs HTTP 429 — backing off {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue
            print(f"  keyword-recs HTTP {resp.status_code}: {resp.text[:200]}")
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
                print(f"  product-metadata request failed: {e}")
                continue
            if resp is None or resp.status_code != 200:
                code = getattr(resp, "status_code", "?")
                print(f"  product-metadata HTTP {code}: {getattr(resp, 'text', '')[:200]}")
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


def _item_failures(body):
    """(failed_count, failed_indexes) from a v3 batch body.

    The v3 write endpoints answer 207 with {"<entities>": {"success": [...],
    "error": [...]}} — a batch where EVERY item errored is still HTTP 207, so
    the status code alone cannot mean success. Error entries carry the request
    'index'. Unrecognized shapes report (None, []): no item info, not failure."""
    if not isinstance(body, dict):
        return None, []
    for v in body.values():
        if isinstance(v, dict) and ("success" in v or "error" in v):
            errors = v.get("error") or []
            idx = [e.get("index") for e in errors if isinstance(e, dict)]
            return len(errors), [i for i in idx if i is not None]
    return None, []


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
            "failed_items": failed,
            "failed_ids": [str(ids[i]) for i in idx if i < len(ids)]}


def failed_ids(results):
    """Every item id any batch reported as rejected."""
    return {i for b in results for i in b.get("failed_ids", [])}


def items_ok(results):
    """Batch success the honest way: 2xx/207 AND zero item-level errors."""
    return all(b.get("http") in (200, 207) and not b.get("failed_items")
               for b in results) if results else False


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
