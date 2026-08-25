#!/bin/bash
# Build a clean, publishable snapshot of this repository.
#
#   bash scripts/make_public_snapshot.sh                     # build + verify only
#   bash scripts/make_public_snapshot.sh --owner my-gh-name  # fill in the repo owner
#   bash scripts/make_public_snapshot.sh --out /tmp/pub      # choose the output folder
#
# What it does:
#   1. Exports the tracked files at HEAD into an empty folder (no history, no
#      working-tree junk, nothing gitignored).
#   2. Deletes files that must never be published — operator data, notes about
#      other people, and briefs for other private projects.
#   3. VERIFIES the result: no secrets, no databases, no home paths, no real
#      ASINs, no revenue figures. It refuses to finish if a check fails.
#   4. Creates a fresh git repository with a single initial commit.
#
# The private repository keeps its full history and all its data. Only the
# snapshot is meant to be pushed anywhere public.
#
# Re-run this whenever you want to update the public repo, then push the result.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${TMPDIR:-/tmp}/merch-ads-public"
OWNER=""      # GitHub owner (user or org). Replaces zdufs in the docs.
VERIFY_ONLY=0 # re-run the checks against an existing tree and exit (the pre-push hook)
# Commit author for the published snapshot. Git records whatever your global
# config says, and GitHub shows it on every commit forever — so a private repo's
# author line becomes public the moment the snapshot is pushed. Default to the
# GitHub noreply form; pass --author-email to override.
AUTHOR_NAME=""
AUTHOR_EMAIL=""
NAME="merch-ads"

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="$2"; shift 2 ;;
    --owner)   OWNER="$2"; shift 2 ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --author-name)  AUTHOR_NAME="$2"; shift 2 ;;
    --author-email) AUTHOR_EMAIL="$2"; shift 2 ;;
    --name)    NAME="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)         echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# --out must never name a folder that holds real work.
#
# Every destructive step in this script targets $OUT: the private-file removal
# in section 2, and the generated-file cleanup at the end of verify_tree. Both
# are correct for a snapshot and catastrophic for the operator's own checkout,
# which holds seven market databases, the Stream database, the catalogue cache,
# `.env` and outputs/. One mistyped --out is the whole dataset.
#
# A snapshot NEVER contains `.env` — it ships `.env.example` — and it is never
# the repository itself. Those two facts are the test. Refuse before anything
# is read, let alone removed. (A leftover *.sqlite from a previous run is NOT a
# refusal: the build creates those itself, and treating them as operator data
# would refuse every rebuild.) Added after review, 2026-08-23.
# ---------------------------------------------------------------------------
_abs() { cd "$1" 2>/dev/null && pwd -P; }
if [ -n "$OUT" ] && [ -d "$OUT" ]; then
  _out_abs=$(_abs "$OUT"); _repo_abs=$(_abs "$REPO")
  if [ -n "$_out_abs" ] && [ "$_out_abs" = "$_repo_abs" ]; then
    echo "REFUSING: --out is this repository itself ($_out_abs)." >&2
    echo "          Every step here deletes from --out. Choose another folder." >&2
    exit 2
  fi
  if [ -e "$_out_abs/.env" ]; then
    echo "REFUSING: $_out_abs holds a .env, so it is somebody's working folder," >&2
    echo "          not a snapshot. Every step here deletes from --out." >&2
    exit 2
  fi
fi

# ---------------------------------------------------------------------------
# Files that must never be published.
#
# Each line is a path relative to the repository root. Add to this list rather
# than deleting things by hand, so the next snapshot stays clean too.
# ---------------------------------------------------------------------------
# The retired strategy's name is built from halves here for the same reason the
# company name is below: written whole, it would appear in the tree this script
# verifies, and the check would flag its own exclusion list.
RETIRED='ta'"mas"
PRIVATE=(
  # Generated business intelligence — your designs, your demand, your numbers.
  "${RETIRED}_candidates.md"     # proven-seller list with real ASINs, CVR and TRAZ
  "design-briefs.md"             # converting search terms — competitive intelligence

  # Notes about a third party, gathered from a private community.
  "docs/csmetro-insights.md"

  # A task brief written for a different, private repository.
  "merchpirate-codex-brief.md"

  # Superseded working plans. The dated specs in docs/superpowers/ replace them.
  "PLAN.md"
  "PLAN-REVIEW-LOG.md"

  # A one-off migration analysis tied to PLAN.md. Carries a sample of real ASINs
  # and is meaningless without this operation's own database.
  "scripts/shadow_econ.py"

  # Retired code kept only for reference. Carries hardcoded home paths and ASINs.
  "attic"

  # Dated internal planning documents. They record a working process and the
  # branch names of the day, which is exactly why they must not be edited to
  # match today's naming — and exactly why a stranger does not need them.
  "docs/superpowers"

  # Dated internal analyses of one account's real money. Each one reconciles
  # this operation's own spend, sales and units against the Amazon console, or
  # names a design and what it has sold. They are excluded rather than
  # rewritten on purpose: the figures ARE the analysis, so scrubbing them
  # leaves a document that argues from numbers it no longer shows, and a
  # stranger cannot use either version.
  "docs/console-reconciliation-2026-08-24.md"
  "docs/rejected-product-ads-2026-08-25.md"
)

if [ "$VERIFY_ONLY" = "1" ]; then
  [ -d "$OUT" ] || { echo "No such tree: $OUT" >&2; exit 2; }
  echo "==> Re-verifying $OUT"
else
echo "==> Building snapshot"
echo "    from: $REPO"
echo "    into: $OUT"
echo

if [ -e "$OUT" ]; then
  BACKUP="$OUT.previous.$$"
  echo "    (moving the existing folder aside to $BACKUP)"
  mv "$OUT" "$BACKUP"
fi
mkdir -p "$OUT"

