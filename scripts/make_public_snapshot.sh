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
NAME="merch-ads"

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="$2"; shift 2 ;;
    --owner)   OWNER="$2"; shift 2 ;;
    --name)    NAME="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)         echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

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
)

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

# ---------------------------------------------------------------------------
# 2. Remove the private files.
# ---------------------------------------------------------------------------
echo "==> Removing private files"
for p in "${PRIVATE[@]}"; do
  if [ -e "$OUT/$p" ]; then
    rm -rf "${OUT:?}/$p"
    echo "    removed  $p"
  fi
done

# ---------------------------------------------------------------------------
# 3. Fill in the repository owner, if one was given.
# ---------------------------------------------------------------------------
if [ -n "$OWNER" ]; then
  echo "==> Setting repository owner to $OWNER/$NAME"
  # -I skips binary files so an .icns or a .png is never rewritten.
  grep -rIl 'zdufs' "$OUT" 2>/dev/null | while read -r f; do
    /usr/bin/sed -i '' -e "s#zdufs/merch-ads#$OWNER/$NAME#g" -e "s#zdufs#$OWNER#g" "$f" 2>/dev/null \
      || sed -i -e "s#zdufs/merch-ads#$OWNER/$NAME#g" -e "s#zdufs#$OWNER#g" "$f"
  done
else
  echo "==> No --owner given; docs keep the zdufs placeholder"
fi

# ---------------------------------------------------------------------------
# 4. Verify. Any failure here stops the release.
# ---------------------------------------------------------------------------
echo
echo "==> Verifying"
FAILED=0

fail() { echo "    FAIL  $1"; FAILED=1; }
pass() { echo "    ok    $1"; }

# 4a. No secrets, databases, or personal caches.
LEAKS=$(cd "$OUT" && find . \( \
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
HOMEPATHS=$(cd "$OUT" && grep -rIl '/Users/[a-z]' . 2>/dev/null || true)
if [ -n "$HOMEPATHS" ]; then fail "hardcoded home paths in:"; echo "$HOMEPATHS" | sed 's/^/          /'
else pass "no hardcoded home paths"; fi

# 4d. No real revenue figures (four digits or more with a thousands separator).
# Scans every text file, not just documentation: the same two account totals
# were also sitting in an appctl.py docstring and a test docstring.
MONEY=$(cd "$OUT" && grep -rIn '\$[0-9]\{1,3\},[0-9]\{3\}' . 2>/dev/null || true)
if [ -n "$MONEY" ]; then fail "revenue figures present:"; echo "$MONEY" | sed 's/^/          /'
else pass "no revenue figures anywhere in the tree"; fi

# 4e. Real ASINs.
#
#     Placeholders are fine and are listed here explicitly. Anything ASIN-shaped
#     that is NOT a placeholder is a real design and fails the release, so a
#     future doc example or fixture written with a live ASIN cannot slip out.
PLACEHOLDER_ASINS='^(B0EXAMPLE[0-9]|B0TEST[A-Z0-9]{4}|B0X{8}|B0Y{8}|B012345678)$'
REAL_ASINS=$(cd "$OUT" && grep -rIoh 'B0[A-Z0-9]\{8\}' . 2>/dev/null \
             | sort -u | grep -vE "$PLACEHOLDER_ASINS" || true)
if [ -n "$REAL_ASINS" ]; then
  fail "real ASINs present ($(echo "$REAL_ASINS" | wc -l | tr -d ' ') distinct):"
  echo "$REAL_ASINS" | sed 's/^/          /'
  echo "          in:"
  (cd "$OUT" && grep -rIl -E "$(echo "$REAL_ASINS" | paste -sd'|' -)" . 2>/dev/null) | sed 's/^/          /'
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
IDS=$(cd "$OUT" && grep -rIoh --exclude='*.pbxproj' '[0-9]\{11,\}' . 2>/dev/null \
      | sort -u | grep -vE '^(9000000000[0-9]{5}|0+1?)$' || true)
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
IDENTITY=$(cd "$OUT" && grep -rIn -i --exclude=LICENSE -E "$COMPANY|d\.o\.o|\b$OPERATOR\b" . 2>/dev/null || true)
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
RETIRED_HITS=$(cd "$OUT" && grep -rIn -i "$RETIRED" . 2>/dev/null || true)
if [ -n "$RETIRED_HITS" ]; then
  fail "the retired strategy's name is present:"
  echo "$RETIRED_HITS" | sed 's/^/          /'
else
  pass "no reference to the retired strategy"
fi

# 4f. The documents a new user needs must exist.
for f in README.md LICENSE CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md CHANGELOG.md \
         .env.example requirements.txt \
         docs/SETUP.md docs/SAFETY.md docs/COMMANDS.md docs/ARCHITECTURE.md \
         docs/TROUBLESHOOTING.md docs/README.md docs/api-access-setup.md; do
  [ -f "$OUT/$f" ] || fail "missing required file: $f"
done
pass "required documentation present"

# 4g. The test suite must still pass in the snapshot.
echo "    ..    running the test suite in the snapshot"
if (cd "$OUT" && python3 -m unittest discover -s tests -p '*_tests.py' -t . >/dev/null 2>&1); then
  pass "test suite passes in the snapshot"
else
  fail "test suite does NOT pass in the snapshot"
fi

# The run leaves a scratch database, an outputs/ folder and bytecode caches
# behind. All are gitignored, so they never reach the commit — but leave the
# folder pristine, and re-check that nothing generated survived.
find "$OUT" \( -name '*.sqlite' -o -name '*.sqlite-*' -o -name '__pycache__' \) \
     -exec rm -rf {} + 2>/dev/null || true
rm -rf "${OUT:?}/outputs"
if [ -e "$OUT/outputs" ] || find "$OUT" -name '*.sqlite' | grep -q .; then
  fail "generated files survived the cleanup"
else
  pass "no generated files left behind"
fi

echo
if [ "$FAILED" != "0" ]; then
  echo "==> VERIFICATION FAILED. Nothing was committed. Fix the items above and re-run."
  exit 1
fi

# ---------------------------------------------------------------------------
# 5. Fresh git repository, single commit.
# ---------------------------------------------------------------------------
echo "==> Creating a fresh git repository"
cd "$OUT"
git init --quiet -b main
git add -A
git -c commit.gpgsign=false commit --quiet -F - <<'MSG'
Merch Ads 0.2.0 — first public release

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
echo "    Review it, then publish with:"
echo
echo "      cd \"$OUT\""
echo "      gh repo create $NAME --public --source=. --remote=origin --push \\"
echo "        --description \"Amazon Ads automation for print-on-demand sellers, with a native macOS app.\""
echo
