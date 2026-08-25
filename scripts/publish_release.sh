#!/bin/bash
# Publish ONE GitHub release for the snapshot that is currently on main, and
# delete every older one.
#
#   bash scripts/publish_release.sh                       # uses ~/merch-ads-public
#   bash scripts/publish_release.sh --out /tmp/pub
#   bash scripts/publish_release.sh --dry-run             # show, change nothing
#
# Run it AFTER pushing the snapshot.
#
# WHY ONLY ONE. A release needs a tag, and a tag freezes that tree forever. The
# public repository is republished by force-pushing a single fresh commit, so an
# old tag is not "a previous version" in any git sense — it is an orphaned tree
# that no later fix can reach.
#
# That is not a hypothetical. The release checks are a ratchet: each new leak
# class found becomes the next check. The retired strategy's name was removed
# from the working tree long before 2026-08-23 and was still sitting in two
# published tags on that date, because v0.2.0 and v0.2.1 predated the check that
# catches it. Deleting them was the fix.
#
# Keeping exactly one release makes that impossible to forget. The full history
# is not lost: CHANGELOG.md carries every version in far more detail than a
# release note.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$HOME/merch-ads-public"
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)         echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "gh is not installed" >&2; exit 1; }
[ -d "$OUT/.git" ] || { echo "Not a snapshot folder: $OUT" >&2; exit 1; }

SLUG=$(git -C "$OUT" remote get-url origin 2>/dev/null \
       | sed -e 's#.*github.com[:/]##' -e 's#\.git$##') || true
[ -n "${SLUG:-}" ] || { echo "No origin remote in $OUT — push it first." >&2; exit 1; }

# The version comes from the SNAPSHOT, never from this checkout: the thing being
# released is what was published, which may not be what is on disk here.
VERSION=$(sed -n 's/.*MARKETING_VERSION = \([0-9.]*\);.*/\1/p' \
          "$OUT/MerchAds.xcodeproj/project.pbxproj" | head -1)
[ -n "$VERSION" ] || { echo "Could not read MARKETING_VERSION from the snapshot" >&2; exit 1; }
TAG="v$VERSION"

# The release notes ARE that version's changelog section. One source, so a
# release note can never say something the changelog does not.
NOTES=$(mktemp)
trap 'rm -f "$NOTES"' EXIT
# String matching, not a regex. awk processes escapes in a -v value, so a
# bracketed heading passed that way becomes a character class and matches
# nothing — which reads exactly like "this version has no changelog entry".
awk -v v="## [$VERSION]" '
  index($0, v) == 1 {found = 1; print; next}
  found && index($0, "## [") == 1 {exit}
  found {print}
' "$OUT/CHANGELOG.md" > "$NOTES"
[ -s "$NOTES" ] || { echo "CHANGELOG.md has no section for $VERSION" >&2; exit 1; }

# "## [0.4.10] - 2026-08-23 - the headline"  ->  "v0.4.10 - the headline".
# GitHub shows the date itself, and the brackets are changelog syntax.
TITLE=$(head -1 "$NOTES" \
        | sed -e 's/^## \[\([^]]*\)\][^—]*—[^—]*— */v\1 — /' \
              -e 's/^## \[\([^]]*\)\].*/v\1/')
# Drop the heading itself; GitHub shows the title separately.
sed -i '' '1d' "$NOTES" 2>/dev/null || sed -i '1d' "$NOTES"

echo "==> Repository : $SLUG"
echo "    Release    : $TAG"
echo "    Title      : $TITLE"
echo "    Notes      : $(grep -c . "$NOTES") lines from CHANGELOG.md"

EXISTING=$(gh release list --repo "$SLUG" --json tagName --jq '.[].tagName' 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
  echo "    Removing   : $(echo "$EXISTING" | tr '\n' ' ')"
else
  echo "    Removing   : nothing (no releases yet)"
fi

if [ "$DRY" = "1" ]; then
  echo
  echo "==> Dry run. Nothing was changed."
  echo "    The notes that WOULD be published:"
  sed 's/^/      /' "$NOTES"
  exit 0
fi

# Delete first, so the repository never shows two versions as current.
for t in $EXISTING; do
  gh release delete "$t" --repo "$SLUG" --yes --cleanup-tag
  echo "    deleted    $t"
done

gh release create "$TAG" --repo "$SLUG" --title "$TITLE" --notes-file "$NOTES" >/dev/null
echo
echo "==> Released $TAG"
echo "    https://github.com/$SLUG/releases/tag/$TAG"
