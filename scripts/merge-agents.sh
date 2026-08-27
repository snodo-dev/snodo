#!/usr/bin/env bash
# Merge the agent branches into main — safely, gated on each branch's CI.
#
# This is a thin wrapper over `snodo merge` (the CI-authorized merge engine,
# Fixes #57). The merge itself — checking each branch's CI conclusion and
# refusing anything that is not green — is done by `snodo merge`; this script
# only owns the environment-specific orchestration that `snodo merge` cannot
# know about:
#
#   · which worktrees map to which agent branches,
#   · that a merge must only happen while on `main` and fetched fresh,
#   · that the merged result is pushed before any worktree is reset,
#   · that a worktree is reset only when its branch is provably on origin/main
#     AND the worktree is clean (resetting a dirty tree destroys work in
#     progress — that has already cost two issues once).
#
#   bash scripts/merge-agents.sh          # all agents
#   bash scripts/merge-agents.sh a        # just agent-a
#   bash scripts/merge-agents.sh a c      # agent-a and agent-c
#
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

ALL_BRANCHES="agent-a agent-b agent-c agent-d"
MERGED=""

worktree_for() {
  case "$1" in
    agent-a) echo "$HOME/Dev/snodo-a" ;;
    agent-b) echo "$HOME/Dev/snodo-b" ;;
    agent-c) echo "$HOME/Dev/snodo-c" ;;
    agent-d) echo "$HOME/Dev/snodo-d" ;;
    *)       echo "" ;;
  esac
}

# Accept "a", "agent-a", or "snodo-a" — all mean the same branch.
normalise() {
  case "$1" in
    agent-*) echo "$1" ;;
    snodo-*) echo "agent-${1#snodo-}" ;;
    *)       echo "agent-$1" ;;
  esac
}

if [ "$#" -gt 0 ]; then
  BRANCHES=""
  for arg in "$@"; do
    b="$(normalise "$arg")"
    case " $ALL_BRANCHES " in
      *" $b "*) BRANCHES="$BRANCHES $b" ;;
      *) echo "✗ unknown agent '$arg' (known: $ALL_BRANCHES)"; exit 1 ;;
    esac
  done
  echo "▸ scope: $(echo "$BRANCHES" | xargs)"
else
  BRANCHES="$ALL_BRANCHES"
fi

fail() {
  echo
  echo "✗ $*"
  echo "  Stopping. No worktree was reset."
  exit 1
}

echo "▸ fetching"
git fetch origin --quiet || fail "fetch failed"

current="$(git rev-parse --abbrev-ref HEAD)"
[ "$current" = "main" ] || fail "you are on '$current', not main"

git merge-base --is-ancestor origin/main main \
  || fail "local main is behind origin/main — run: git pull --ff-only"

# ── push each branch so CI runs on it ──────────────────────────────────────
# CI triggers on `push: branches: ['**']`, so a branch that is never pushed
# never gets a CI conclusion — and `snodo merge` would refuse it as "CI has
# not run". The agent branches live only locally, so push them to origin
# first; GitHub runs the CI workflow on the push and the gate has something
# to query (Fixes #57). Branches already on the remote are skipped.
echo
for branch in $BRANCHES; do
  git show-ref --verify --quiet "refs/heads/$branch" || continue
  if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    echo "— $branch: already on origin"
  else
    echo "▸ pushing $branch so CI can run on it"
    git push -u origin "$branch" || fail "failed to push $branch to origin"
  fi
done

# ── merge, gated on CI ────────────────────────────────────────────────────
# The CI gate is the whole point: the merge is authorised by the branch's CI
# conclusion (queried by `snodo merge`), never by an agent's self-reported
# gate results (Fixes #57). `snodo merge` operates on the git root, skips
# branches with no new commits (resume-safe after a hand-resolved conflict),
# and stops on the first refusal or conflict.
echo
# The tool enforcing the gate must not run from an agent's worktree, which may
# be mid-task, on a different commit, or about to be reset (Fixes #73). Run it
# from the repository being merged (the checkout this script is run in) or from
# an installed snodo — never from ~/Dev/snodo-*.
if command -v snodo >/dev/null 2>&1; then
  echo "▸ snodo merge $BRANCHES"
  # shellcheck disable=SC2086
  snodo merge $BRANCHES || fail "snodo merge refused or failed"
else
  # Fall back to the editable-checkout alias (CONTRIBUTING.md), resolved
  # against THIS repository (the one being merged), not an agent worktree.
  SNODO_CMD="${SNODO_CMD:-uv run --project \"$PWD\" snodo}"
  echo "▸ $SNODO_CMD merge $BRANCHES"
  # shellcheck disable=SC2086
  eval "$SNODO_CMD merge $BRANCHES" || fail "snodo merge refused or failed"
fi

# The merge may have skipped some branches (already ancestors). Track which
# of the ones we care about actually moved — same logic as the old script:
# a branch is "merged" once it is an ancestor of main.
MERGED=""
for branch in $BRANCHES; do
  if git show-ref --verify --quiet "refs/heads/$branch" \
     && git merge-base --is-ancestor "$branch" main; then
    MERGED="$MERGED $branch"
  fi
done

# Resume-safe: what matters is whether main has anything unpushed, not whether
# THIS invocation did the merging. After resolving a conflict by hand you
# re-run the script, and by then every branch is already an ancestor of main.
UNPUSHED="$(git rev-list --count origin/main..main 2>/dev/null || echo 0)"

if [ -z "$(echo "$MERGED" | tr -d ' ')" ] && [ "$UNPUSHED" = "0" ]; then
  echo
  echo "Nothing to merge and nothing unpushed."
  echo "Checking whether any worktree can be reset anyway..."
  MERGED="$ALL_BRANCHES"
  SKIP_GATES=1
fi

if [ "${SKIP_GATES:-0}" = "1" ]; then
  echo
else

# ── push the merged result ─────────────────────────────────────────────────
echo
echo "▸ pushing  ($UNPUSHED commit(s) ahead of origin/main)"
git push || fail "push failed"
echo "✓ pushed"

fi   # end of push block

# ── reset only what is provably landed ───────────────────────────────────
#
# Sweep every branch in scope, not just the ones this invocation merged: a
# branch merged in an earlier run is still sitting behind origin/main and an
# agent starting there would stop on a stale base. The ancestry guard below is
# what makes the wider sweep safe.
echo
git fetch origin --quiet
for branch in $BRANCHES; do
  git show-ref --verify --quiet "refs/heads/$branch" || continue
  wt="$(worktree_for "$branch")"
  if [ -z "$wt" ] || [ ! -d "$wt" ]; then
    echo "— $branch: worktree not found at '$wt', not resetting"
    continue
  fi
  # An agent may be working in this worktree right now. Its branch will look
  # "merged and behind" until it commits, so ancestry alone does not make a
  # reset safe — resetting a dirty tree destroys work in progress. This has
  # happened once; do not let it happen again.
  if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]; then
    echo "— $branch: worktree has uncommitted changes (agent may be running) — NOT resetting"
    continue
  fi

  if git merge-base --is-ancestor "$branch" origin/main; then
    if git -C "$wt" reset --hard origin/main --quiet; then
      echo "✓ $branch: reset $wt to origin/main"
    else
      echo "— $branch: reset failed (uncommitted changes in the worktree?)"
    fi
  else
    echo "— $branch: NOT an ancestor of origin/main — refusing to reset"
  fi
done

echo
echo "Done."
