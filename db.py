#!/usr/bin/env python3
"""
Local SQLite storage for the ads automation tool.
Phase 0: store campaign structure + daily performance snapshots (read-only pulls).
Keeping our own history lets the rules engine look at trends, not one noisy day.
"""

import os
import re
import sqlite3
import datetime
import time

import markets

_HERE = os.path.dirname(os.path.abspath(__file__))


def _db_path():
    """US (default) -> ads_data.sqlite (unchanged). Other markets -> own file."""
    m = markets.current()
    return os.path.join(_HERE, "ads_data.sqlite" if m == markets.DEFAULT
                        else f"ads_data_{m}.sqlite")


# Resolved at import from ADS_MARKET; each per-market process gets its own DB file.
DB_PATH = _db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name TEXT, state TEXT, targeting_type TEXT,
    daily_budget REAL, bidding_strategy TEXT,
    pulled_at TEXT
);
CREATE TABLE IF NOT EXISTS ad_groups (
    ad_group_id TEXT PRIMARY KEY,
    campaign_id TEXT, name TEXT, state TEXT, default_bid REAL,
    pulled_at TEXT
);
-- Per-target/keyword mirror from /sp/targets/list + /sp/keywords/list (the
-- nightly pull replaces it wholesale). This is the ONLY place a target's own
-- bid and state live: the perf reports carry neither, and before this table
-- `bid` silently meant the ad-group default — a setBid rule computed from the
-- wrong base. bid NULL = no own bid, the ad-group default rules the auction.
CREATE TABLE IF NOT EXISTS targets (
    target_id TEXT PRIMARY KEY,
    campaign_id TEXT, ad_group_id TEXT,
    kind TEXT,            -- 'target' (clause) or 'keyword'
    text TEXT,            -- expression summary / keywordText
    match_type TEXT, state TEXT, bid REAL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_ad_group ON targets(ad_group_id);
-- One row per (entity, date) so we build real history across runs.
CREATE TABLE IF NOT EXISTS campaign_perf (
    date TEXT, campaign_id TEXT, campaign_name TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL,
    kenp_read REAL, kenp_royalties REAL,
    PRIMARY KEY (date, campaign_id)
);
CREATE TABLE IF NOT EXISTS targeting_perf (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    targeting TEXT, match_type TEXT, target_id TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL,
    kenp_read REAL, kenp_royalties REAL,
    PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type)
);
CREATE TABLE IF NOT EXISTS search_term_perf (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    search_term TEXT, targeting TEXT, match_type TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL,
    kenp_read REAL, kenp_royalties REAL,
    PRIMARY KEY (date, campaign_id, ad_group_id, search_term, targeting)
);
-- MEASURED cross-purchase: a shopper clicked the ad for `advertised_asin` and
-- bought `purchased_asin`. When those differ the sale is halo the ad earned but
-- the normal reports credit nowhere — for Merch that is one design's ad selling
-- a different design. `halo` only ESTIMATES this correlationally from the
-- Merch sales report; this is Amazon's own attribution.
-- `*_other_sku` are Amazon's own roll-ups of the not-advertised-ASIN portion.
CREATE TABLE IF NOT EXISTS purchased_product (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    keyword_id TEXT, keyword TEXT, keyword_type TEXT, match_type TEXT,
    advertised_asin TEXT, purchased_asin TEXT,
    units_sold INTEGER, sales REAL, purchases INTEGER,
    units_sold_other_sku INTEGER, sales_other_sku REAL, purchases_other_sku INTEGER,
    PRIMARY KEY (date, campaign_id, ad_group_id, keyword_id,
                 advertised_asin, purchased_asin)
);
CREATE INDEX IF NOT EXISTS idx_purchased_advertised
    ON purchased_product(date, advertised_asin);
CREATE INDEX IF NOT EXISTS idx_purchased_purchased
    ON purchased_product(date, purchased_asin);