# ---------------------------------------------------------------------------
# 1. Export tracked files at HEAD. Nothing gitignored can come along.
# ---------------------------------------------------------------------------
git -C "$REPO" archive HEAD | tar -x -C "$OUT"
echo "==> Exported $(find "$OUT" -type f | wc -l | tr -d ' ') tracked files"
fi

# ---------------------------------------------------------------------------
# 2. Remove the private files.
# ---------------------------------------------------------------------------
if [ "$VERIFY_ONLY" = "1" ]; then
  # Verification must not touch the tree it is judging. Point --verify-only at
  # the wrong folder and the removal below would delete real files.
  echo "==> Read-only check (no files are removed or rewritten)"
else
echo "==> Removing private files"
for p in "${PRIVATE[@]}"; do
  if [ -e "$OUT/$p" ]; then
    rm -rf "${OUT:?}/$p"
    echo "    removed  $p"
  fi
done
fi

# ---------------------------------------------------------------------------
# 3. Fill in the repository owner, if one was given.
# ---------------------------------------------------------------------------
if [ "$VERIFY_ONLY" = "1" ]; then
  :   # no rewrites while verifying
elif [ -n "$OWNER" ]; then
  echo "==> Setting repository owner to $OWNER/$NAME"
  # -I skips binary files so an .icns or a .png is never rewritten.
  grep -rIl 'zdufs' "$OUT" 2>/dev/null | while read -r f; do
    /usr/bin/sed -i '' -e "s#zdufs/merch-ads#$OWNER/$NAME#g" -e "s#zdufs#$OWNER#g" "$f" 2>/dev/null \
      || sed -i -e "s#zdufs/merch-ads#$OWNER/$NAME#g" -e "s#zdufs#$OWNER#g" "$f"
  done
else
  echo "==> No --owner given; docs keep the zdufs placeholder"
fi

# Resolve the commit author. gh knows the numeric id the noreply form needs.
if [ -z "$AUTHOR_EMAIL" ] && command -v gh >/dev/null 2>&1; then
  _id=$(gh api user --jq '.id' 2>/dev/null || true)
  _login=$(gh api user --jq '.login' 2>/dev/null || true)
  [ -n "$_id" ] && [ -n "$_login" ] && AUTHOR_EMAIL="${_id}+${_login}@users.noreply.github.com"
  [ -z "$AUTHOR_NAME" ] && AUTHOR_NAME="$_login"
fi

# ---------------------------------------------------------------------------
# 4. Verify. Any failure here stops the release.
#
# A function, not a straight-line block, because it runs in two places: here
# before the snapshot is committed, and again from the snapshot's own pre-push
# hook. A check that only runs at build time is not a gate — the folder is a git
# repository and `git push` does not care how it was made.
# ---------------------------------------------------------------------------
verify_tree() {
  OUT="$1"
  echo
  echo "==> Verifying"
  FAILED=0

fail() { echo "    FAIL  $1"; FAILED=1; }
pass() { echo "    ok    $1"; }
# A check that could not run. NOT a pass: the release output says "ok" for
# fifteen lines and a silent skip would read as the sixteenth. It does not stop
# the release, because the only check that needs the operator's databases is
# one a stranger rebuilding this snapshot legitimately cannot run.
warn() { echo "    ..    $1"; }

# Where the market databases live. The operator's checkout IS the data folder,
# so a build from there can run the keyword check below. The snapshot's own
# pre-push hook runs from a folder that has no databases by design, and reports
# the check as skipped rather than passing it.
DATA_DIR="${MERCHADS_DATA_DIR:-$REPO}"

# 4a. No secrets, databases, or personal caches.
LEAKS=$(cd "$OUT" && find . -path ./.git -prune -o \( \
      -name '.env' -o -name '*.env' \
   -o -name '*.sqlite' -o -name '*.sqlite-*' -o -name '*.db' \
   -o -name 'kdp_books.json' -o -name 'kdp_titles.json' \
   -o -name 'seasonal.json' \
   -o -name 'export_products_*.csv' -o -name 'SALES_REPORT*' \
   -o -name '.DS_Store' \) -print)
if [ -n "$LEAKS" ]; then fail "secret or data files present:"; echo "$LEAKS" | sed 's/^/          /'
else pass "no secrets, databases or operator data files"; fi

# 4b. No outputs folder.
if [ -d "$OUT/outputs" ]; then fail "outputs/ is present"; else pass "no outputs/ folder"; fi

# 4c. No hardcoded home paths.
#
#     macOS, Linux and Windows shapes. Obvious placeholders are allowed through
#     on purpose: docs/WINDOWS.md has to show a Windows user where their
#     download landed, and `C:\Users\You\Downloads` is documentation, not a
#     leak. The allowance is a short list of names nobody is actually called —
#     a real login still fails here, and check 4c2 below catches the operator's
#     own name whatever shape it is written in.
HOMEPATHS=$(cd "$OUT" && grep -rIn --exclude-dir=.git \
              -e '/Users/[a-z]' -e '/home/[a-z]' -e 'C:\\Users\\[A-Za-z]' \
              . 2>/dev/null \
            | grep -viE '(Users|home)[\\/](You|Your-?Name|YOUR-WINDOWS-NAME|username|user|<user>|name)([\\/]|$)' \
            || true)
if [ -n "$HOMEPATHS" ]; then fail "hardcoded home paths in:"; echo "$HOMEPATHS" | sed 's/^/          /'
else pass "no hardcoded home paths"; fi

# 4c2. The operator's USERNAME, in any spelling.
#
#      Check 4c looks for the SHAPE of a path. That is not the same question as
#      whether the tree names the person, and on 2026-08-23 the difference was
#      live on GitHub: docs/review-2026-08-04.md pointed at
#      `~/.claude/projects/-Users-<name>-Biznis-...`, which is how Claude Code
#      writes a project folder — the slashes are DASHES, so 4c walked straight
#      past it and published the operator's macOS account name.
#
#      So ask the real question instead of guessing at path shapes. The name is
#      taken from the environment, never written down here: this script is part
#      of the tree it greps, and a literal would fail the release on its own
#      source. That has happened three times already, which is why the company
#      name and the retired strategy name are both split into halves below.
#
#      Skipped for a very short or dictionary-common login ("test", "admin",
#      "user"), where the check would match ordinary prose everywhere and be
#      muted within a day.
OPERATOR=$(basename "${HOME:-}" 2>/dev/null || true)
case "$OPERATOR" in
  test|admin|user|users|root|build|runner|ci|home|"") OPERATOR="" ;;
