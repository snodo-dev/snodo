#!/usr/bin/env bash
# Merge the agent branches into main — safely.
#
#   · skips a branch that has no new commits (the agent did not commit)
#   · stops on a conflict without touching anything else
#   · runs the gates BEFORE pushing, and refuses to push if any fails
#   · resets an agent worktree only once its work is provably on origin/main
#
# The last rule is the important one: resetting a branch whose work has not
# landed discards it silently. That has already cost two issues once.
#
#   bash scripts/merge-agents.sh          # all agents
#   bash scripts/merge-agents.sh a        # just agent-a
#   bash scripts/merge-agents.sh a c      # agent-a and agent-c
#
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

ALL_BRANCHES="agent-a agent-b agent-c"
MERGED=""

worktree_for() {
  case "$1" in
    agent-a) echo "$HOME/Dev/snodo-a" ;;
    agent-b) echo "$HOME/Dev/snodo-b" ;;
    agent-c) echo "$HOME/Dev/snodo-c" ;;
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

# ── merge ────────────────────────────────────────────────────────────────
for branch in $BRANCHES; do
  if ! git show-ref --verify --quiet "refs/heads/$branch"; then
    echo "— $branch: no such branch, skipping"
    continue
  fi

  if git merge-base --is-ancestor "$branch" main; then
    echo "— $branch: NO NEW COMMITS — the agent did not commit. Skipping, and NOT resetting it."
    continue
  fi

  n="$(git rev-list --count "main..$branch")"
  echo
  echo "▸ merging $branch  ($n new commit(s))"
  if ! git merge --no-ff --no-edit "$branch"; then
    echo
    echo "✗ conflict merging $branch — unmerged paths:"
    git diff --name-only --diff-filter=U | sed 's/^/    /'
    echo
    echo "  Resolve them, then:"
    echo "    git add -A && git commit --no-edit && bash scripts/merge-agents.sh"
    exit 1
  fi
  MERGED="$MERGED $branch"
done

if [ -z "$(echo "$MERGED" | tr -d ' ')" ]; then
  echo
  echo "Nothing merged. Nothing reset."
  exit 0
fi

# ── gates, before the push ───────────────────────────────────────────────
echo
echo "▸ gates"
run_gate() {
  printf '  %-34s' "$1"
  if eval "$1" >/tmp/snodo-gate.out 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    echo
    tail -30 /tmp/snodo-gate.out | sed 's/^/    /'
    fail "gate failed: $1
  main holds the merge but was NOT pushed. Fix, commit, then re-run this script."
  fi
}
run_gate "uv run ruff check ."
run_gate "uv run lint-imports"
run_gate "uv run pytest tests/ -q -n auto"
run_gate "uv run pytest tests/ -m e2e -q -n auto"

echo
echo "▸ pushing"
git push || fail "push failed"
echo "✓ pushed"

# ── reset only what is provably landed ───────────────────────────────────
echo
git fetch origin --quiet
for branch in $MERGED; do
  wt="$(worktree_for "$branch")"
  if [ -z "$wt" ] || [ ! -d "$wt" ]; then
    echo "— $branch: worktree not found at '$wt', not resetting"
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