-- The Merch sales report, BANKED per day rather than read from whichever file
-- happens to be newest. It is the only source of ORGANIC royalty (the Ads API
-- reports ad-attributed sales only), each download covers one window, and the
-- windows do not overlap — so "newest file wins" silently hid every earlier
-- period. Accumulating here makes the history permanent and additive: import
-- ten reports over a year and you have a year of daily organic royalty.
-- Colour/size variants repeat an (mkt, date, asin, product_type), so rows are
-- SUMMED on import; re-importing the same report is therefore idempotent.
-- ACCOUNT-WIDE: one file covers every marketplace, so this lives in the default
-- market's DB (see connect_shared) with `mkt` preserved, never fragmented.
CREATE TABLE IF NOT EXISTS sales_report_rows (
    mkt TEXT, date TEXT, asin TEXT,
    title TEXT, product_type TEXT,
    purchased INTEGER, cancelled INTEGER, returned INTEGER,
    revenue REAL, royalty REAL, currency TEXT,
    PRIMARY KEY (mkt, date, asin, product_type)
);
CREATE INDEX IF NOT EXISTS idx_sales_rows_asin ON sales_report_rows(asin, date);
CREATE INDEX IF NOT EXISTS idx_sales_rows_date ON sales_report_rows(date);
-- Provenance for every file the operator imports: what, when, which period it
-- covered, how much of it landed. Answers "is this stale?" and "did that import
-- actually add anything?" without guessing from filenames.
CREATE TABLE IF NOT EXISTS imported_files (
    kind TEXT, filename TEXT, imported_at TEXT,
    period_start TEXT, period_end TEXT,
    rows_in_file INTEGER, rows_banked INTEGER, note TEXT,
    PRIMARY KEY (kind, filename)
);
-- Monthly account history exported from the Ads CONSOLE, which reaches back
-- years where the API stops at ~95 days. Imported by hand; the API can never
-- refill it, so once a month is banked here it is the only copy.
-- The console's `Country` dimension came back EMPTY, so the finest split
-- available is Budget currency: USD is US, GBP is UK, and EUR is DE+FR+ES+IT
-- COMBINED and cannot be separated. `market` records that honestly rather than
-- pretending a euro row belongs to one country.
CREATE TABLE IF NOT EXISTS ads_history_monthly (
    month TEXT,                 -- YYYY-MM
    currency TEXT,              -- USD | GBP | EUR
    market TEXT,                -- US | UK | EU (EUR = four markets merged)
    impressions INTEGER, clicks INTEGER,
    spend REAL, sales REAL, purchases INTEGER, units INTEGER,
    source_file TEXT,
    PRIMARY KEY (month, currency)
);
CREATE INDEX IF NOT EXISTS idx_ads_history_month ON ads_history_monthly(month);
-- Per-ASIN economics as of one catalogue export. The export is ~2GB and the
-- nightly deletes every copy but the newest, so a design's price and royalty at
-- a past date is otherwise destroyed the moment a new export lands. Bidding and
-- kill decisions are priced off these numbers, so losing the history means
-- never being able to explain a past decision.
-- Scoped to ASINs the account actually advertises (~197k) rather than the whole
-- 2M-row catalogue: those are the ones whose price drives a decision.
CREATE TABLE IF NOT EXISTS asin_econ_snapshot (
    export_date TEXT,           -- ISO stamp parsed from the export filename
    asin TEXT, marketplace TEXT,
    product_type TEXT, brand TEXT, status TEXT,
    list_price TEXT, royalty_last30 REAL, sales_last30 INTEGER, sales_total INTEGER,
    PRIMARY KEY (export_date, asin, marketplace)
);
CREATE INDEX IF NOT EXISTS idx_asin_econ_asin ON asin_econ_snapshot(asin, export_date);
CREATE TABLE IF NOT EXISTS pull_log (
    pulled_at TEXT, kind TEXT, rows INTEGER, note TEXT
);
-- Tracks async report jobs so a re-run can resume instead of recreating.
CREATE TABLE IF NOT EXISTS report_jobs (
    report_type TEXT PRIMARY KEY,
    report_id TEXT, status TEXT, requested_at TEXT, window_end TEXT,
    downloaded INTEGER DEFAULT 0
);
-- Audit log of every WRITE the tool makes to Amazon (for review + rollback).
CREATE TABLE IF NOT EXISTS writes_log (
    applied_at TEXT, action TEXT, entity_type TEXT, entity_id TEXT,
    detail TEXT, prev_state TEXT, result TEXT
);
-- Maps each ad group to its product type (from ASIN -> Merch export), for per-type rules.
-- lifetime_sales = all-time units (from export salesTotal); drives the proven-winner pause guardrail.
CREATE TABLE IF NOT EXISTS ad_group_product (
    ad_group_id TEXT PRIMARY KEY,
    asin TEXT, product_type TEXT, brand TEXT, list_price TEXT, lifetime_sales INTEGER, mapped_at TEXT
);
-- Account ad totals per reporting period ('daily' = yesterday, 'mtd' = month-to-date),
-- each from a small SUMMARY report, for the Discord digest's spend/ACOS KPIs.
CREATE TABLE IF NOT EXISTS period_totals (
    period TEXT PRIMARY KEY, window TEXT, cost REAL, sales REAL, orders INTEGER, pulled_at TEXT
);
-- Per-market, per-type economics derived from the Merch export (non-US markets):
-- median royalty-per-unit + price -> break-even, so thresholds are local-currency correct.
CREATE TABLE IF NOT EXISTS market_econ (
    market TEXT, product_type TEXT, royalty REAL, price REAL, break_even REAL,
    n INTEGER, updated_at TEXT,
    PRIMARY KEY (market, product_type)
);
-- Running log of converting search terms worth promoting to manual exact-match campaigns.
CREATE TABLE IF NOT EXISTS harvest_log (
    search_term TEXT, source_ad_group_id TEXT,
    kind TEXT, product_type TEXT, source_campaign_id TEXT,
    clicks INTEGER, orders INTEGER, sales REAL, acos REAL, cpc REAL,
    first_seen TEXT, last_seen TEXT, promoted INTEGER DEFAULT 0,
    PRIMARY KEY (search_term, source_ad_group_id)
);
-- True per-day account totals (banked by daily_metrics.py / backfill_daily.py).
-- Also created lazily by store_daily_total for pre-existing DBs.
CREATE TABLE IF NOT EXISTS daily_totals (
    date TEXT PRIMARY KEY, cost REAL, sales REAL, orders INTEGER,
    impressions INTEGER, clicks INTEGER, units INTEGER, pulled_at TEXT
);
-- True per-day PER-CAMPAIGN totals (banked by backfill_daily.py) — powers the
-- campaign-scoped Targets chart. Created lazily by store_campaign_daily too.
CREATE TABLE IF NOT EXISTS campaign_daily (
    date TEXT, campaign_id TEXT, campaign_name TEXT,
    cost REAL, sales REAL, orders INTEGER,
    impressions INTEGER, clicks INTEGER, units INTEGER, pulled_at TEXT,
    PRIMARY KEY (date, campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_daily_campaign ON campaign_daily(campaign_id);
-- True per-day PER-TARGET performance, from an spTargeting report requested
-- with timeUnit=DAILY. This is the bottom rung of the daily ladder that
-- daily_totals and campaign_daily already cover at coarser grain.
-- NOT a snapshot table: targeting_perf holds overlapping trailing-30 windows
-- keyed by pull date, and differencing those gives the day that entered the
-- window minus the day that left it. These rows are single days.
-- A day's spend is final at once; its sales keep growing for up to 30 days as
-- Amazon attributes purchases back to the click that earned them. So recent
-- days under-report sales, and readers lag the window by
-- DAILY_ATTRIBUTION_LAG_DAYS.
-- The key matches targeting_perf rather than target_id: auto and
-- product-targeting clauses may not carry a keywordId. target_id gets an index.
CREATE TABLE IF NOT EXISTS target_daily (
    date TEXT, campaign_id TEXT, ad_group_id TEXT,
    targeting TEXT, match_type TEXT, target_id TEXT,
    impressions INTEGER, clicks INTEGER, cost REAL,
    orders INTEGER, sales REAL, acos REAL, pulled_at TEXT,
    PRIMARY KEY (date, campaign_id, ad_group_id, targeting, match_type)
);
CREATE INDEX IF NOT EXISTS idx_target_daily_target ON target_daily(target_id, date);
CREATE INDEX IF NOT EXISTS idx_target_daily_adgroup ON target_daily(ad_group_id, date);
-- Secondary indexes for the app's hot read paths. writes_log(action,entity_id)
-- kills the per-target full scan in targets/bidhistory; the targeting_perf ones
-- kill the per-ad-group/per-target scans in asin/searchterms/history.
CREATE INDEX IF NOT EXISTS idx_writes_log_action_entity ON writes_log(action, entity_id);
CREATE INDEX IF NOT EXISTS idx_targeting_adgroup_date ON targeting_perf(ad_group_id, date);
CREATE INDEX IF NOT EXISTS idx_targeting_target ON targeting_perf(target_id);
CREATE INDEX IF NOT EXISTS idx_search_term_adgroup_date ON search_term_perf(ad_group_id, date);
"""


def connect(ro=False):
    """ro=True opens read-only (URI mode) and skips schema/migration, so app
    reads never contend with the nightly writer for locks. Falls back to the
    normal read-write open when the DB file doesn't exist yet (first pull)."""
    if ro and os.path.exists(DB_PATH):
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn
    conn = sqlite3.connect(DB_PATH)
    # The app keeps long-lived read-only connections open against these files.
    # The default busy timeout is 0, so any momentary contention aborts a write
    # instantly; wait instead of failing the nightly pull.
    conn.execute("PRAGMA busy_timeout=30000")
    # WAL lets the app's read-only connections keep reading while the nightly
    # writer writes. In the old rollback mode the two fought over one exclusive
    # lock. WAL also stops a big perf write from churning the whole
    # several-hundred-MB main file mid-transaction — that journal churn was
    # behind the old "disk I/O error" that killed the targeting write. The mode
    # is a persistent property of the file, so this is a no-op once set; leaving
    # it here re-asserts it if a file is ever reverted. Read-only opens above
    # never reach this line (they cannot set journal_mode anyway).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def target_mirror_rows(clauses, keywords, now=None):
    """API targeting clauses + keywords → `targets` rows (one shared mirror —
    both carry bid/state, they just come from two endpoints)."""
    now = now or _now()
    rows = []
    for c in clauses:
        expr = c.get("expression") or []
        text = " ".join(
            f"{e.get('type')}={e['value']}" if e.get("value") else str(e.get("type"))
            for e in expr) or None
        rows.append((str(c.get("targetId")), str(c.get("campaignId")),
                     str(c.get("adGroupId")), "target", text, None,
                     c.get("state"), c.get("bid"), now))
    for k in keywords:
        rows.append((str(k.get("keywordId")), str(k.get("campaignId")),
                     str(k.get("adGroupId")), "keyword", k.get("keywordText"),
                     k.get("matchType"), k.get("state"), k.get("bid"), now))
    return rows


def store_targets(conn, rows, retries=None, chunk=None):
    """Replace the targets mirror atomically. Delete + insert in ONE
    transaction — a half-replaced mirror would mix two pulls' bids, worse
    than either. Same retry/diagnostics discipline as bulk_write."""
    retries = retries if retries is not None else BULK_RETRIES
    chunk = chunk if chunk is not None else BULK_CHUNK
    rows = list(rows)
    for attempt in range(1, retries + 1):
        try:
            with conn:
                conn.execute("DELETE FROM targets")
                for i in range(0, len(rows), chunk):
                    conn.executemany(
                        "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?,?,?)",
                        rows[i:i + chunk])
            return len(rows)
        except sqlite3.OperationalError as e:
            print(f"  !! TARGETS MIRROR FAILED db={os.path.basename(_conn_db_path(conn))} "
                  f"rows={len(rows)} attempt={attempt}/{retries} "
                  f"err={e} ext={_sqlite_error_detail(e)} {_io_context(conn)}")
            if attempt >= retries:
                raise
            time.sleep(BULK_BACKOFF_SECS * attempt)


def set_local_target_bids(conn, pairs):
    """Write applied bids through to the targets mirror, so DSL previews and
    the app's Bid column stay honest between nightly pulls. pairs =
    [(target_id, bid), …]; ids the mirror doesn't know are ignored."""
    conn.executemany("UPDATE targets SET bid=? WHERE target_id=?",
                     [(float(b), str(t)) for t, b in pairs])
    conn.commit()


def store_asin_econ_snapshot(conn, rows):
    """Bank one export's per-ASIN economics. Idempotent per (export, asin, market)."""
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO asin_econ_snapshot
           (export_date,asin,marketplace,product_type,brand,status,
            list_price,royalty_last30,sales_last30,sales_total)
           VALUES(?,?,?,?,?,?,?,?,?,?)""", rows, "asin_econ_snapshot")


def store_history_monthly(conn, rows):
    """Bank console-exported monthly history. Idempotent per (month, currency)."""
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO ads_history_monthly
           (month,currency,market,impressions,clicks,spend,sales,purchases,units,source_file)
           VALUES(?,?,?,?,?,?,?,?,?,?)""", rows, "ads_history_monthly")


def connect_shared(ro=False):
    """The ACCOUNT-WIDE store, always the default market's DB whatever ADS_MARKET says.

    Most tables here are per-market because Amazon reports per advertising
    profile. The Merch sales report is not: one download covers every
    marketplace at once. Banking it per-market would shard a single dataset
    across six files and lose every row whose market DB was not the one open at
    import time. It lives in one place, keyed by `mkt`, and any market reads it
    from here."""
    path = os.path.join(_HERE, "ads_data.sqlite")
    if ro and os.path.exists(path):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")   # same rationale as connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def store_sales_report_rows(conn, rows):
    """Bank aggregated sales-report rows. Idempotent per (mkt, date, asin, type).

    `rows` must already be summed per key — see sales_import.parse, which folds
    the colour/size variants Amazon emits as separate lines."""
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO sales_report_rows
           (mkt,date,asin,title,product_type,purchased,cancelled,returned,
            revenue,royalty,currency)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""", rows, "sales_report_rows")


def log_import(conn, kind, filename, period_start=None, period_end=None,
               rows_in_file=None, rows_banked=None, note=""):
    """Record an operator import so provenance survives the file being moved."""
    conn.execute(
        """INSERT OR REPLACE INTO imported_files
           (kind,filename,imported_at,period_start,period_end,
            rows_in_file,rows_banked,note) VALUES(?,?,?,?,?,?,?,?)""",
        (kind, filename, _now(), period_start, period_end,
         rows_in_file, rows_banked, note))
    conn.commit()


def imported_file_log(conn, kind=None, limit=50):
    sql = "SELECT kind,filename,imported_at,period_start,period_end,rows_in_file,rows_banked,note FROM imported_files"
    args = ()
    if kind:
        sql += " WHERE kind=?"
        args = (kind,)
    sql += " ORDER BY imported_at DESC LIMIT ?"
    return conn.execute(sql, args + (limit,)).fetchall()


# ---- resilient bulk writes ---------------------------------------------------
# A single 40k-row executemany against the 400MB US DB is what kept dying with
# "disk I/O error" on the nightly pull, losing the whole targeting snapshot.
# Chunking bounds the page/journal churn per statement, the retry rides out a
# transient failure, and the diagnostics record the extended SQLite error code
# so a repeat failure identifies the actual syscall instead of just "IOERR".

BULK_CHUNK = 5000
BULK_RETRIES = 3
BULK_BACKOFF_SECS = 2.0


def _sqlite_error_detail(exc):
    """Extended SQLite error name/code, when the interpreter exposes them.

    Python 3.11+ carries sqlite_errorname/sqlite_errorcode. The nightly job runs
    under Xcode's Python 3.9, which does not, so this says so rather than
    pretending to know the subcode."""
    name = getattr(exc, "sqlite_errorname", None)
    code = getattr(exc, "sqlite_errorcode", None)
    if name or code is not None:
        return f"{name or '?'}/{code if code is not None else '?'}"
    return "ext-code needs python>=3.11"


def _conn_db_path(conn):
    """The file a connection actually writes. connect_shared() pins the US DB
    whatever ADS_MARKET says, so diagnostics must ask the connection — the
    module-level DB_PATH can name the wrong database in a failure line."""
    try:
        for _, name, file in conn.execute("PRAGMA database_list"):
            if name == "main" and file:
                return file
    except sqlite3.Error:
        pass
    return DB_PATH


def _io_context(conn=None):
    """Filesystem facts to log next to a failed write.

    "disk I/O error" is SQLITE_IOERR with the subcode stripped, and Python 3.9
    can't show the subcode — so capture what the OS can tell us instead: free
    space, DB size, and whether a rollback journal was left behind."""
    path = _conn_db_path(conn) if conn is not None else DB_PATH
    bits = [f"sqlite={sqlite3.sqlite_version}"]
    try:
        st = os.statvfs(os.path.dirname(path) or ".")
        bits.append(f"free={st.f_bavail * st.f_frsize / 1e9:.1f}GB")
    except OSError as e:
        bits.append(f"free=? ({e})")
    try:
        bits.append(f"db={os.path.getsize(path) / 1e6:.0f}MB")
    except OSError:
        bits.append("db=?")
    for suffix in ("-journal", "-wal"):
        if os.path.exists(path + suffix):
            bits.append(f"{suffix.lstrip('-')}=present")
    return " ".join(bits)


def bulk_write(conn, sql, rows, label, chunk=BULK_CHUNK, retries=BULK_RETRIES):
    """executemany in bounded chunks inside ONE transaction, with retry.

    All-or-nothing on purpose: a partially stored snapshot would read as real
    data downstream. Raises the last OperationalError if every attempt fails."""
    rows = list(rows)
    for attempt in range(1, retries + 1):
        try:
            with conn:                       # commits on success, rolls back on error
                for i in range(0, len(rows), chunk):
                    conn.executemany(sql, rows[i:i + chunk])
            return len(rows)
        except sqlite3.OperationalError as e:
            print(f"  !! BULK WRITE FAILED [{label}] db={os.path.basename(_conn_db_path(conn))} "
                  f"rows={len(rows)} chunk={chunk} attempt={attempt}/{retries} "
                  f"err={e} ext={_sqlite_error_detail(e)} {_io_context(conn)}")
            if attempt >= retries:
                raise
            time.sleep(BULK_BACKOFF_SECS * attempt)


# ---- perf-snapshot freshness -------------------------------------------------
# Every perf table is filled by its OWN Amazon report job. Those jobs fail
# independently, so the tables drift apart. Reusing one table's MAX(date) to
# query another then matches ZERO rows and the caller reports "no changes"
# instead of "no data" — that silently froze US bids and pauses for four
# nights in Aug 2026. Always resolve the date from the table you are about to
# read, and refuse to act on evidence that is too old.

SNAPSHOT_STALE_AFTER_DAYS = 3

# Amazon attributes a sale back to the click that earned it for up to 30 days,
# so a recent day's sales are incomplete while its spend is already final. A
# rule reading a recent window would see full spend against partial sales and
# pause too eagerly. Rolling windows therefore end this many days ago.
DAILY_ATTRIBUTION_LAG_DAYS = 2

# Amazon's reporting retention starts about 95 days back and rolls forward.
# Asking for more is a promise the data cannot keep.
MAX_DAILY_WINDOW_DAYS = 92


def latest_snapshot(conn, table):
    """Newest snapshot date banked in ONE perf table (None when empty).

    NEVER pass another table's date into a query against `table`."""
    row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
    return row[0] if row else None


def snapshot_age_days(date_str, today=None):
    """Whole days between a 'YYYY-MM-DD' snapshot and today (None if unparseable)."""
    if not date_str:
        return None
    try:
        d = datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None
    return ((today or datetime.date.today()) - d).days


def snapshot_gate(conn, table, stale_after=SNAPSHOT_STALE_AFTER_DAYS, today=None):
    """Fail-closed freshness check for one perf table, mirroring econ_gate's shape.

    Returns {date, ok, reason, age_days}. `ok` is False when the table is empty
    or its newest snapshot is older than `stale_after` days, so callers can
    refuse to write rather than quietly evaluating nothing."""
    date = latest_snapshot(conn, table)
    if not date:
        return {"date": None, "ok": False, "age_days": None,
                "reason": f"{table} has no snapshots banked"}
    age = snapshot_age_days(date, today=today)
    if age is None:
        return {"date": date, "ok": False, "age_days": None,
                "reason": f"{table} newest snapshot '{date}' is not a valid date"}
    if age > stale_after:
        return {"date": date, "ok": False, "age_days": age,
                "reason": (f"{table} newest snapshot is {date} ({age}d old, limit "
                           f"{stale_after}d) — the report job has been failing")}
    return {"date": date, "ok": True, "age_days": age, "reason": ""}


# ---- rolling daily windows ---------------------------------------------------
# target_daily and campaign_daily hold TRUE single days, so a rolling window is
# a date range rather than one snapshot date. Two things can go wrong, and both
# have to fail closed.
#
# The window can have holes. If a report job dies and a week holds six days, a
# naive SUM calls six days a week: every entity looks about 14% cheaper and 14%
# worse-selling than it was, and the rules act on that. A wrong answer is worse
# than a refusal.
#
# The table can be stale. That is the same condition snapshot_gate already
# guards, so it reuses the same threshold rather than inventing a second one.


def daily_window(days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None):
    """The inclusive (start, end) ISO dates of a rolling window of `days`,
    ending `lag` days before today. Both ends are inclusive, so a 7-day window
    spans 7 dates."""
    today = today or datetime.date.today()
    end = today - datetime.timedelta(days=lag)
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def window_dates(spec, today=None, lag=DAILY_ATTRIBUTION_LAG_DAYS):
    """Resolve an inline baseline/trend window spec to inclusive (start, end)
    ISO dates. Powers `metric IN <window>` in the rules DSL.

      ("rolling", n)   -> a settled n-day window ending `lag` days back
      ("range", a, b)  -> FROM a DAYS AGO TO b DAYS AGO — unlagged day offsets;
                          the older bound is max(a, b), so order does not matter
      ("day", n)       -> the single unlagged day n days ago
      ("yesterday",)   -> the latest settled day, `lag` days back

    Day-offset forms are UNLAGGED on purpose (they name exact past days), matching
    how MerchDash treats `N DAYS AGO`. Returns None for an unknown spec."""
    today = today or datetime.date.today()

    def ago(n):
        return (today - datetime.timedelta(days=int(n))).isoformat()

    kind = spec[0]
    if kind == "rolling":
        return daily_window(int(spec[1]), lag=lag, today=today)
    if kind == "range":
        a, b = int(spec[1]), int(spec[2])
        return ago(max(a, b)), ago(min(a, b))
    if kind == "day":
        return ago(spec[1]), ago(spec[1])
    if kind == "yesterday":
        return ago(lag), ago(lag)
    return None


def daily_window_gate(conn, table, days, lag=DAILY_ATTRIBUTION_LAG_DAYS, today=None):
    """Fail-closed completeness check for one rolling window, mirroring
    snapshot_gate's shape. `ok` is False when the window reaches past Amazon's
    retention, when the newest banked day is stale, or when any day inside the
    window has no rows."""
    result = {"table": table, "days_requested": days, "days_banked": 0,
              "missing": [], "start": None, "end": None, "ok": False, "reason": ""}
    if days < 1 or days > MAX_DAILY_WINDOW_DAYS:
        result["reason"] = (f"a {days}-day window is outside what Amazon keeps "
                            f"(1 to {MAX_DAILY_WINDOW_DAYS} days)")
        return result

    start, end = daily_window(days, lag=lag, today=today)
    result["start"], result["end"] = start, end

    present = {r[0] for r in conn.execute(
        f"SELECT DISTINCT date FROM {table} WHERE date BETWEEN ? AND ?", (start, end))}
    result["days_banked"] = len(present)

    newest = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()[0]
    if not newest:
        result["reason"] = f"{table} has no days banked"
        return result

    age = snapshot_age_days(newest, today=today)
    limit = lag + SNAPSHOT_STALE_AFTER_DAYS
    if age is None:
        result["reason"] = f"{table} newest day '{newest}' is not a valid date"
        return result
    if age > limit:
        result["reason"] = (f"{table} newest day is {newest} ({age}d old, limit "
                            f"{limit}d) — stale, the report job has been failing")
        return result

    if len(present) < days:
        wanted = datetime.date.fromisoformat(start)
        last = datetime.date.fromisoformat(end)
        missing = []
        while wanted <= last:
            if wanted.isoformat() not in present:
                missing.append(wanted.isoformat())
            wanted += datetime.timedelta(days=1)
        result["missing"] = missing[:5]
        result["reason"] = (f"{table} covers {len(present)} of {days} days in "
                            f"{start}..{end} — summing a short window would "
                            f"understate every entity")
        return result

    result["ok"] = True
    return result


def _migrate(conn):
    # additive migrations for existing DBs
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ad_group_product)")]
    if "lifetime_sales" not in cols:
        conn.execute("ALTER TABLE ad_group_product ADD COLUMN lifetime_sales INTEGER")
        conn.commit()
    # price-aware economics (PLAN.md 2026-07-12): append-only price history +
    # engine metadata. Writer-owned; read-only connections that find these
    # absent must treat economics as UNAVAILABLE (fail closed), never as
    # "no history".
    conn.execute("""CREATE TABLE IF NOT EXISTS price_change (
        asin TEXT, ad_group_id TEXT, old_cents INTEGER, new_cents INTEGER,
        observed_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS engine_meta (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_change_observed"
                 " ON price_change(observed_at)")
    # KENP (Kindle Edition Normalized Pages) — KDP books earn from pages read
    # through Kindle Unlimited, not just outright sales. Additive + nullable, so
    # existing rows and every Merch INSERT are untouched; a Merch pull never
    # requests these columns, so _f() stores 0 there. Only the KDP report asks
    # for them (phase0_pull.py, kind-aware).
    for t in ("campaign_perf", "targeting_perf", "search_term_perf"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        if "kenp_read" not in cols:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN kenp_read REAL")
            conn.execute(f"ALTER TABLE {t} ADD COLUMN kenp_royalties REAL")
    conn.commit()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- engine metadata (freshness stamps, STALE marker, deployment stamp) --------

def econ_tables_present(conn):
    rows = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name IN ('price_change','engine_meta')")}
    return len(rows) == 2


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM engine_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn, key, value):
    conn.execute("""INSERT INTO engine_meta(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                    updated_at=excluded.updated_at""", (key, str(value), _now()))
    conn.commit()


# --- per-market max-bid ceiling (Spec A) ----------------------------------------

_BID_CEILING_KEYS = {"target": "max_bid_target", "keyword": "max_bid_keyword",
                     "budget": "max_budget_daily"}


def get_bid_ceiling(conn, surface):
    """Per-market bid ceiling for a write surface ('target' or 'keyword').
    Returns a float dollar amount or None when unset."""
    key = _BID_CEILING_KEYS.get(surface)
    if key is None:
        raise ValueError(f"unknown bid-ceiling surface {surface!r}")
    v = meta_get(conn, key)
    return float(v) if v not in (None, "") else None


def set_bid_ceiling(conn, surface, value):
    """Set (float) or clear (None) a per-market bid ceiling."""
    key = _BID_CEILING_KEYS.get(surface)
    if key is None:
        raise ValueError(f"unknown bid-ceiling surface {surface!r}")
    if value is None:
        conn.execute("DELETE FROM engine_meta WHERE key=?", (key,))
        conn.commit()
    else:
        meta_set(conn, key, f"{float(value):.2f}")


# --- per-market portfolio monthly cap (R8 spend guard) --------------------------
# Daily budgets only stop Amazon throttling one campaign; this is the pooled
# month-to-date ceiling the operator can afford to lose. Nothing enforces it as a
# hard stop — the alerts feed warns as month-to-date spend nears it.
_PORTFOLIO_CAP_KEY = "portfolio_monthly_cap"


def get_portfolio_cap(conn):
    """The market's monthly portfolio-spend cap in dollars, or None when unset."""
    v = meta_get(conn, _PORTFOLIO_CAP_KEY)
    return float(v) if v not in (None, "") else None


def set_portfolio_cap(conn, value):
    """Set (float) or clear (None) the market's monthly portfolio cap."""
    if value is None:
        conn.execute("DELETE FROM engine_meta WHERE key=?", (_PORTFOLIO_CAP_KEY,))
        conn.commit()
    else:
        meta_set(conn, _PORTFOLIO_CAP_KEY, f"{float(value):.2f}")


# --- price-change history (transition windows) ----------------------------------

TRANSITION_DAYS = 30


def log_price_changes(conn, rows):
    """rows: list of (asin, ad_group_id, old_cents_or_None, new_cents)."""
    now = _now()
    conn.executemany(
        "INSERT INTO price_change(asin,ad_group_id,old_cents,new_cents,observed_at)"
        " VALUES(?,?,?,?,?)",
        [(r[0], str(r[1]), r[2], r[3], now) for r in rows])
    conn.commit()


def active_price_changes(conn, now=None):
    """asin -> list of (old_cents, new_cents, observed_at) whose 30-day window is
    still active. old_cents IS NULL == transition-unknown (deployment seed).
    Raises sqlite3.OperationalError if the table is absent — callers must treat
    that as economics-unavailable (fail closed)."""
    now = now or datetime.datetime.now()
    cutoff = (now - datetime.timedelta(days=TRANSITION_DAYS)).isoformat(timespec="seconds")
    out = {}
    for asin, old_c, new_c, at in conn.execute(
        "SELECT asin, old_cents, new_cents, observed_at FROM price_change"
        " WHERE observed_at >= ?", (cutoff,)):
        out.setdefault(asin, []).append((old_c, new_c, at))
    return out


def get_design_map(conn):
    """ad_group_id -> dict(asin, product_type, brand, list_price, lifetime_sales).
    asin IS NULL == multi-ASIN cohort group (scavenger) — never a per-design row."""
    out = {}
    for agid, asin, pt, brand, price, life in conn.execute(
        "SELECT ad_group_id, asin, product_type, brand, list_price, lifetime_sales"
        " FROM ad_group_product"):
        out[str(agid)] = {"asin": asin, "product_type": pt, "brand": brand,
                          "list_price": price, "lifetime_sales": life or 0}
    return out


# --- writes_log detail: human prefix + optional econ_v1 JSON suffix ------------
# Every exact-match consumer of `detail` MUST go through detail_prefix() —
# phase2's negative dedup, the negatives inventory, bid-history parsing —
# because econ-aware writes append a machine suffix after the human text.

ECON_SUFFIX_MARK = " econ_v1="
_SUFFIX_RE = re.compile(r" \w+_v1=")


def detail_prefix(detail):
    """The human-readable prefix of a writes_log detail, stripping any trailing
    machine suffix(es) of the form ' <name>_v1={...}' (econ_v1, cap_v1, ...).
    Safe on old-format rows and None."""
    if not detail:
        return detail
    m = _SUFFIX_RE.search(detail)
    return detail[:m.start()] if m else detail


def econ_suffix(price_cents=None, royalty_cents=None, break_even=None,
                target=None, src=None, model=None, export_ts=None):
    """Compact versioned economics payload appended to writes_log.detail."""
    import json as _json
    payload = {k: v for k, v in [
        ("price", price_cents), ("roy", royalty_cents),
        ("be", round(break_even, 4) if break_even is not None else None),
        ("tgt", round(target, 4) if target is not None else None),
        ("src", src), ("model", model), ("export", export_ts),
    ] if v is not None}
    return ECON_SUFFIX_MARK + _json.dumps(payload, separators=(",", ":"))


def upsert_campaigns(conn, rows):
    now = _now()
    conn.executemany(
        """INSERT INTO campaigns(campaign_id,name,state,targeting_type,daily_budget,bidding_strategy,pulled_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(campaign_id) DO UPDATE SET
             name=excluded.name, state=excluded.state, targeting_type=excluded.targeting_type,
             daily_budget=excluded.daily_budget, bidding_strategy=excluded.bidding_strategy,
             pulled_at=excluded.pulled_at""",
        [(c.get("campaignId"), c.get("name"), c.get("state"),
          c.get("targetingType"),
          (c.get("budget") or {}).get("budget") if isinstance(c.get("budget"), dict) else c.get("budget"),
          (c.get("dynamicBidding") or {}).get("strategy"), now) for c in rows],
    )
    conn.commit()


def upsert_ad_groups(conn, rows):
    now = _now()
    # ~105k rows for US — the other write that kept dying with "disk I/O error".
    bulk_write(
        conn,
        """INSERT INTO ad_groups(ad_group_id,campaign_id,name,state,default_bid,pulled_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(ad_group_id) DO UPDATE SET
             campaign_id=excluded.campaign_id, name=excluded.name, state=excluded.state,
             default_bid=excluded.default_bid, pulled_at=excluded.pulled_at""",
        [(a.get("adGroupId"), a.get("campaignId"), a.get("name"),
          a.get("state"), a.get("defaultBid"), now) for a in rows],
        "ad_groups")


def _f(row, *keys):
    """First present numeric value among keys, else 0."""
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return 0


def store_campaign_perf(conn, rows, end_date):
    out = []
    for r in rows:
        cost = _f(r, "cost", "spend")
        sales = _f(r, "sales30d", "sales", "sales14d", "sales7d")
        out.append((end_date, r.get("campaignId"), r.get("campaignName"),
                    _f(r, "impressions"), _f(r, "clicks"), cost,
                    _f(r, "purchases30d", "purchases", "orders"), sales,
                    round(cost / sales, 4) if sales else None,
                    _f(r, "kindleEditionNormalizedPagesRead14d"),
                    _f(r, "kindleEditionNormalizedPagesRoyalties14d")))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO campaign_perf
           (date,campaign_id,campaign_name,impressions,clicks,cost,orders,sales,acos,kenp_read,kenp_royalties)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""", out, "campaign_perf")


def store_targeting_perf(conn, rows, end_date):
    out = []
    for r in rows:
        cost = _f(r, "cost", "spend")
        sales = _f(r, "sales30d", "sales", "sales14d", "sales7d")
        out.append((end_date, r.get("campaignId"), r.get("adGroupId"),
                    r.get("targeting"), r.get("matchType"), r.get("keywordId") or r.get("targetId"),
                    _f(r, "impressions"), _f(r, "clicks"), cost,
                    _f(r, "purchases30d", "purchases", "orders"), sales,
                    round(cost / sales, 4) if sales else None,
                    _f(r, "kindleEditionNormalizedPagesRead14d"),
                    _f(r, "kindleEditionNormalizedPagesRoyalties14d")))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO targeting_perf
           (date,campaign_id,ad_group_id,targeting,match_type,target_id,impressions,clicks,cost,orders,sales,acos,kenp_read,kenp_royalties)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out, "targeting_perf")


def store_purchased_product(conn, rows, end_date):
    """Cross-purchase rows from the spPurchasedProduct report.

    Amazon returns one row per (target, advertised ASIN, purchased ASIN). Rows
    where the two ASINs differ are the interesting ones: the ad sold something
    it was not advertising. Stored whole so the split can be made at read time.
    """
    out = []
    for r in rows:
        out.append((end_date, r.get("campaignId"), r.get("adGroupId"),
                    r.get("keywordId"), r.get("keyword"), r.get("keywordType"),
                    r.get("matchType"),
                    r.get("advertisedAsin"), r.get("purchasedAsin"),
                    _f(r, "unitsSoldClicks30d", "unitsSoldClicks14d"),
                    _f(r, "sales30d", "sales14d"),
                    _f(r, "purchases30d", "purchases14d"),
                    _f(r, "unitsSoldOtherSku30d", "unitsSoldOtherSku14d"),
                    _f(r, "salesOtherSku30d", "salesOtherSku14d"),
                    _f(r, "purchasesOtherSku30d", "purchasesOtherSku14d")))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO purchased_product
           (date,campaign_id,ad_group_id,keyword_id,keyword,keyword_type,match_type,
            advertised_asin,purchased_asin,units_sold,sales,purchases,
            units_sold_other_sku,sales_other_sku,purchases_other_sku)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out, "purchased_product")


def store_search_term_perf(conn, rows, end_date):
    out = []
    for r in rows:
        cost = _f(r, "cost", "spend")
        sales = _f(r, "sales30d", "sales", "sales14d", "sales7d")
        out.append((end_date, r.get("campaignId"), r.get("adGroupId"),
                    r.get("searchTerm"), r.get("targeting"), r.get("matchType"),
                    _f(r, "impressions"), _f(r, "clicks"), cost,
                    _f(r, "purchases30d", "purchases", "orders"), sales,
                    round(cost / sales, 4) if sales else None,
                    _f(r, "kindleEditionNormalizedPagesRead14d"),
                    _f(r, "kindleEditionNormalizedPagesRoyalties14d")))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO search_term_perf
           (date,campaign_id,ad_group_id,search_term,targeting,match_type,impressions,clicks,cost,orders,sales,acos,kenp_read,kenp_royalties)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out, "search_term_perf")


def log_pull(conn, kind, rows, note=""):
    conn.execute("INSERT INTO pull_log(pulled_at,kind,rows,note) VALUES(?,?,?,?)",
                 (_now(), kind, rows, note))
    conn.commit()


# ---- async report job tracking (for resumable pulls) --------------------
def save_report_job(conn, report_type, report_id, window_end, status="PENDING"):
    conn.execute(
        """INSERT INTO report_jobs(report_type,report_id,status,requested_at,window_end,downloaded)
           VALUES(?,?,?,?,?,0)
           ON CONFLICT(report_type) DO UPDATE SET
             report_id=excluded.report_id, status=excluded.status,
             requested_at=excluded.requested_at, window_end=excluded.window_end, downloaded=0""",
        (report_type, report_id, status, _now(), window_end))
    conn.commit()


def get_report_job(conn, report_type):
    return conn.execute(
        "SELECT report_type,report_id,status,requested_at,window_end,downloaded FROM report_jobs WHERE report_type=?",
        (report_type,)).fetchone()


def expire_stale_report_jobs(conn, hours=48):
    """Retire undownloaded report jobs past `hours` — they are dead weight the
    pending counter would otherwise flag forever (report URLs also stop being
    downloadable after a few days, so there is nothing left to recover)."""
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")
    cur = conn.execute(
        """UPDATE report_jobs SET status='EXPIRED'
           WHERE downloaded=0 AND requested_at < ?
             AND status NOT IN ('COMPLETED','EXPIRED')""", (cutoff,))
    conn.commit()
    return cur.rowcount


def set_report_status(conn, report_type, status, downloaded=None):
    if downloaded is None:
        conn.execute("UPDATE report_jobs SET status=? WHERE report_type=?", (status, report_type))
    else:
        conn.execute("UPDATE report_jobs SET status=?, downloaded=? WHERE report_type=?",
                     (status, downloaded, report_type))
    conn.commit()


def log_write(conn, action, entity_type, entity_id, detail, prev_state, result):
    conn.execute(
        """INSERT INTO writes_log(applied_at,action,entity_type,entity_id,detail,prev_state,result)
           VALUES(?,?,?,?,?,?,?)""",
        (_now(), action, entity_type, entity_id, detail, prev_state, result))
    conn.commit()


def set_local_ad_group_state(conn, ad_group_ids, state):
    """Mirror a state change we just pushed to Amazon onto the local ad_groups table,
    so the cached snapshot doesn't disagree with Amazon until the next morning pull."""
    if not ad_group_ids:
        return
    conn.executemany("UPDATE ad_groups SET state=? WHERE ad_group_id=?",
                     [(state, str(i)) for i in ad_group_ids])
    conn.commit()


def set_local_campaign_state(conn, campaign_ids, state):
    """Mirror a campaign state change we just pushed to Amazon onto the local campaigns table."""
    if not campaign_ids:
        return
    conn.executemany("UPDATE campaigns SET state=? WHERE campaign_id=?",
                     [(state, str(i)) for i in campaign_ids])
    conn.commit()


def upsert_ad_group_products(conn, rows):
    """rows: list of (ad_group_id, asin, product_type, brand, list_price, lifetime_sales)."""
    now = _now()
    conn.executemany(
        """INSERT INTO ad_group_product(ad_group_id,asin,product_type,brand,list_price,lifetime_sales,mapped_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(ad_group_id) DO UPDATE SET
             asin=excluded.asin, product_type=excluded.product_type, brand=excluded.brand,
             list_price=excluded.list_price, lifetime_sales=excluded.lifetime_sales, mapped_at=excluded.mapped_at""",
        [(str(r[0]), r[1], r[2], r[3], r[4], (r[5] if len(r) > 5 else None), now) for r in rows])
    conn.commit()


def get_product_map(conn):
    """ad_group_id -> product_type."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT ad_group_id, product_type FROM ad_group_product").fetchall()}


def get_lifetime_map(conn):
    """ad_group_id -> lifetime_sales (units). Missing/None -> 0."""
    return {r[0]: (r[1] or 0) for r in conn.execute(
        "SELECT ad_group_id, lifetime_sales FROM ad_group_product").fetchall()}


def store_market_econ(conn, market, rows):
    """rows: list of (product_type, royalty, price, break_even, n)."""
    now = _now()
    conn.executemany(
        """INSERT INTO market_econ(market,product_type,royalty,price,break_even,n,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(market,product_type) DO UPDATE SET
             royalty=excluded.royalty, price=excluded.price, break_even=excluded.break_even,
             n=excluded.n, updated_at=excluded.updated_at""",
        [(market, r[0], r[1], r[2], r[3], r[4], now) for r in rows])
    conn.commit()


def get_market_econ(conn, market):
    """market -> {product_type: {royalty, price, break_even, n}}."""
    out = {}
    for r in conn.execute(
        "SELECT product_type,royalty,price,break_even,n FROM market_econ WHERE market=?", (market,)):
        out[r[0]] = dict(royalty=r[1], price=r[2], break_even=r[3], n=r[4])
    return out


def store_period_total(conn, period, window, cost, sales, orders):
    """Upsert one period's account totals ('daily' or 'mtd')."""
    conn.execute(
        """INSERT INTO period_totals(period,window,cost,sales,orders,pulled_at) VALUES(?,?,?,?,?,?)
           ON CONFLICT(period) DO UPDATE SET
             window=excluded.window, cost=excluded.cost, sales=excluded.sales,
             orders=excluded.orders, pulled_at=excluded.pulled_at""",
        (period, window, cost, sales, orders, _now()))
    conn.commit()


def get_period_total(conn, period):
    """-> (window, cost, sales, orders) or None."""
    return conn.execute(
        "SELECT window,cost,sales,orders FROM period_totals WHERE period=?", (period,)).fetchone()


def _ensure_daily_totals_cols(conn):
    """Lazy migration: add impressions/clicks/units to pre-existing daily_totals."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(daily_totals)")}
    for col in ("impressions", "clicks", "units"):
        if col not in have:
            conn.execute(f"ALTER TABLE daily_totals ADD COLUMN {col} INTEGER")


def store_daily_total(conn, date, cost, sales, orders,
                      impressions=None, clicks=None, units=None):
    """Bank ONE calendar day's true account total, keyed by date, so a real per-day history
    accrues over time (the single 'daily' period_total only holds the latest). Enables a
    trustworthy 7-day rolling ACOS + the per-day metric chart. impressions/clicks/units are
    optional (older callers don't pass them); COALESCE keeps any already-banked value rather
    than nulling it when a caller omits them."""
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_totals (
        date TEXT PRIMARY KEY, cost REAL, sales REAL, orders INTEGER, pulled_at TEXT)""")
    _ensure_daily_totals_cols(conn)
    conn.execute(
        """INSERT INTO daily_totals(date,cost,sales,orders,impressions,clicks,units,pulled_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             cost=excluded.cost, sales=excluded.sales, orders=excluded.orders,
             impressions=COALESCE(excluded.impressions, daily_totals.impressions),
             clicks=COALESCE(excluded.clicks, daily_totals.clicks),
             units=COALESCE(excluded.units, daily_totals.units),
             pulled_at=excluded.pulled_at""",
        (date, cost, sales, orders, impressions, clicks, units, _now()))
    conn.commit()


def store_campaign_daily(conn, rows):
    """Bulk-upsert per-(campaign, date) totals. rows: iterables of
    (date, campaign_id, campaign_name, cost, sales, orders, impressions, clicks, units)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS campaign_daily (
        date TEXT, campaign_id TEXT, campaign_name TEXT,
        cost REAL, sales REAL, orders INTEGER,
        impressions INTEGER, clicks INTEGER, units INTEGER, pulled_at TEXT,
        PRIMARY KEY (date, campaign_id))""")
    now = _now()
    conn.executemany(
        """INSERT INTO campaign_daily(date,campaign_id,campaign_name,cost,sales,orders,
                                      impressions,clicks,units,pulled_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(date,campaign_id) DO UPDATE SET
             campaign_name=excluded.campaign_name, cost=excluded.cost, sales=excluded.sales,
             orders=excluded.orders, impressions=excluded.impressions,
             clicks=excluded.clicks, units=excluded.units, pulled_at=excluded.pulled_at""",
        [(*r, now) for r in rows])
    conn.commit()


def store_target_daily(conn, rows, end_date=None):
    """Bulk-upsert true per-day per-target rows from a DAILY spTargeting report.

    `end_date` is accepted and ignored so this matches the signature every other
    entry in phase0_pull.STORERS has. It must be ignored: the report returns one
    row per target PER DAY, so each row's own `date` is the truth. Using the
    report's end date would collapse the whole window onto a single day.

    Re-running is idempotent by design. The Monday true-up re-reads days that
    are already banked, because their sales have grown since.
    """
    out = []
    for r in rows:
        date = r.get("date")
        if not date:
            continue        # cannot be banked as a day; a guessed date is worse
        cost = _f(r, "cost", "spend")
        sales = _f(r, "sales30d", "sales", "sales14d", "sales7d")
        out.append((date, r.get("campaignId"), r.get("adGroupId"),
                    r.get("targeting"), r.get("matchType"),
                    r.get("keywordId") or r.get("targetId"),
                    _f(r, "impressions"), _f(r, "clicks"), cost,
                    _f(r, "purchases30d", "purchases", "orders"), sales,
                    round(cost / sales, 4) if sales else None, _now()))
    return bulk_write(
        conn,
        """INSERT OR REPLACE INTO target_daily
           (date,campaign_id,ad_group_id,targeting,match_type,target_id,
            impressions,clicks,cost,orders,sales,acos,pulled_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", out, "target_daily")


def upsert_harvest(conn, rows):
    """rows: dicts with term, agid, kind, pt, cid, clicks, orders, sales, acos, cpc.
    Accumulates winners; keeps first_seen and the 'promoted' flag across runs."""
    now = _now()
    conn.executemany(
        """INSERT INTO harvest_log
           (search_term,source_ad_group_id,kind,product_type,source_campaign_id,
            clicks,orders,sales,acos,cpc,first_seen,last_seen,promoted)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
           ON CONFLICT(search_term,source_ad_group_id) DO UPDATE SET
             clicks=excluded.clicks, orders=excluded.orders, sales=excluded.sales,
             acos=excluded.acos, cpc=excluded.cpc, kind=excluded.kind,
             product_type=excluded.product_type, last_seen=excluded.last_seen""",
        [(r["term"], r["agid"], r["kind"], r["pt"], r["cid"], r["clicks"], r["orders"],
          r["sales"], r["acos"], r["cpc"], now, now) for r in rows])
    conn.commit()
