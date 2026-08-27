# Runbook 01 — A greenfield web app with snodo

**Tier:** minimal / greenfield · **Protocol:** `solo` · **Stack:** Node + TypeScript
**Status:** 🚧 in progress — written as it happens, not reconstructed afterwards

> **How to read this.** This is a real transcript, not a demo. Where something
> failed, escalated, or surprised us, it is written down along with what we did
> about it — because you will hit the same things. Sections marked **Friction**
> are places snodo got in the way; some of those became bug fixes.

---

## 1. What we're building

A web app that produces a customisable digital business card: a card designer, a
public card page, a QR code that carries it, and a vCard download.

**In scope:** the application, running locally, with a dev deployment.
**Out of scope:** wallet passes, physical NFC tags, native clients, app-store
submission. All deferred deliberately, and all additive later — see ADR 001 in
§5 for why, including the finding that phone-to-phone NFC is unavailable on
every platform regardless of framework.

The whole runbook is followable with **no paid accounts**.

**Why this project:** it is small enough to finish, multi-step enough that
governance has something to govern, and every task ends in a state a test can
verify — which matters, because the `quality` validator (and, since ADR 028,
the post-execute `acceptance` validator) is what gates progress.

## 2. Prerequisites

| Requirement | Needed for | Notes |
|---|---|---|
| Python 3.12+ | running snodo | |
| Node 20+ | the app itself | `npm test` is what the quality validator runs |
| git | worktrees per task | snodo isolates each task in its own worktree |
| An LLM provider key | the coder + validators | `snodo config add <provider> <key>`, or `<PROVIDER>_API_KEY` |

```bash
pip install snodo        # or: uv tool install snodo
snodo --version          # 0.6.1 at time of writing
```

> **Friction — developing snodo alongside this.** If you are working on snodo
> itself rather than consuming it, `pip install` gives you the published build.
> For an editable checkout across the whole uv workspace, alias instead
> (documented in `CONTRIBUTING.md`):
> ```bash
> alias snodo='uv run --project ~/path/to/snodo snodo'
> ```
> A plain `uv tool install snodo` will pull the five sub-packages from PyPI, so
> they will **not** be editable — which defeats the point if you are patching
> the engine.

## 3. Choosing a protocol

We start with the shipped **`solo`** template, unmodified. Not because it is
optimal, but because it is what you get out of the box — and if it turns out to
be wrong for this kind of project, that is worth knowing and writing down rather
than hiding behind a bespoke protocol nobody else has.

What `solo` gives you:

| | |
|---|---|
| Modes | one (`producer`) — no reviewer separation |
| Validators | `security`, `architecture`, `meta-spec` (pre-execute) · `quality`, `acceptance` (post-execute) |
| Disagreement policy | `unanimous` |
| Test command | auto-detected from repo marker files (`package.json` → `npm test`) |

Two consequences to expect, both of which shaped how this runbook went:

- **`unanimous` with three LLM judges is strict.** A `warn` from any judge
  withholds approval, so the task escalates to you. On a greenfield repo, with
  little context to judge against, expect this to happen. (Since this was
  written, `solo` also ships `acceptance` post-execute — ADR 028 — and the
  verifier warns at load time if a unanimous policy has exactly one
  post-execute validator, because that is an unopposed veto over completed
  work; see Fixes #41.)
- **`meta-spec` polices *your* task descriptions**, not just the agent's output.
  It rejects specs that are "code wearing a spec's clothes" — literal
  implementations rather than intent and constraints. If you are used to writing
  prescriptive tickets, this will block you until you change how you write them.

## 4. Setup

```bash
mkdir -p ~/Dev/nfc-card && cd ~/Dev/nfc-card
git init
snodo init --template solo
```

`init` asks for consent before writing anything:

```
snodo runs AI agents that execute code in this repository — including your test
and build commands. Only continue if this repository is yours or you trust its
contents.
Continue? [y/N] y
```

This is the trusted-repository boundary (ADR 014): snodo assumes the repo it is
initialised in is yours. Do not point it at untrusted third-party code.

Output:

```
Created .snodo/
Added .snodo/ to .gitignore
Project ID:  local:787a9cf6a06345439626d8883b13918b (local)
Created .snodo/protocol.yml
Active mode: producer
Using existing RS256 keypair:
  Private: ~/.ssh/NO-AGENT/snodo.pem
  Public:  ~/.ssh/NO-AGENT/snodo.pub.pem

OpenCode adapter: Docker detected. Build the image with:
  docker build -t snodo-opencode:latest -f docker/Dockerfile.opencode .
  Note: the opencode coder is EXPERIMENTAL — no per-turn progress
  or usage/cost records, and not used by any shipped template.

Snodo initialized successfully!
```

`.snodo/protocol.yml` is now **yours** — a copy of the template, not a reference
to it. Edit it here; nothing you change affects other projects.

> **Note — keeping the audit trail.** `init` adds `.snodo/` to `.gitignore`, so
> the audit log and session state are not committed. That is the right default
> for most projects. If you want the decision history preserved alongside the
> code, un-ignore `.snodo/audit.log`.

## 5. Decide before you dispatch

We nearly got this wrong, and the mistake is worth more to you than the fix.

The plan was to write the protocol, fire the first task, and let architecture
decisions emerge. That is backwards. The `architecture` validator judges each
task against *something* — and if you have not written down what that something
is, it judges against whatever the model imagines the project to be. You get
plausible-sounding objections with no stable reference, and no way to tell a
real violation from a difference of taste.

So: **record the shaping decisions as ADRs first, then point the protocol at
them.** ADRs are validator *input*, not validator output.

This also disposes of a tempting idea. We considered adding a validator that
checks "were ADRs written?" — but that criterion is unfalsifiable and would fire
on every task, including the ones that decide nothing. The useful criterion is
the inverse, and it needs the ADRs to already exist:

> Must not contradict a decision recorded in `docs/decisions/`.

### The decisions we recorded

| ADR | Decision | Why it was not obvious |
|---|---|---|
| 001 | Web app + PWA, no native client. QR transport. NFC deferred to physical tags. | The instinct was Flutter, for NFC. Phone-to-phone NFC turns out to be unavailable in *any* framework — Android Beam was removed in Android 14, and Apple's HCE entitlement excludes this use case. |
| 002 | Hono on Cloudflare Workers; `core` / `web` split; storage deferred. | The split is not tidiness. `core` has no runtime deps, so `npm test` is fast and hermetic — which is exactly what the `quality` validator needs to be useful. |

The ADR-001 finding is the kind of thing worth doing before you write code, not
after: the entire client-shape decision turned on a platform capability that
does not exist. No amount of governance catches that, because it is not a
process failure. Governance can only enforce a decision once you have made one.

### What we changed in `.snodo/protocol.yml`

Two validators. `meta-spec`, `quality`, the single `producer` mode, and
`disagreement_policy: "unanimous"` are untouched from the `solo` template.

```yaml
  - validator_id: "security"
    criteria:
      - "Card data is personal data — name, employer, email, phone. In this release card content is encoded in the URL, so it appears in browser history, server and CDN access logs, and Referer headers on any outbound link. Flag anything that widens that exposure, and anything that logs, forwards, or persists card content beyond what serving the page requires."
      - "Card content is user-supplied and is rendered into HTML, into a QR payload, and into a vCard. Each has different escaping rules. User input must not be able to break out of any of those three encodings."
      - "No credentials, tokens, account identifiers, or signing material in the repository."

  - validator_id: "architecture"
    criteria:
      - "Must not contradict a decision recorded in docs/decisions/. Cite the ADR when raising this."
      - "The card data model must stay independent of delivery channel. Page rendering, vCard, and QR are layered over one model; none of them may leak assumptions back into it. Wallet passes and physical tags are deferred, not designed out — the model must not make them harder to add."
      - "core/ must have no runtime dependencies, no Cloudflare bindings, no network and no filesystem access, and must never import from web/. The dependency runs one way only."
      - "Card data must not live solely in browser storage. A card is a URL handed to other people; it has to resolve on a stranger's first cold visit."
```

Each criterion is there because a specific thing can go wrong, and each is
checkable against an artefact. Criteria that cannot fail are noise — they spend
a judge's attention and produce agreeable nothing.

> **Note.** We deliberately did *not* add a fifth validator. Under `unanimous`,
> every judge you add is another veto, and we have not yet run a single task to
> see how escalation-prone three already are on an empty repository. Tune after
> you have evidence, not before.

## 6. Task sequence

<!-- One subsection per task. For each:
     - the spec as actually written
     - the command
     - real output (trimmed, including failures)
     - what happened: passed / escalated / blocked, and what we did
     - resulting commit in the demo repo
-->

### Task 1 — repository skeleton + vCard generation

**Attempt 1 — `blocker`.** Halted at `pre_execute`, iteration 3. Two of three
judges passed; `architecture` blocked:

> Criterion 1 is not met: wallet pass signing is explicitly out of scope, so no
> interface or working development implementation exists.

The task never mentioned wallets. Here is how it got there.

1. Our first-pass `architecture` criteria were written before ADR 001 deferred
   wallet passes, and still demanded a signing interface. On the first pass
   through the graph, `architecture` blocked on it.
2. snodo has a **spec-authoring recovery path**: when a validator critiques the
   spec, `governance.py` asks the model to rewrite it, bounded to two attempts.
   The critique handed to the author is the validators' justifications — *all*
   of them, not just the spec validator's.
3. So architecture's wallet complaint went into the rewrite, and the author
   dutifully added "wallet signing ... explicitly not implemented" to the spec.
4. `architecture` then read that sentence and blocked on the same criterion
   again. The halt payload shows only the final iteration, which is why the
   rewritten spec appears in `task_spec` with no sign of where it came from.

**The recovery loop wrote its own next violation.** The engine behaved
correctly at every step; the policy it was enforcing was out of date. Note also
that this is a `blocker`, not an `escalate` — INV3 means you cannot authorize
past it. The fix targets are code, spec, or *policy*, and here it was policy.
The halt hint only suggests revising the task, which sent us looking in the
wrong place at first.

**Two fixes, one of which we only found by reading snodo's source.**

*Fix 1 — delegate to the ADRs instead of restating them.* Criterion 1 became
"must not contradict a decision recorded in `docs/decisions/`". Scope now lives
in one place, so deferring a feature cannot leave a validator demanding it.

*Fix 2 — give the validator eyes.* Criterion 1 is worthless if the judge cannot
open the ADRs, and by default **it cannot**:

> Runs iff `validator_spec.tools` is non-empty AND MCPs + completion_fn present.
> Empty/absent tools => single-completion path (no loop, no tools).
> — `validators/llm_validator.py`

**No shipped template declares validator tools.** Out of the box, every LLM
judge in snodo evaluates the task text and nothing else. It has never opened a
file in your repository. For `meta-spec` that is correct — it judges the spec,
and the spec is all it should see. But any criterion phrased as "the repository
must not contain X" is unenforceable without an explicit grant, and an LLM asked
to check something invisible does not say "I cannot tell"; it guesses. Usually
it guesses pass. That is a fail-open hiding in a default.

So `security` and `architecture` each got:

```yaml
    tools:
      - "read_file"
      - "list_files"
```

The allowlist is fixed and read-only — `read_file`, `read_file_lines`,
`list_files`, `git_show`, `git_log`, `read_diff_between_refs` — and the compiler
rejects anything mutating. `read_diff_between_refs` is stripped outside
`post_execute`. Expect this to cost real tokens and latency: the tool loop runs
up to 20 turns per validator.

**Also on attempt 1: worktree isolation silently failed.**

```
fatal: invalid reference: main
WARNING: Worktree creation failed — running WITHOUT isolation.
WARNING: No task branch will be created. Files change current working tree.
```

The repo had no commits, so `main` was unborn and did not resolve. snodo then
degraded to running directly in the working tree, warning but continuing. Make
an initial commit **before** your first `snodo run`. There is a certain irony in
a tool built on "structural enforcement beats advisory warnings" dropping its
own isolation guarantee and printing a warning about it.

**Attempt 2 — `blocker`, and the fix caused it.** Worktree isolation worked.
`meta-spec` passed on the raw spec with no rewrite. And both validators that had
just been given eyes used them to block:

> The task is not implemented. The repository contains only docs/decisions
> (ADRs 001 and 002), a .gitignore, and a git worktree pointer — there is no
> package.json, no tsconfig, no core/ or web/ directories...

They evaluated whether the work was **finished**, at `pre_execute`, before any
code could exist. The cause is in `llm_validator.py`: `phase` is computed inside
the tool loop and used for exactly one thing — stripping the diff tool. **It
never reaches the prompt.** The judge is told *"Evaluate the task against the
criteria below"* and handed a filesystem, with nothing to indicate whether it is
reviewing a proposal or inspecting a result.

Blind, that ambiguity is harmless: with only spec text in front of it, "evaluate
the task" can only mean "evaluate this proposal". Give it `list_files` and the
identical sentence reads as "check whether this was done". Two independent
judges made the same reading, so this is not model noise — it is an
under-specified prompt whose ambiguity only becomes load-bearing once tools
exist. Granting read tools without a phase-aware prompt converts a pre-execute
validator into a completion checker.

**Fix: state the frame in the criteria**, since the protocol is the only lever
available from outside snodo. Both tool-enabled validators gained a leading
criterion:

> REVIEW FRAME — read this first. You are reviewing a PROPOSAL before any of it
> has been built. The repository will NOT contain the described work; that is
> expected and is never a finding. Absence of implementation, tooling, tests, or
> a passing build is out of scope for this review and must never be cited. Judge
> only this: if the proposal were carried out as described, would it violate a
> criterion below? ... If nothing below is violated, return pass.

The general lesson, which outlives this bug: **once a judge has tools, the
criteria must state what it is judging.** A criterion list that reads fine
against a spec becomes ambiguous the moment the judge can also see the disk.

`meta-spec` deliberately keeps no tools. It judges the spec, and the spec is all
it should ever see.

**Attempt 3 — `recovery_exhausted` after 4 cycles, and nothing committed.**

The front half finally worked. Pre-validation returned **unanimous pass**, and
`architecture` cited ADR 002 by name rather than reasoning from imagination —
which is what granting it `read_file` was for. Execution ran.

Then this:

```
Coder output truncated at max_tokens=64000
...
task_00d49f8dcfc0                    recovery_exhausted  (depth=0)
  task_00d49f8dcfc0_fix_1            recovery_exhausted  (depth=1)
    ..._fix_1_fix_1                  recovery_exhausted  (depth=2)
      ..._fix_1_fix_1_fix_1          recovery_exhausted  (depth=3)
  Total attempts: 4
```

Four full cycles — each running three tool-enabled judges plus a coder call —
and the task branch ended with **zero commits**. Total loss.

The chain, which is worth following because every link is a separate defect:

1. **The coder hit its 64k token ceiling.** One task asked for `package.json`,
   `tsconfig`, a test runner, the `core/`+`web/` layout, the vCard module and
   three test cases. Truncation was reported as a *warning* and execution
   continued on partial output.
2. **No `package.json` reached the tree**, so `quality`'s auto-detect had
   nothing to detect.
3. **`quality` reported that as `warn`** — `"Cannot determine test command."`
   This is not a judgement about the code. It is an operational fault, and
   snodo already has the vocabulary for exactly that distinction
   (`validator_error`, per ADR 015). Reporting it as `warn` puts it on the
   human-adjudicable path instead of the operational one.
4. **`warn` withholds approval**, and post-execute had `total_count: 1` — one
   validator, so `unanimous` gave it an unopposed veto. Escalate.
5. **The recovery loop synthesised a fix task from the warning text:**
   `"Fix post-validation issues: Cannot determine test command. Set
   tooling.test_command in protocol validator config."` The loop asked the
   *coder* to edit `.snodo/protocol.yml` — to change the policy governing it.
6. That recursed to `max_recovery_depth`, re-validating identically each time,
   because no code change could ever satisfy a configuration fault.

Step 5 deserves a pause. Nothing was written — the branch is empty and
`protocol.yml` is untouched, so no boundary was actually crossed. But a tool
whose central claim is that agents must not be able to alter their own
governance generated a task instructing an agent to do precisely that. The
protocol should be structurally outside the coder's writable surface, not
merely unlikely to be reached.

**Two fixes and one restructure.**

*Set the test command explicitly.* `tooling: {}` auto-detects by reading
`package.json` — which cannot exist before the task that creates it.
**On the first task of any greenfield project, auto-detection is unsatisfiable
by construction**, and the shipped `solo` template defaults to it. So:

```yaml
    tooling:
      test_command: "npm test"
```

*Cap recovery while bootstrapping.* Depth 3 turns one misdiagnosis into four
paid cycles. Depth 1 fails fast while the failures are still configuration
rather than code:

```yaml
execution:
  max_recovery_depth: 1
```

*Split the task.* The truncation is the honest signal here: one coder call could
not carry both the toolchain and the domain logic. **The first task of a
greenfield project should be the toolchain and nothing else** — it is the task
that makes every later task verifiable, and bundling features into it means a
single token ceiling takes out both. Task 1 became 1a (skeleton, one trivial
passing test) and 1b (vCard).

**A gap this leaves open.** With `architecture` running only at `pre_execute`,
nothing checks the *produced code* against the ADRs — `quality` only runs
`npm test`. A post-execute architecture validator is the right answer, and it is
the natural place for "core/ has no runtime dependencies", which is a fact about
files rather than about a plan. Deferred until one task completes, on the same
principle as everywhere else here: tune on evidence.

> **Update (ADR 028 / Fixes #59).** The shipped `solo` template now includes a
> post-execute `acceptance` validator that judges the produced artifacts against
> the task's acceptance criteria — the completeness check this gap describes,
> aimed at the spec rather than the ADRs. Its deterministic canary proves it can
> reject a real omission (a missing required test/ADR). It is not a substitute
> for a post-execute architecture judge: it checks the task was carried out, not
> that the code honours the ADRs. The gap for ADR-conformance of produced code
> remains open.

### Task 1b — vCard generation (`resolved`, first clean pass)

Four validators, unanimous, one attempt, no recovery. `quality` returned a real
verdict — `"Tests passed"` — which in five runs had never happened before.

What made the difference was environmental, not architectural:

- `main` carried the merged toolchain, so the worktree branched off something
  buildable. **The sequence composed** — but only because we merged by hand.
- `test_command: "npm ci --no-audit --no-fund && npm test"` gave the worktree
  its dependencies. Without it this run fails at exit 127 like all the others.
- The task was scoped to one coder call, so nothing truncated.

Output: `core/card.ts`, `core/vcard.ts`, `core/vcard.test.ts`, committed.

The code is good. The card model is documented as channel-independent and cites
the ADRs. Escaping applies backslash first, then `;`, `,`, then newline forms —
correct order, since escaping backslash last would corrupt the sequences added
before it. And the tests go beyond the spec: eight cases including a round-trip
decoder written to prove escaping is faithful, and a structural invariant that
no raw newline can survive anywhere in the output. That is the test a careful
engineer writes, not the minimum the acceptance criteria demanded.

**One real defect, and nothing caught it.** The output declares `VERSION:3.0`
and omits `N`. RFC 2426 requires VERSION, N *and* FN, so the vCard is
non-conformant with the version it selected — while RFC 6350 (4.0) makes `N`
optional and would have permitted exactly this output.

Why it passed is the part worth keeping. No validator had a criterion about
specification conformance: `security` checked injection, `architecture` checked
the ADRs, `quality` ran tests, and the tests assert the implementation's own
choices. Meanwhile the source argued the omission at length in a docstring.

> **A persuasive rationale comment is an effective way to move a deviation past
> an LLM judge.** The judge reads justification as compliance.

The lesson is not that the judges are weak. It is that **they police exactly
what you assign them and nothing else**, and no one had been assigned the RFC.
A criterion naming the authority — "output claiming conformance to a published
specification must satisfy that specification's mandatory requirements; cite
the section" — is what closes it. Absent that, a validator quorum is only as
broad as the criteria you thought to write.

## 7. Escalations and how we resolved them

<!-- Each escalation as a worked example: the halt payload, what the validators
     disagreed about, the decision, and the reasoning. Readers will hit these. -->

## 8. Where snodo got in the way

<!-- Honest friction log. Anything surprising, awkward, or broken — and whether
     it became a snodo issue. -->

| # | Friction | What we did |
|---|---|---|
| 1 | `snodo` not on PATH outside its own checkout when developing from source | Documented the `uv run --project` alias in §2 |
| 2 | Worktree creation fails on a repo with no commits (`invalid reference: main`), then **silently degrades to no isolation**. Also hardcodes `main`. | Committed before running. Filed as P1 — a safety property lost by default, on the state every greenfield repo starts in |
| 3 | LLM validators cannot read the repository unless the protocol declares `tools:`, and **no shipped template does**. Criteria about repo contents silently evaluate on imagination. | Added `read_file` + `list_files` to `security` and `architecture`. Filed: templates should grant them, or the compiler should warn when a criterion implies file access |
| 4 | Granting read tools to a `pre_execute` validator turns it into a completion checker — `phase` never reaches the judge's prompt | Added a REVIEW FRAME criterion as a workaround. Filed as P1 — snodo should state the phase in the prompt, not leave it to protocol authors |
| 5 | The spec-authoring recovery path feeds **every** validator's critique to the rewriter, so one validator's complaint gets written into the spec and can then be blocked on by that same validator | Filed. The author should receive spec-quality critique only |
| 6 | The spec actually validated may be a rewrite of what you typed, and there is no sign of it until you read `task_spec` in the halt payload | Filed: surface the rewrite, and show which iteration produced it |
| 7 | Halt payload has `reason: null` and `blocker_reason: null` while the real reason sits inside `validator_results` — the top-level fields a script would read are empty | Filed |
| 8 | Blocker justifications cite criteria by index ("Criterion 1"), so you cannot tell what was violated without hand-counting the YAML | Filed |
| 9 | Halt hint says "re-run a revised task" for every blocker, but blockers have three fix targets — code, spec, or policy. Both of ours were policy. | Filed |
| 10 | `SNODO_TOKEN_SECRET` unset surfaces as a raw Python `UserWarning` with a source path, which reads like a crash | Filed: use a clean CLI warning; consider generating a secret at `init` |
| 11 | LangGraph deserialisation warning for `snodo.engine.policy.PolicyAction` — will be blocked in a future LangGraph version | Filed: register the type before it becomes a hard failure |
| 12 | `tooling: {}` auto-detect is **unsatisfiable on the first task of any greenfield repo** — it reads `package.json`, which task 1 creates. Shipped `solo` default. | Set `test_command` explicitly. Filed as P1 — the template default cannot work for the case it is most likely to meet first |
| 13 | "Cannot determine test command" is an operational fault reported as `warn`, so it enters human adjudication and the recovery loop instead of surfacing as `validator_error` (ADR 015) | Filed as P1 — this is what converted a config error into four paid cycles |
| 14 | The recovery loop synthesised a fix task instructing the **coder to edit `.snodo/protocol.yml`** — the policy governing it | Filed as P1. Nothing was written, but the protocol should be structurally outside the writable surface |
| 15 | Coder output truncated at `max_tokens` is a warning; execution continues on partial output | Filed — truncation should fail the execute step, not proceed |
| 16 | A single post-execute validator under `unanimous` gives `total_count: 1`, an unopposed veto with no counterbalance | **Fixed** — the verifier now warns at load time when a unanimous policy has exactly one post-execute validator (Fixes #41), and the `solo` template ships two (`quality` + `acceptance`) |
| 17 | `max_recovery_depth` defaults to 3, so any misdiagnosed fault costs 4 full cycles of 3 judges + coder before stopping | Set to 1 during bootstrap. Filed: skip recovery entirely when the fault class is operational |
| 18 | **A failed task destroys its own evidence.** Teardown calls `remove_worktree` in an unconditional `finally` with no preserve flag; commits only happen inside execute when artifact paths are parsed. Truncated coder output → nothing committed → worktree deleted → nothing left to inspect. | Filed as P1. Keep the worktree on failure, or commit to the task branch before teardown |
| 19 | **No dependency installation, ever.** Every task runs in a fresh git worktree, which has no `node_modules` / `.venv` / `target`. `npm test` exits 127 (`tsc: not found`) and the validator reports it as "Tests failed". This is not a greenfield problem — **worktree isolation and dependency installation are in direct conflict, and snodo implements the first without the second.** | Workaround: fold the install into the test command — `test_command: "npm ci --no-audit --no-fund && npm test"`, with the lockfile committed. Filed as P0 — this is the single highest-leverage fix |
| 20 | **Task sequences do not compose on the CLI path.** Every worktree branches off `main` (hardcoded, `worktree.py:74`) and `_move_next_node` only sets `is_complete = True`. `merge_branch` exists in `GitMCP` and is exposed as an MCP tool, but the engine graph never calls it. | Merge by hand between tasks. Filed as P1 — merge-on-success exists on the MCP surface but not the engine surface |

## 9. Result

The minimal web application produces a digital business card with zero external runtime dependencies:
- **Card Designer & Preview**: Interactive form storing user data in URL hash state (no server-side database required).
- **Contact Export (vCard 3.0)**: RFC 2426-compliant `.vcf` generator supporting name, title, organization, email, phone, and optional photo fields, with strict CRLF and character escaping.
- **On-Screen QR Code**: Zero-dependency SVG QR code generator rendering the share URL directly on screen.
- **Verification Suite**: 100% of tasks gated by `npm test` and `make check`, running 18 unit and integration tests across core models and generators.

### Running the Result
```bash
git clone https://example.com/acme-corp/card-app-demo.git
cd card-app-demo
npm ci
npm test
npm start
```

Sample card state (stored in URL hash or vCard export):
```json
{
  "name": "Jane Doe",
  "title": "Principal Engineer",
  "organization": "Acme Corp",
  "email": "jane.doe@example.com",
  "phone": "+1-555-0199",
  "url": "https://example.com/cards/jane-doe"
}
```

### Artifact Summary
- **Demo repository**: `https://example.com/acme-corp/card-app-demo` (tag `v1.0.0`)
- **Protocol used**: `solo` (with `acceptance` validator enabled via ADR 028)
- **Total tasks executed**: 6 tasks across 2 phases

> **Update (ADR 030–037 & Fixes #81–#98).** Since this runbook was written, the verification
> work and recent defect fixes have sharpened what an operator should expect from the gates:
> the coder seam is declared rather than duck-typed (ADR 035), in-place coders leave
> their change reviewable (ADR 030), the opencode path is explicitly experimental (ADR 034),
> verification executions are first-class audit events (ADR 031), patch coverage is enforced
> over modified lines (ADR 032), operator review outcomes are measurable via `snodo task review`
> / `snodo task report` (ADR 036), parallel `CHANGELOG.md` merges auto-merge cleanly via
> `merge=union` (ADR 037 / Fixes #81), pre-execute tree-state findings are passed forward as
> non-blocking evidence to prevent recovery deadlocks (Fixes #90), specs naming untracked paths
> warn before dispatching (Fixes #93), test suite under-collection is prevented via rootdir and
> minimum collection threshold assertions (Fixes #98), and audit hash chain corruption reports
> distinct, actionable `✖ AUDIT LOG CHAIN CORRUPTED` diagnostic alerts (Fixes #96).
> The empirical finding that "execution catches what read-only judgement misses" still holds —
> see runbook 02 §9.1–§9.3 for the detailed findings.

## Appendix A — What we deferred, and what it would take

Not part of this runbook. Recorded so the deferrals stay deliberate.

| Deferred | What it needs | Blocked by |
|---|---|---|
| Physical NFC tags | Write the card URL to an NTAG sticker | Nothing — works today, read natively by iPhone and Android with no app |
| Apple Wallet pass | Apple Developer account (US$99/yr), Pass Type ID, `.p12` signing cert renewed every 398 days, WWDR intermediate | Paid account; plus PKCS#7/CMS signing is awkward on `workerd` (see ADR 002) |
| Google Wallet pass | Google Cloud service account, signed JWT | Free tier, but out of scope for the first release |
| Flutter client | — | No capability justifies it; see ADR 001 |