esac
if [ -n "$OPERATOR" ] && [ "${#OPERATOR}" -ge 5 ]; then
  # -i: the same login appears capitalised in places macOS writes it, and a
  # case-sensitive -F walked past every one of them (found by review, 2026-08-23).
  NAMEHITS=$(cd "$OUT" && grep -rIn --exclude-dir=.git -iF "$OPERATOR" . 2>/dev/null || true)
  if [ -n "$NAMEHITS" ]; then
    fail "the operator's account name appears in the tree:"
    echo "$NAMEHITS" | sed 's/^/          /'
  else
    pass "the operator's account name appears nowhere"
  fi
else
  pass "operator-name check skipped (login too short or too common to match on)"
fi

# 4d. No real revenue figures (four digits or more with a thousands separator).
# Scans every text file, not just documentation: the same two account totals
# were also sitting in an appctl.py docstring and a test docstring.
MONEY=$(cd "$OUT" && grep -rIn --exclude-dir=.git '\$[0-9]\{1,3\},[0-9]\{3\}' . 2>/dev/null || true)
if [ -n "$MONEY" ]; then fail "revenue figures present:"; echo "$MONEY" | sed 's/^/          /'
else pass "no revenue figures anywhere in the tree"; fi

# 4d2. Real figures written with NO currency symbol and no separator.
#
#      Check 4d above can only see a figure carrying a currency symbol and a
#      thousands separator. A JSON or Swift fixture writes the very same number
#      as a bare decimal, and that is exactly where the operator's real lifetime
#      spend, sales and profit sat until 2026-08-23: in two Swift test files,
#      invisible to every check, left there by a session documenting a Dashboard
#      bug.
#
#      Nothing in this project legitimately writes a money figure in the
#      thousands. Bids are pennies, list prices are tens, royalties are single
#      dollars, and every fixture is synthetic. So the SHAPE is the whole signal
#      and it needs no context to be certain of: four or more digits, a point,
#      two decimals. Measured across the entire tree the day it was added, the
#      Xcode project file included, it matched the nine real figures and nothing
#      else.
#
#      Keep fixtures below four digits. A test that genuinely needs a large
#      total should scale its whole scenario down rather than take an exemption.
#
#      NOTE, and this cost a build: do not write an example figure into this
#      comment. The checks grep the tree they are part of, so a sample number
#      here fails the release on its own source. The company name above is split
#      into halves for the same reason.
RAW_MONEY=$(cd "$OUT" && grep -rInE --exclude-dir=.git '[0-9]{4,}\.[0-9]{2}' . 2>/dev/null || true)
if [ -n "$RAW_MONEY" ]; then
  fail "money figures in the thousands (no currency symbol needed):"
  echo "$RAW_MONEY" | sed 's/^/          /'
else
  pass "no money figures in the thousands"
fi

# 4d3. A money figure whose currency symbol comes AFTER it, or none at all.
#
#      Check 4d matches a symbol BEFORE the number. Amazon, the app's own
#      German locale and most of Europe put it after, and 4d2 needs a decimal
#      point, so a figure written with a comma and a trailing symbol slips both.
#      That is not hypothetical: the operator's real all-time spend and profit
#      sat in docs/review-2026-08-04.md in exactly that shape, and were LIVE on
#      GitHub from the first publication until the 2026-08-23 audit found them
#      on a second pass — through eleven passing checks, twice.
#
#      A bare comma-thousands number cannot be judged on shape alone. This
#      project writes plenty of them legitimately: rows banked, listings
#      merged, impressions served, designs advertised. Measured across the tree,
#      a blanket rule matched 112 lines, almost all of them counts — an alarm
#      that noisy gets muted, and then the real one is missed too.
#
#      So the line has to talk about MONEY as well. That is the whole
#      distinction the leak turns on: a catalogue count is already published
#      here on purpose, and an account total never should be.
#
#      Even then a money WORD is not enough on its own: "5,217 rows ... of US
#      royalty" is a row count in a sentence about royalties. So a number
#      immediately followed by a counting noun is excluded, which took the
#      match from five lines to one. Add to COUNT_NOUNS when a legitimate count
#      trips it — never widen the money words, and never drop the money-word
#      filter, or this becomes the 112-line version nobody reads.
#
#      `1,234` is allowed anywhere - it is the textbook placeholder, and what
#      export_reader's docstring uses to show the separator it parses - and so
#      is its European spelling `1.234,00`, which the German-locale notes use.
#      It is STRIPPED FROM THE LINE, never used to discard the line: dropping
#      the line let a real figure through whenever it shared one with the
#      example. And the strip is written without `\b`, which BSD sed does not
#      understand and silently ignores (found by review, 2026-08-23).
COUNT_NOUNS='rows?|listings?|designs?|impressions?|clicks?|orders?|units?|asins?|campaigns?|keywords?|targets?|ad.groups?|entities|calls?|days?|tees?|hats?|products?|records?|files?|sellers?|messages?|snapshots?|pairs?|tags?|shared|different|round.trips?|of'
# DO NOT WRITE A MATCHING FIGURE IN THIS COMMENT. This script greps the tree it
# ships in, and adding this very check failed the release on its own source AND
# on the changelog entry describing it — the fifth time this trap has caught a
# release. The example below uses the `1,234` placeholder, which is allowlisted
# a few lines down for exactly this reason.
#
# A number with a currency CODE welded to it is money by construction, so it
# needs no nearby money word — that requirement was letting a bare
# a bare `Total: 1,234 EUR` through, since "total" is not a money word and
# adding it
# would match half the prose in the repository. Split into two patterns: an
# attached currency fails on its own; only the BARE thousands shape still has
# to prove it is talking about money (found by review, 2026-08-23).
# The placeholder is STRIPPED FROM THE LINE, never used to discard the line.
# Dropping the line meant a real figure passed whenever it happened to share one
# with the example: both pipelines found it and then threw the whole line away.
#
# The European spelling is covered too — dots for thousands and a comma for the
# decimals — because it matches neither comma-thousands pattern.
#
# STILL NO WORKED EXAMPLES IN THIS COMMENT. Writing the two shapes out was the
# SEVENTH time this file failed the release on its own source; strengthening a
# money check and then naming the figures it catches is the same mistake as
# naming a keyword. Only the `1,234` placeholder may appear here, and only
# because the strip below removes it.
MONEY_CUR=$(cd "$OUT" && grep -rInE --exclude-dir=.git \
              -e '[0-9],[0-9]{3}[0-9,]*([.][0-9]{2})?[[:space:]]*(US\$|USD|EUR|GBP|€|£)' \
              -e '[0-9]{1,3}([.][0-9]{3})+,[0-9]{2}[[:space:]]*(US\$|USD|EUR|GBP|€|£)' . 2>/dev/null \
            | sed -E 's/(^|[^0-9.,])1[.,]234([.,]00)?([^0-9]|$)/\1\3/g' \
            | grep -E '([0-9],[0-9]{3}|[0-9][.][0-9]{3},[0-9]{2})' || true)
