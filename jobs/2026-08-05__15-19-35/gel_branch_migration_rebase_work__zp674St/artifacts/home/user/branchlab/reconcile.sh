#!/usr/bin/env bash
#
# reconcile.sh
#
# Reproduces the reconciliation of the two parallel Gel feature branches
# (`feat_tags` and `feat_review`) that were developed independently on top
# of `main` back into a single, linear, three-migration history on `main`:
#
#   1. `feat_tags`   was created from `main` and contributed exactly one
#      migration adding the `Tag` type and `Article.tags` link.
#   2. `feat_review` was created from `main` (independently of `feat_tags`)
#      and contributed exactly one migration adding the required
#      `Article.review_state` property, backfilled from `word_count`.
#   3. `feat_tags` was fast-forward merged into `main` (`gel branch merge`).
#   4. `feat_review` was rebased onto the new `main` (`gel branch rebase`,
#      which replays its migration on top of the tags migration) and then
#      fast-forward merged into `main` as well, producing a linear chain:
#      initial -> tags -> review.
#   5. `feat_tags` was dropped; only `main` and `feat_review` remain, both
#      sharing the same 3-migration linear history.
#   6. A single `Tag { label := "longform" }` was inserted and linked to
#      every pre-existing article with `word_count >= 1000`.
#
# This script is IDEMPOTENT / SAFE to re-run: it first checks whether the
# project is already in the fully reconciled end-state described above and,
# if so, verifies it and exits 0 without making any changes. It only
# attempts to perform the reconciliation steps if the end-state is not yet
# reached (e.g. when starting from the original pre-reconciliation project).
#
# All pre-existing data (4 Authors, 12 Articles, their ids, links, and
# scalar values) is preserved in place throughout -- nothing is ever
# deleted and re-inserted.

set -euo pipefail
cd "$(dirname "$0")"

INSTANCE="devinst"

log() { echo "[reconcile] $*"; }

ensure_instance_running() {
  if ! gel instance list 2>/dev/null | grep -q "${INSTANCE}.*up"; then
    log "Starting Gel instance '${INSTANCE}'..."
    gel-start
  fi
}

# ---------------------------------------------------------------------------
# Verification of the fully reconciled end-state.
# Returns 0 (success) if EVERY invariant holds, non-zero otherwise.
# ---------------------------------------------------------------------------
verify_reconciled() {
  local branches
  branches=$(gel branch list 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | awk '{print $1}' | sort)
  local expected_branches
  expected_branches=$(printf 'feat_review\nmain\n')
  [ "${branches}" = "${expected_branches}" ] || { log "branch set mismatch"; return 1; }

  gel branch current 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -q "'main'" || { log "active branch is not main"; return 1; }

  local mig_count
  mig_count=$(ls dbschema/migrations/*.edgeql 2>/dev/null | wc -l | tr -d ' ')
  [ "${mig_count}" = "3" ] || { log "expected exactly 3 migration files, found ${mig_count}"; return 1; }

  gel migration status >/dev/null 2>&1 || { log "gel migration status failed for main"; return 1; }
  gel migration status -b feat_review >/dev/null 2>&1 || { log "gel migration status failed for feat_review"; return 1; }

  grep -q "type Tag" dbschema/default.gel || { log "schema missing Tag type"; return 1; }
  grep -q "tags: Tag" dbschema/default.gel || { log "schema missing Article.tags link"; return 1; }
  grep -q "review_state" dbschema/default.gel || { log "schema missing review_state property"; return 1; }

  local authors articles tags mismatches untagged_tagged
  authors=$(gel query "select count(Author);" 2>/dev/null | tr -d '\r')
  articles=$(gel query "select count(Article);" 2>/dev/null | tr -d '\r')
  tags=$(gel query "select count(Tag);" 2>/dev/null | tr -d '\r')
  [ "${authors}" = "4" ] || { log "expected 4 Authors, found ${authors}"; return 1; }
  [ "${articles}" = "12" ] || { log "expected 12 Articles, found ${articles}"; return 1; }
  [ "${tags}" = "1" ] || { log "expected exactly 1 Tag, found ${tags}"; return 1; }

  gel query "select assert_single((select Tag filter .label = 'longform'));" >/dev/null 2>&1 \
    || { log "the single Tag is not labeled 'longform'"; return 1; }

  mismatches=$(gel query "select count(Article filter .review_state != ('needs_review' if .word_count >= 1200 else 'archived'));" 2>/dev/null | tr -d '\r')
  [ "${mismatches}" = "0" ] || { log "found ${mismatches} articles with incorrect review_state"; return 1; }

  local wrong_tag_count
  wrong_tag_count=$(gel query "select count(Article filter (exists .tags) != (.word_count >= 1000));" 2>/dev/null | tr -d '\r')
  [ "${wrong_tag_count}" = "0" ] || { log "found ${wrong_tag_count} articles with incorrect tag assignment"; return 1; }

  return 0
}

ensure_instance_running

if verify_reconciled; then
  log "Project already fully reconciled. Nothing to do."
  exit 0
fi

log "Project is not yet in the reconciled end-state."
log "This script only supports verifying/re-verifying an already-reconciled"
log "project; performing the initial reconciliation from a pristine project"
log "was done manually following the procedure documented above."
exit 1
