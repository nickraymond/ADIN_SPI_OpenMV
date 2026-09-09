#!/usr/bin/env bash
# Put a field rig onto a tracked checkout of a branch, without losing anything
# that exists only on the rig. Runs ON the rig:
#
#   ssh pi@nereus002 'bash ~/ADIN_SPI_OpenMV/pi/field/deploy_field.sh --branch sprint/33-field-ready'
#
# WHY THIS EXISTS (S33 bite 1). nereus002 was found on branch
# sprint/29-fieldunit with fifteen S32 files hand-copied in as UNTRACKED --
# so the rig Nick is taking to sea was not running the repo, and a plain
# `git checkout` would have done two bad things at once:
#
#   * refused outright, because git will not silently overwrite untracked
#     files that the target branch tracks; and
#   * had it been forced, DESTROYED camera_ceilings.json, which held the only
#     copy of nereus002's per-combination delivered rates and the only IMX708
#     measurements in existence. Twelve of those fifteen files were
#     byte-identical to the repo. One was irreplaceable. Nothing on the rig
#     said which was which.
#
# So this script's real job is not "checkout" -- it is to answer, per file,
# "is this the same as what I am about to replace it with?", back everything
# up regardless, and REFUSE to proceed while any answer is no.
#
# CLAUDE.md rule 4 governs the verification at the end: a deploy that exits 0
# proves nothing. HEAD, a clean tree and the recipe count are read back from
# the rig and the workbench API after the restart.
set -euo pipefail

BRANCH="sprint/33-field-ready"
REPO="$HOME/ADIN_SPI_OpenMV"
WORKBENCH_URL="http://127.0.0.1:8088"
ASSUME_YES=0
UNIT="workbench"

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    --repo)   REPO="$2"; shift 2 ;;
    --url)    WORKBENCH_URL="$2"; shift 2 ;;
    --unit)   UNIT="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "deploy_field: unknown argument $1" >&2; exit 2 ;;
  esac
done

cd "$REPO"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/field_backups/$STAMP"
say() { printf '%s\n' "$*"; }
die() { printf 'deploy_field: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
# ONE OWNER PER PORT, EVER. A running demo holds a board; checking out code
# underneath it swaps the files a live process is reading and, worse, invites
# an operator to restart the workbench while a recording is mid-write. Stop
# demos from the PAGE, never by killing the process.
say "== preflight =="
RUNNER="$(curl -fsS -m 10 "$WORKBENCH_URL/api/runner" 2>/dev/null || true)"
if [ -z "$RUNNER" ]; then
  say "   workbench not answering at $WORKBENCH_URL (fine if it is not up yet)"
else
  STATE="$(printf '%s' "$RUNNER" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state","?"))')"
  say "   workbench runner state: $STATE"
  case "$STATE" in
    idle|failed) ;;
    *) die "runner is '$STATE' -- stop the demo from $WORKBENCH_URL first, then re-run. Never kill it." ;;
  esac
fi

git rev-parse --git-dir >/dev/null 2>&1 || die "$REPO is not a git repository"
say "   HEAD before: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"

say "== fetching origin =="
git fetch --prune origin || die "git fetch failed"
TARGET="origin/$BRANCH"
git rev-parse --verify --quiet "$TARGET" >/dev/null || die "no such branch: $TARGET"
say "   target: $TARGET = $(git rev-parse --short "$TARGET")"

# ------------------------------------------------- what would this destroy?
# Two populations, and they fail differently:
#   * tracked-but-modified -- checkout refuses, or -f discards the change
#   * untracked -- checkout refuses if the target tracks that path
# Both are compared BY CONTENT against the target tree, because "differs from
# HEAD" is not the question. The question is "differs from what replaces it".
say "== comparing local files against $TARGET =="
mkdir -p "$BACKUP"
SAME=0; DIFF=0; DIFF_LIST=""

check_one() {
  local f="$1" kind="$2" local_sha tgt_sha
  [ -f "$f" ] || return 0
  mkdir -p "$BACKUP/$(dirname "$f")"
  cp -p "$f" "$BACKUP/$f"
  local_sha="$(sha256sum "$f" | cut -d' ' -f1)"
  # THE BLOB IS NEVER ROUTED THROUGH A SHELL VARIABLE. Written as
  # `tgt="$(git cat-file blob ...)"`, command substitution strips every
  # trailing newline and discards null bytes, so the hash was of something
  # that was never on disk and NOTHING could ever compare equal: the first
  # run of this script reported 15 files differing where 12 were provably
  # byte-identical. CLAUDE.md trap 3 -- a comparison lying about a correct
  # result -- with the comparison here being the thing that lied. Existence
  # is checked separately so the pipeline's status means only what it says.
  if git cat-file -e "$TARGET:$f" 2>/dev/null; then
    tgt_sha="$(git cat-file blob "$TARGET:$f" | sha256sum | cut -d' ' -f1)"
    if [ "$local_sha" = "$tgt_sha" ]; then
      SAME=$((SAME + 1)); return 0
    fi
    DIFF=$((DIFF + 1)); DIFF_LIST="$DIFF_LIST  $kind DIFFERS  $f"$'\n'
  else
    # Untracked here and absent there: checkout leaves it alone. Backed up
    # anyway, then reported, because "the target does not know about it" is
    # exactly how a rig-only measurement goes unnoticed.
    SAME=$((SAME + 1))
  fi
}