MONEY_BARE=$(cd "$OUT" && grep -rInE --exclude-dir=.git \
               '(^|[^$€£0-9.])[0-9]{1,3},[0-9]{3}([^0-9]|$)' . 2>/dev/null \
             | grep -iE 'royalt|spend|sales|profit|revenue|earn|margin|payout' \
             | grep -viE "[0-9],[0-9]{3}[0-9,]*[- ]($COUNT_NOUNS)\b" \
             | sed -E 's/(^|[^0-9.,])1[.,]234([.,]00)?([^0-9]|$)/\1\3/g' | grep -E '[0-9],[0-9]{3}' \
             || true)
MONEY_SUFFIX=$(printf '%s\n%s' "$MONEY_CUR" "$MONEY_BARE" | grep -v '^$' || true)
if [ -n "$MONEY_SUFFIX" ]; then
  fail "money figures in the thousands beside a money word:"
  echo "$MONEY_SUFFIX" | sed 's/^/          /'
else
  pass "no money figures written with a trailing or absent currency symbol"
fi

# 4d4. A real Amazon identifier of the `amzn1.` family.
#
#      Client ids, secrets, refresh tokens and ADS-ACCOUNT ids all share that
#      prefix. `.env.example` shows their shapes with runs of x, which is what
#      a placeholder looks like; anything else is somebody's real identifier.
#
#      This is here because `engine/inspect_accounts.py` carried the author's
#      own ads-account id as a module constant, published from the first
#      release. It granted nobody access — an account id is not a credential —
#      but it named the account, permanently, and it also told every other
#      reader their own account was unreachable, because the script compared
#      what it found against that one hardcoded value. It now reads
#      AMZN_ADS_ACCOUNT_ID and lists everything when that is unset.
#
#      A placeholder is a run of EIGHT or more identical characters. Four was
#      the first threshold and it is far too loose: a real base64 secret can
#      easily contain four repeats, and this filter then discards the whole
#      match as a placeholder — the gate would publish a live refresh token
#      because it happened to contain `aaaa`. The shipped placeholders are runs
#      of forty-odd x, so eight keeps them and puts a real secret out of reach
#      (roughly one in ten billion). Found by review, 2026-08-23.
#      NOT every credential wears that prefix. A LOGIN WITH REFRESH TOKEN — the
#      one secret here that is enough on its own to write to the live account —
#      begins `Atzr` and a pipe, as `.env.example` has always shown. The check
#      read `amzn1.` alone, so a real refresh token pasted into a tracked .py or
#      .md file passed both this and 4a (found by review, 2026-08-23). The
#      placeholder rule is the same for both: a run of four identical characters
#      is somebody typing xxxx, and a real secret cannot contain one.
AMZN_IDS=$(cd "$OUT" && grep -rIhoE --exclude-dir=.git \
             -e 'amzn1\.[a-z0-9-]+(\.[a-z0-9]+)*\.[A-Za-z0-9_-]{12,}' \
             -e 'Atzr[|][A-Za-z0-9_/+=-]{20,}' . 2>/dev/null \
           | sort -u | grep -vE '(.)\1{7,}' || true)
if [ -n "$AMZN_IDS" ]; then
  fail "real Amazon identifiers or refresh tokens present — use a placeholder or read it from .env:"
  echo "$AMZN_IDS" | sed 's/^/          /'
else
  pass "no real Amazon identifiers or refresh tokens (only x-run placeholders)"
fi

