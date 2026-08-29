# Note for the plan work

Date: 2026-08-28. Ylli's observation, kept for when `snodo plan` is picked up.

## What an orchestrator actually produces

An external orchestrator (an Opus model) decomposing a spec into a plan emits
exactly this:

```bash
# 1. amend the record  (decide mode)
snodo mode change decide
snodo run "$(cat ~/.snodo/specs/w1-notify-c-adr-amend.txt)"
# then git merge the branch — decide doesn't auto-merge

# 2. swap the implementation  (build mode)
snodo mode change build
snodo run "$(cat ~/.snodo/specs/w1-notify-d-send-email-binding.txt)"

# 3. finally, the inbox status
snodo run "$(cat ~/.snodo/specs/w1-notify-b-screen.txt)"
```

That is the whole thing. A plan is an **ordered list of (mode, spec) pairs**,
with a merge point where the mode does not auto-merge.

## What this tells us

**The real Plan type is smaller than the one planner.py implements.**
`PlannerMCP` carries waves, parent refs, depth resolution, cycle detection and
a separate status.json. The observed unit of work needs: order, a mode, a spec,
and whether the step merges. Waves are a tagging/grouping convenience over that
list, not the primitive.

**Mode is NOT required on a plan step.** (Ylli, same day.) The example above
crosses modes only because it amends an ADR mid-flight — moving from SendGrid to
Cloudflare Email. A plan may assume the architecture is already decided, in
which case every step runs in one mode and `plan_run`'s current
mode-agnosticism is correct. Do not add per-step mode on the strength of this
example. Revisit only if a real plan needs to cross modes; the cost of not
having it is one `snodo mode change` between plan runs.

**Merge behaviour differs by mode.** `decide` does not auto-merge; `build` does
(`run_cmd._merge_on_success`). A plan running wholly in one mode inherits that
mode's behaviour consistently, so this is only a hazard for the cross-mode case
above.

## Implication for sequencing

Before auto-decomposition is worth building:

1. Give `Plan` a type (pydantic model + well-formedness check), the way
   `Protocol` has `compiler/models.py` + `compiler/verifier.py`. Today plans are
   untyped dicts on disk.
2. Put `mode` on the task/step, and have `plan_run` apply it per step.
3. Make the merge point explicit per step rather than implied by mode.
4. Let a plan be authored by hand from a file — the shell script above is
   already a working hand-authored plan, so the format has a known-good target.

Only then is "LLM writes the specs" (auto-decompose) an addition to a proven
path rather than a new path on an untyped one.