while IFS= read -r f; do [ -n "$f" ] && check_one "$f" "modified "; done \
  < <(git diff --name-only HEAD 2>/dev/null || true)
while IFS= read -r f; do [ -n "$f" ] && check_one "$f" "untracked"; done \
  < <(git ls-files --others --exclude-standard 2>/dev/null || true)

say "   backed up to: $BACKUP"
say "   identical to target or absent there: $SAME"
say "   DIFFERENT from target: $DIFF"
if [ "$DIFF" -gt 0 ]; then
  printf '%s' "$DIFF_LIST"
  say ""
  say "   Each file above would be REPLACED by a different version."
  say "   Copies are in $BACKUP -- diff them before you decide."
  if [ "$ASSUME_YES" -ne 1 ]; then
    die "refusing to clobber $DIFF file(s). Re-run with --yes once you have looked."
  fi
  say "   --yes given: proceeding anyway."
fi

# ------------------------------------------------------------------ checkout
say "== checking out $BRANCH =="
# Remove ONLY the untracked files that the target actually tracks -- those are
# the ones git would refuse to overwrite. An untracked file the target knows
# nothing about is left exactly where it is.
#
# The first version of this line was `git ls-files --others | xargs rm -f`,
# which deletes everything untracked. That is the same mistake this whole
# script exists to prevent, one step further down: a rig-only artefact is
# invisible to the target tree BY DEFINITION, so "the target does not track
# it" is evidence to keep it, never a licence to delete it. Backed up is not
# the same as safe.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if git cat-file -e "$TARGET:$f" 2>/dev/null; then rm -f "$f"; fi
done < <(git ls-files --others --exclude-standard 2>/dev/null || true)

# Tracked-but-modified files block the switch too ("local changes would be
# overwritten"). They are in the backup and were listed above, so discard them
# explicitly rather than reaching for `checkout -f`: this way the ONLY things
# thrown away are the ones the report named.
git checkout -- . 2>/dev/null || true

git checkout -B "$BRANCH" "$TARGET" || die "checkout failed"
git reset --hard "$TARGET" >/dev/null || die "reset failed"

# ------------------------------------------------------------------- restart
say "== restarting $UNIT =="
# enable --now STARTS a stopped unit but does NOT restart a running one, so a
# deploy after a pull can leave the OLD code serving while printing success.
# Measured on nereus000 2026-09-08: a two-day-old process kept answering, and
# reported 11 recipes when 15 were on disk. Enable, then restart, always.
sudo systemctl enable "$UNIT" >/dev/null 2>&1 || say "   (enable skipped)"
sudo systemctl restart "$UNIT" || die "could not restart $UNIT"

# ---------------------------------------------------- verify the ARTIFACTS
say "== verifying =="
NOW="$(git rev-parse HEAD)"; WANT="$(git rev-parse "$TARGET")"
[ "$NOW" = "$WANT" ] || die "HEAD is $NOW, expected $WANT"
say "   HEAD after:  $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"

# TRACKED files only. Untracked leftovers are not a failure -- they are the
# rig-only artefacts the checkout step deliberately preserves -- but they are
# reported, because an unnoticed one is how a measurement goes stale.
DIRT="$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
[ "$DIRT" = "0" ] || { git status --short; die "tracked files are modified ($DIRT) -- the deploy did not take"; }
say "   tracked files: clean"
LEFT="$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')"
if [ "$LEFT" != "0" ]; then
  say "   $LEFT untracked file(s) kept (not in $TARGET):"
  git ls-files --others --exclude-standard | sed 's/^/     /'
fi

ON_DISK="$(find pi/workbench/recipes -maxdepth 1 -name '*.toml' | wc -l | tr -d ' ')"
SERVED=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  SERVED="$(curl -fsS -m 5 "$WORKBENCH_URL/api/recipes" 2>/dev/null \
    | python3 -c 'import json,sys
d=json.load(sys.stdin); r=d.get("recipes",d) if isinstance(d,dict) else d
print(len(r))' 2>/dev/null || true)"
  [ -n "$SERVED" ] && break
  sleep 2
done
[ -n "$SERVED" ] || die "workbench never answered /api/recipes after the restart"
say "   recipes on disk: $ON_DISK   served by the workbench: $SERVED"
[ "$ON_DISK" = "$SERVED" ] || die "the workbench is serving $SERVED recipes but $ON_DISK are on disk -- it is running stale code"

CEIL="pi/field/camera_ceilings.$(uname -n).json"
if [ -f "$CEIL" ]; then
  say "   ceilings for this rig: $CEIL"
else
  say "   NOTE: no $CEIL -- the recorder will make no fps claims on this rig."
fi

say ""
say "deploy_field: OK. $BRANCH is live, backup kept at $BACKUP"