# 4d5. A KEYWORD the account actually bids on.
#
#      The only check here that cannot work on shape, because the thing it looks
#      for is ordinary English. The one this was written for was not an ASIN,
#      not money, not an identifier and not a path — two everyday words, sitting
#      in a SwiftUI preview fixture, while the account bid on them in sixty-odd
#      targeting rows across three markets. Every other check walked past it for
#      the same reason a reader would: it looks made up.
#
#      AND DO NOT NAME IT HERE. This script greps the tree it is part of, so an
#      example written into this comment fails the release on its own source.
#      That is now the fourth check to learn that lesson — see the note under
#      4d2, and the company name split into halves further down.
#
#      It is also the most commercially useful thing an ads repository can give
#      away. An ASIN says which product; a keyword you bid on says how the money
#      is actually made, and a competitor can use it directly.
#
#      **It asks `targeting_perf`, never `search_term_perf`, and the difference
#      is the whole check.** Targeting rows are what the operator CHOSE to bid
#      on. Search-term rows are what SHOPPERS typed, which Amazon matched to an
#      ad — the operator never picked those words and often wants rather less to
#      do with them. Both `engine/demand_feed.py` and the Harvest screen ship
#      deliberate blocklists of trademarked names, and every one of those
#      appears in search terms with zero targeting rows behind it. Reading that
#      table would fail the release on a list of things the operator is
#      carefully NOT selling, which is the shape of an alarm that gets muted.
#
#      Exclusions are STRUCTURAL and are read out of the snapshot's own source,
#      never listed here — this script ships inside the tree it greps, so a
#      phrase named in an allowlist fails the release on its own source. Four
#      releases have now learned that. Three sources are exempt:
#        * `seasonal.example.json`, a calendar of public holidays. Nobody
#          invented Christmas, and its only operator-specific field is an ASIN
#          map that ships empty and is covered by check 4e.
#        * Amazon's product wording, from `products.py` and `preempt.py`'s
#          format-synonym map. "coffee" plus a drinking vessel is what Amazon
#          calls a product, not a niche somebody found.
#        * `demand_feed.py`'s declared trademark blocklist. That list is what
#          the operator refuses to sell, which is the opposite of strategy.
#
#      An earlier version pre-filtered the TREE with a regex of common apparel
#      and stop words. Measured against the live account, that regex could not
#      see 46.6% of the keywords it was meant to protect, and it was also
#      lowercase-only, so a title-cased niche in a docstring was invisible.
#      Both holes were live: a real, currently-bid-on niche shipped in
#      `harvest_suggest.py` through a release that reported clean. Matching is
#      case-insensitive now and the blind spot is 9.6% — 220 single-word
#      keywords (a lone word in a string literal is too common in code to
#      judge), 111 carrying punctuation, and a handful outside the length band.
#
#      It FAILS CLOSED. The scanner's exit status is checked, because the
#      previous version discarded it with `|| true` and an empty result then
#      printed the same green line as a clean tree — a security gate that
#      passed exactly when it had not run.
if [ -n "$DATA_DIR" ] && ls "$DATA_DIR"/ads_data*.sqlite >/dev/null 2>&1; then
  KEYWORD_HITS=$(SNAPSHOT_DIR="$OUT" DATA_DIR="$DATA_DIR" python3 - <<'PYEOF' 2>&1
import ast, glob, os, re, sqlite3, sys

snapshot = os.environ["SNAPSHOT_DIR"]
data_dir = os.environ["DATA_DIR"]

SKIP_FILES = {"seasonal.example.json"}


def _declared(path, names):
    """Every string constant inside the named module-level assignments.

    Read from the SOURCE rather than listed here, for two reasons. It cannot go
    stale when a product type or a blocked brand is added. And this script ships
    inside the tree it greps, so a phrase written into an allowlist would fail
    the release on its own source — a trap that has caught four releases.
    A file that cannot be read or parsed RAISES, because a silently empty
    exclusion set turns into a wall of false positives, and an alarm that noisy
    gets muted."""
    out, seen = set(), set()
    with open(os.path.join(snapshot, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        # AnnAssign too: adding a type annotation (`IP_BLOCK: set[str] = {...}`)
        # changes the node class, and reading only Assign made the exclusion
        # set silently empty — which does not leak, it FLOODS, failing the
        # release on legitimate product vocabulary until somebody widens the
        # check to make the noise stop.
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        hit = [t.id for t in targets if t.id in names]
        if not hit or node.value is None:
            continue
        seen.update(hit)
        for c in ast.walk(node.value):
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                out.add(c.value.strip().lower())
    # A name that is gone has been RENAMED or REMOVED, and carrying on with a
    # partial exclusion set hides that. Say so instead.
    missing = sorted(names - seen)
    if missing:
        raise SystemExit(
            "%s no longer declares %s - this check reads it for its exclusion "
            "list. Update the name here, or restore it there."
            % (path, ", ".join(missing)))
    return out


# Generic product vocabulary and the declared trademark blocklist. Neither is a
# niche: the first is what Amazon calls a product, the second is a list of what
# the operator refuses to sell.
VOCAB = set()
VOCAB |= _declared("engine/preempt.py", {"FORMAT_GROUPS", "TYPE_GROUP"})
VOCAB |= _declared("engine/demand_feed.py", {"IP_BLOCK"})
with open(os.path.join(snapshot, "engine", "products.py"), encoding="utf-8") as fh:
    for m in re.finditer(r'["\x27]([A-Za-z][A-Za-z0-9 \x27-]{2,40})["\x27]\s*[:,]', fh.read()):
        VOCAB.add(m.group(1).strip().lower())

# Every 3-to-6 word window ANYWHERE in a file, plus 2-word windows only where a
# run of words BEGINS. Case-insensitive.
#
# Three narrowings were removed and each had a live leak behind it. Lowercase
# only made a title-cased niche in a docstring invisible. Quoted-text only missed
# every keyword written in a comment or in prose. Non-overlapping runs missed a
# keyword with ordinary words on both sides of it inside one sentence.
#
# Reading all prose sounds unaffordable and is not: the answer is an
# INTERSECTION with the account's own targeting rows, so ordinary English is
# thrown out by never having been bid on. The whole snapshot costs about a third
# of a second.
#
# The 2-word rule is the one deliberate limit, and it is measured. A
# broad-match campaign bids on whatever Amazon decides to match, which on this
# account includes ordinary English word PAIRS - the kind any paragraph of prose
# contains several of. Comparing every 2-word window returns five of them and no
# extra leak; requiring three words except at a run start returns ZERO false
# positives and still catches every real niche found so far. Nothing else
# separates them: frequency in the tree does not (an English pair appeared once,
# a real niche three times) and `match_type` does not (both are mostly BROAD).
#
# (Naming example pairs in this comment failed the release on this very file,
# which was the sixth time it has caught itself. Describe the shape; never write
# the example.)
WORDRUN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[ \t]+[A-Za-z0-9]+)*")


def windows(text):
    """(phrase, certain) for every candidate in one file, lowercased.

    `certain` is False only for a TWO-word window taken from the middle of a
    longer run of words. Those are the ones that collide with ordinary English,
    because broad match bids on whatever Amazon decides to match. They are
    REPORTED rather than failed, so a two-word niche buried mid-sentence is
    still seen by a human and a paragraph of prose does not block the release.
    Everything else - three words or more anywhere, and two words where a run
    begins - fails the build (found by review, 2026-08-23)."""
    for m in WORDRUN.finditer(text):
        ws = m.group(0).split()
        for i in range(len(ws)):
            for n in range(2, 7):
                if i + n > len(ws):
                    break
                yield " ".join(ws[i:i + n]).lower(), (n >= 3 or i == 0)

phrases = {}
for root, dirs, files in os.walk(snapshot):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
    for name in files:
        if name in SKIP_FILES:
            continue
        path = os.path.join(root, name)
        # Read as BYTES and replace what will not decode. Skipping the file
        # was fail-open: one stray byte in a fixture and every keyword in it
        # went uncompared while the gate reported the tree clean. An OSError is
        # different — a file that cannot be read at all is a broken snapshot,
        # so that RAISES and the check fails closed (found by review,
        # 2026-08-23).
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
        for phrase, certain in windows(text):
            if len(phrase) < 6 or phrase in VOCAB:
                continue
            e = phrases.setdefault(phrase, [set(), False])
            e[0].add(os.path.relpath(path, snapshot))
            e[1] = e[1] or certain

if not phrases:
    sys.exit(0)

# One query per database, not one per phrase: about 4,500 distinct targeting
# values across seven markets, read once and intersected in Python. The first
# version asked one COUNT per phrase per market, which took the pre-push hook
# past the two minutes a shell here is given and killed the push mid-verify.
hits = []
seen_db = 0
for db_path in sorted(glob.glob(os.path.join(data_dir, "ads_data*.sqlite"))):
    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "targeting_perf" not in tables:
            continue
        seen_db += 1
        bid_on = {r[0]: r[1] for r in conn.execute(
            "SELECT lower(targeting), COUNT(*) FROM targeting_perf "
            "GROUP BY lower(targeting)")}
    finally:
        conn.close()
    for phrase in set(phrases) & set(bid_on):
        where, certain = phrases[phrase]
        hits.append("%s%r is bid on in %s (%s rows) - in %s"
                    % ("" if certain else "WARN ", phrase,
                       os.path.basename(db_path), bid_on[phrase],
                       ", ".join(sorted(where))))

if not seen_db:
    raise SystemExit("no database carried a targeting_perf table - nothing was compared")

for line in sorted(set(hits)):
    print(line)
PYEOF
)
  KEYWORD_RC=$?
  if [ "$KEYWORD_RC" -ne 0 ]; then
    fail "keyword check could not RUN (exit $KEYWORD_RC) — refusing to call the tree clean:"
    echo "$KEYWORD_HITS" | sed 's/^/          /'
  else
    KEYWORD_SURE=$(printf '%s\n' "$KEYWORD_HITS" | grep -v '^WARN ' | grep -v '^$' || true)
    KEYWORD_MAYBE=$(printf '%s\n' "$KEYWORD_HITS" | grep '^WARN ' || true)
    if [ -n "$KEYWORD_SURE" ]; then
      fail "phrases in the tree that the account actually bids on:"
      echo "$KEYWORD_SURE" | sed 's/^/          /'
    else
      pass "no phrase in the tree is a keyword the account bids on"
    fi
    if [ -n "$KEYWORD_MAYBE" ]; then
      # EXPECTED to list a handful on a clean tree. Broad match bids on
      # whatever Amazon decides to match, which includes ordinary English word
      # pairs, and no cheap signal separates those from a real niche —
      # match_type does not (four of six real niches are BROAD-only), and
      # neither does frequency. So this reports rather than fails, and the
      # count staying small is what makes it readable.
      warn "two-word phrases found mid-sentence that are also bid on — read them,"
      echo "          they are usually ordinary English that broad match picked up:"
      echo "$KEYWORD_MAYBE" | sed 's/^WARN /          /'
    fi
  fi
else
  warn "keyword check SKIPPED — no ads_data*.sqlite here to compare against"
fi


# 4e. Real ASINs.
#
#     Placeholders are fine and are listed here explicitly. Anything ASIN-shaped
#     that is NOT a placeholder is a real design and fails the release, so a
#     future doc example or fixture written with a live ASIN cannot slip out.
#     The allowlist is an EXPLICIT list, not a pattern. A pattern that describes
#     "what a fake ASIN looks like" will one day describe a real one too: an
#     earlier prefix-only skip flagged its own placeholders while letting a real
#     ASIN pass. Every entry below is a deliberate declaration, and adding a
#     fixture ASIN means adding a line here — which is the friction we want.
PLACEHOLDER_ASINS=$(cat <<'ASINS'
B000000001
B00TEST001
B012345678
B0AAA00001
B0AAA00002
B0AAA00003
B0AAAAAAAA
B0DUPE0001
B0EXAMPLE1
B0GBR00001
B0GONE0001
B0HATADSAF
B0HATBARE0
B0HATSAFE0
B0HATZERO0
B0KEPT0001
B0LOT00001
B0LOT00002
B0MMM00003
B0NEW00001
B0NEWADSAF
B0NEWDRINK
B0NEWTEE00
B0OLD00001
B0OLD00002
B0OLDADSAF
B0PART0001
B0POPSOCK1
B0REALASIN
B0REST0002
B0TEEBARE0
B0TEST0000
B0TEST0001
B0TEST0002
B0TESTAAAA
B0TESTBBBB
B0TESTCCCC
B0TUMBLER1
B0US000001
B0USA00001
B0XXXXXXXX
B0YYYYYYYY
B0ZZZ00001
ASINS
)
REAL_ASINS=$(cd "$OUT" && grep -rIoh --exclude-dir=.git 'B0[A-Z0-9]\{8\}' . 2>/dev/null \
             | sort -u | grep -vxF "$PLACEHOLDER_ASINS" || true)
if [ -n "$REAL_ASINS" ]; then
  fail "real ASINs present ($(echo "$REAL_ASINS" | wc -l | tr -d ' ') distinct):"
  echo "$REAL_ASINS" | sed 's/^/          /'
  echo "          in:"
  (cd "$OUT" && grep -rIl --exclude-dir=.git -E "$(echo "$REAL_ASINS" | paste -sd'|' -)" . 2>/dev/null) | sed 's/^/          /'
else
  pass "no real ASINs (only documented placeholders)"
fi

# 4e2. Amazon entity ids (profile, campaign, ad group, keyword).
#
#      These are long digit runs. They are not credentials, but they name real
#      entities in a real account, so documentation examples and test fixtures
#      use the synthetic 9000000000xxxxx range instead. Anything else with 11 or
#      more digits is treated as real. Xcode's project file has its own long
#      identifiers and is excluded.
#      Amazon's OWN AWS account ids are public constants and must stay verbatim.
#      Marketing Stream publishes each dataset from a DIFFERENT Amazon account,
#      and a queue policy naming the wrong one silently drops every message while
#      the subscription still reads ACTIVE — so these are copied from Amazon's
#      published CloudFormation template and may never be edited to please a
#      check. They are listed one per line so each is an explicit decision.
#
#      The operator's own AWS account id is NOT here and must never be: it lives
#      in .env with the queue URLs, which is gitignored. Verified 2026-08-23 —
#      the only queue URLs in the tree are the synthetic ones below.
PUBLIC_IDS=$(cat <<'IDLIST'
906013806264
802324068763
668473351658
562877083794
074266271188
622939981599
926844853897
123456789012
210987654321
IDLIST
)
IDS=$(cd "$OUT" && grep -rIoh --exclude-dir=.git --exclude='*.pbxproj' '[0-9]\{11,\}' . 2>/dev/null \
      | sort -u | grep -vE '^(9000000000[0-9]{5}|0+1?)$' | grep -vxF "$PUBLIC_IDS" || true)
if [ -n "$IDS" ]; then
  fail "real-looking Amazon entity ids present:"
  echo "$IDS" | sed 's/^/          /'
else
  pass "no real Amazon entity ids (only the synthetic 9000000000xxxxx range)"
fi

# 4e3. The operator's own identity.
#
#      Docs written as notes to one person named a company, a domain, a Merch
#      tier and a revenue target. The public tree says "the operator" instead.
#
#      The company name and the operator's name are each spelled in two halves
#      on purpose. Written whole, the company name matched this check's own
#      source and failed every release. The operator's name only escaped that
#      by accident — the `\b` in front of it happened to sit against a word
#      character, so the boundary never matched. Do not rely on that.
#
#      LICENSE is excluded deliberately: its copyright line names the legal owner,
#      which is what makes the no-hosting term enforceable, and is the one place
#      the company name belongs.
COMPANY='prov'"enio"
OPERATOR='Mar'"ko"
IDENTITY=$(cd "$OUT" && grep -rIn -i --exclude-dir=.git --exclude=LICENSE -E "$COMPANY|d\.o\.o|\b$OPERATOR\b" . 2>/dev/null || true)
if [ -n "$IDENTITY" ]; then
  fail "the operator's personal or company identity is present:"
  echo "$IDENTITY" | sed 's/^/          /'
else
  pass "no personal or company identity"
fi

# 4e4. The retired strategy's name.
#
#      It named a manual method the operator no longer runs, and it should not
#      appear anywhere a reader could mistake it for a live feature. $RETIRED is
#      built from halves at the top of this script for exactly this check.
RETIRED_HITS=$(cd "$OUT" && grep -rIn -i --exclude-dir=.git "$RETIRED" . 2>/dev/null || true)
if [ -n "$RETIRED_HITS" ]; then
  fail "the retired strategy's name is present:"
  echo "$RETIRED_HITS" | sed 's/^/          /'
else
  pass "no reference to the retired strategy"
fi

# 4f. The documents a new user needs must exist.
# engine/ is listed because the product IS the engine. Without these the only
# code that reads them is skipped as a warning, and a tree holding the documents
# and an empty passing test package could be certified with no product in it
# (found by review, 2026-08-23).
for f in README.md LICENSE CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md CHANGELOG.md \
         .env.example requirements.txt \
         engine/appctl.py engine/products.py engine/preempt.py engine/demand_feed.py \
         run_scheduled.sh \
         docs/SETUP.md docs/SAFETY.md docs/COMMANDS.md docs/ARCHITECTURE.md \
         docs/TROUBLESHOOTING.md docs/README.md docs/api-access-setup.md \
         docs/WINDOWS.md docs/BUILD-A-UI.md; do
  [ -f "$OUT/$f" ] || fail "missing required file: $f"
done
pass "required documentation present"

# 4g. The test suite must still pass in the snapshot.
#
# The suite writes a scratch database, an outputs/ folder and bytecode caches,
# so the exact set of generated-looking paths is recorded FIRST. The cleanup
# below removes only what appeared between these two listings.
_gen_before=$(cd "$OUT" && find . \( -name '*.sqlite' -o -name '*.sqlite-*' \
                                    -o -name '__pycache__' -o -path './outputs' \) \
                            -not -path './.git/*' 2>/dev/null | sort)
echo "    ..    running the test suite in the snapshot"
if (cd "$OUT" && python3 -m unittest discover -s tests -p '*_tests.py' -t . >/dev/null 2>&1); then
  pass "test suite passes in the snapshot"
else
  fail "test suite does NOT pass in the snapshot"
fi

# Remove ONLY what this run created, never what it found.
#
# Section 2 states the rule for the other destructive step: "Verification must
# not touch the tree it is judging." It was written there and broken here, 600
# lines down, where the deletion is far worse — every *.sqlite, every WAL
# sidecar and the whole outputs/ folder, unconditionally, in verify-only mode
# too. Pointed at the operator's own checkout that is seven market databases,
# the Stream database, the catalogue cache and 98 output files, erased while the
# script prints "ok". Found by review, 2026-08-23.
#
# Not cleaning at all is not the fix either: the suite above dirties the tree,
# so the NEXT verify would fail on the leftovers of the last one. Diffing
# against the listing taken before the suite ran leaves the tree exactly as
# found, which is what "read-only" has to mean.
_gen_after=$(cd "$OUT" && find . \( -name '*.sqlite' -o -name '*.sqlite-*' \
                                   -o -name '__pycache__' -o -path './outputs' \) \
                           -not -path './.git/*' 2>/dev/null | sort)
_created=$(comm -13 <(printf '%s\n' "$_gen_before") <(printf '%s\n' "$_gen_after"))
if [ -n "$_created" ]; then
  printf '%s\n' "$_created" | while IFS= read -r rel; do
    [ -n "$rel" ] && rm -rf "${OUT:?}/${rel#./}"
  done
fi
_gen_left=$(cd "$OUT" && find . \( -name '*.sqlite' -o -path './outputs' \) \
                          -not -path './.git/*' 2>/dev/null | sort)
if [ "$_gen_left" != "$_gen_before" ]; then
  fail "generated files survived the cleanup"
else
  pass "the tree is exactly as it was found"
fi


  echo
  return $FAILED
}

if [ "$VERIFY_ONLY" = "1" ]; then
  verify_tree "$OUT" || {
    echo "==> VERIFICATION FAILED — do not publish this tree."
    exit 1
  }
  echo "==> Verification passed."
  exit 0
fi

verify_tree "$OUT" || {
  echo "==> VERIFICATION FAILED. Nothing was committed. Fix the items above and re-run."
  exit 1
}

# ---------------------------------------------------------------------------
# 5. Fresh git repository, single commit.
# ---------------------------------------------------------------------------
echo "==> Creating a fresh git repository"
cd "$OUT"
git init --quiet -b main

# ---------------------------------------------------------------------------
# The gate. Building this tree verified it; pushing it must verify it again.
#
# This folder is a plain git repository, so `git push` works whether or not the
# checks ever ran — and a tree edited by hand after the build would go straight
# out. Twice already a check caught something a careful read had missed: a launch
# list of real ASINs, and account revenue figures in a docstring. Both times
# because the check greps the built tree rather than trusting the diff.
#
# The hook is written per snapshot because the repository is recreated each run.
# ---------------------------------------------------------------------------
mkdir -p "$OUT/.git/hooks"
cat > "$OUT/.git/hooks/pre-push" <<HOOK
#!/bin/bash
# Re-run the release checks against the tree being pushed. Installed by
# $REPO/scripts/make_public_snapshot.sh — edit it there, not here.
echo "pre-push: re-verifying this tree before it goes public…"
if ! bash "$REPO/scripts/make_public_snapshot.sh" --verify-only --out "$OUT"; then
  echo
  echo "PUSH BLOCKED — this tree failed the release checks (see above)."
  echo "Rebuild it:  bash $REPO/scripts/make_public_snapshot.sh --owner <you> --out $OUT"
  echo "Override only if you are certain:  git push --no-verify"
  exit 1
fi
HOOK
chmod +x "$OUT/.git/hooks/pre-push"
echo "    pre-push gate installed"

# The repository is recreated on every build, so its remote goes with it. It was
# re-added by hand three times before this line existed, and a push that fails
# for want of a remote is a small thing that happens at the worst moment.
if [ -n "$OWNER" ]; then
  git remote add origin "https://github.com/$OWNER/$NAME.git" 2>/dev/null || true
  echo "    remote:        origin -> $OWNER/$NAME"
fi

git add -A
AUTHOR_ARGS=()
if [ -n "$AUTHOR_EMAIL" ]; then
  AUTHOR_ARGS=(-c "user.name=${AUTHOR_NAME:-$OWNER}" -c "user.email=$AUTHOR_EMAIL")
  echo "    commit author: ${AUTHOR_NAME:-$OWNER} <$AUTHOR_EMAIL>"
fi
# The version is READ, never typed. It was hardcoded at 0.2.0 and stayed there
# through fourteen releases, so the snapshot commit announced a version the code
# had not been for a week.
VERSION=$(sed -n 's/.*MARKETING_VERSION = \([0-9.]*\);.*/\1/p' \
          "$OUT/MerchAds.xcodeproj/project.pbxproj" | head -1)
[ -n "$VERSION" ] || { echo "could not read MARKETING_VERSION" >&2; exit 1; }
echo "    version:       $VERSION"

git "${AUTHOR_ARGS[@]}" -c commit.gpgsign=false commit --quiet -F - <<MSG
Merch Ads $VERSION

Amazon Sponsored Products automation for print-on-demand sellers, with a
native macOS app on top.

This is a clean snapshot. The private development repository keeps the full
history and the operator's own data; none of that is included here.

See CHANGELOG.md for what this release adds, and docs/SAFETY.md before running
anything that writes to a live account.
MSG

echo
echo "==> Done."
echo "    Snapshot:  $OUT"
echo "    Files:     $(git ls-files | wc -l | tr -d ' ')"
echo "    Size:      $(du -sh "$OUT" | cut -f1)"
echo
echo "    Review it, then publish:"
echo
echo "      cd \"$OUT\" && git push --force origin main"
echo "      bash $REPO/scripts/publish_release.sh --out \"$OUT\""
echo
echo "    The push re-runs every check against the tree being pushed. The second"
echo "    line publishes ONE release and deletes any older one — see that script's"
echo "    header for why only one."
echo
echo "    First time only, if the repository does not exist yet:"
echo
echo "      gh repo create $NAME --public --source=. --push \\"
echo "        --description \"Amazon Ads automation for print-on-demand sellers, with a native macOS app.\""
echo
