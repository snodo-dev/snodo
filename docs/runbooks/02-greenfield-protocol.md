# Runbook 02 — Building a digital business card, end to end

**Protocol:** `greenfield` · **Project:** a shareable digital business card, live at a custom domain
**Status:** ✅ 6 phases complete and verified against snodo v0.7.0+

> This is not a template. Every command below is the exact command that was run,
> in order, on a real project. Copy them. Runbook 01 is the discovery log that
> produced this path — read that one if you want to know why each step is here.

---

## 0. What gets built

A web page where you fill in your details and get a shareable link. Anyone who
opens that link sees your card, can save you to their contacts, and can scan a
QR code from your screen. No accounts, no backend, no app.

Everything is decided by the project itself in phase 1 — the stack below is what
it chose, not what was imposed:

| | |
|---|---|
| Runtime | vanilla JS ES modules, Node LTS ≥ 20 for tooling |
| Dependencies | **zero**, enforced by a check inside the build |
| Verification | `make check` |
| Hosting | Cloudflare Pages via wrangler, custom domain |
| Contact format | vCard 3.0 (RFC 2426) |

## 1. Prerequisites

```bash
python3 --version     # 3.12+
node --version        # 20+
git --version
snodo --version       # 0.7.0+
```

An LLM provider key: `snodo config add <provider> <key>`.

For the deploy step only: a Cloudflare account, and a domain whose DNS is on
Cloudflare. Everything up to deploy works without either.

## 2. Create the project

```bash
mkdir ~/Dev/mycard && cd ~/Dev/mycard
git init
git commit --allow-empty -m "init"
snodo init --template greenfield
git add .gitignore && git commit -m "chore: commit .gitignore"
```

**The initial commit is required.** Worktrees branch off `main`, which does not
resolve on a repo with no commits. (In snodo v0.7.0+, unborn-HEAD worktree creation
fails loud with `WorktreeError` per ADR 025 / Fixes #29, rather than degrading to
no isolation).

**Commit `.gitignore` immediately.** `init` writes `.snodo/` and adds it to `.gitignore`.
In snodo v0.7.0+ (Fixes #24), `snodo init` commits `.snodo/` and `.gitignore` safety guards.

Note: `greenfield` template registration was fixed in Issue #19 (`snodo init --template greenfield` works out of the box).

Leave `test_command` as `REPLACE_ME`. You cannot know it yet — it is an output
of phase 1.

## 3. Phase 1 — decide

```bash
snodo mode change decide
```

```bash
snodo run "$(cat <<'EOF'
Decide and record how this project will be built.

INTENT
Establish the decisions the rest of the project depends on, as records in
docs/decisions/. No implementation, no toolchain, no code.

CONTEXT
The product is a customisable digital business card. A person fills in their
details, gets a page they can share, and the recipient can save them as a
contact. Exchange is phone-to-phone. The first release must be buildable and
testable with no paid developer accounts.

CONSTRAINTS
- Produce one record per decision in docs/decisions/, numbered.
- Decide at minimum: language and runtime, hosting and deployment target,
  repository layout, the single verification command, and the boundary of the
  first release.
- Investigate before deciding. Where a decision depends on a platform
  capability, establish whether that capability is actually available rather
  than assuming it.
- Record what is deliberately deferred and what picking it up would require.

ACCEPTANCE
- Every decision above is recorded, with alternatives considered and rejected,
  and with consequences including what it makes harder.
- No record contradicts another.
- Nothing is implemented.
EOF
)"
```

Produced seven records in one pass. **Read them before merging** — this is the
only phase whose exit gate is one model's opinion of another's prose.

```bash
cat docs/decisions/0004-verification-command.md
sed -i '' 's|test_command: "REPLACE_ME"|test_command: "make check"|' .snodo/protocol.yml
git merge --no-ff $(git branch --format='%(refname:short)' | grep decide-and-record)
git worktree prune
```

> **A record is not verified by being recorded.** ADR-0004 specified
> `node --test tests/`. That command does not work — Node resolves a bare
> directory as a module rather than scanning it. The decide gate passed it; in
> the next phase two more validators confirmed the implementation matched the
> record. Three judges agreed on a broken command because all three were
> reading. Only execution found it.

## 4. Phase 2 — scaffold

```bash
snodo mode change scaffold
```

```bash
snodo run "$(cat <<'EOF'
Establish the toolchain and repository skeleton.

INTENT
Make the project verifiable. After this task the verification command recorded
in docs/decisions/ must run and pass from a clean checkout, so every later task
has a working gate. No domain logic.

CONSTRAINTS
- Implement the language, runtime, repository layout and verification command
  exactly as recorded in docs/decisions/. Where a record specifies something,
  follow it; do not re-decide. If a record is ambiguous or two records conflict,
  stop and say so rather than choosing.
- Each task runs in a fresh git worktree with no installed dependencies. The
  verification command must therefore work from a clean checkout, which means
  dependency installation is part of it or of a documented step. Commit the
  lockfile if the ecosystem has one.
- Any dependency boundary the records assert must be enforced by something that
  executes — a build configuration or a check inside the verification command.
  A boundary that is only described in prose is not enforced.
- Include the minimum code needed to prove the toolchain works: one trivial
  exported unit and one test for it. No product behaviour.

ACCEPTANCE
- The recorded verification command runs and exits zero from a clean checkout
  with no credentials present.
- The repository layout matches what the records specify.
- The dependency boundary fails the verification command when violated.
- No product feature is implemented.
EOF
)"
```

**This blocks.** `quality` reports `Tests failed (exit 2)` — the broken command
from ADR-0004. The message names no test and no assertion, so reconstruct the
worktree and run it yourself:

```bash
B=$(git branch --format='%(refname:short)' | grep toolchain)
rm -rf /tmp/sg && mkdir -p /tmp/sg && git archive "$B" | tar -x -C /tmp/sg
cd /tmp/sg && make check; cd -
```

`Error: Cannot find module '.../tests'`. Fix forward:

```bash
git merge --no-ff "$B"
sed -i '' 's|node --test tests/\{0,1\}$|node --test "tests/**/*.test.js"|' Makefile
sed -i '' 's|node --test tests/|node --test "tests/**/*.test.js"|g' docs/decisions/0004-verification-command.md
make check
git add -A && git commit -m "fix: node --test needs a glob, not a directory path"
git worktree prune && git branch -D "$B"
```

`make check` now: `check-deps: OK`, `check-syntax: OK`, 1 test passing,
`build: OK`.

## 5. Phase 3 — build the contact export

```bash
snodo mode change build
```

```bash
snodo run "$(cat <<'EOF'
Implement the save-as-contact capability.

INTENT
Turn a person's card details into something their contact app can import. This
is the first product capability; the toolchain is in place and must not change.

CONSTRAINTS
- Follow the format and scope recorded in docs/decisions/. Do not re-decide. If
  a record is ambiguous or two records conflict, stop and say so.
- Add no dependencies. The dependency boundary check must keep passing.
- Do not modify the Makefile, the check scripts, or the CI workflow.
- Field values are user-supplied and may contain characters that are
  significant in the output format, including separators, escapes and
  newlines. These must not be able to alter the structure of the output. Treat
  this as a correctness requirement, not formatting.
- Output claiming conformance to a published specification must satisfy that
  specification's mandatory properties. Cite the section.
EOF
)"
```

Keep those last two constraints in **every** build task. The spec-conformance
one is why this project emits a valid `N` property; the same model without it
shipped a `VERSION:3.0` vCard missing `N` — non-conformant with the spec it
declared — and every validator passed it.

**This blocks too.** Six of ten tests error with
`TypeError: Cannot read properties of undefined (reading 'base64')` — an
unguarded optional field. Reconstruct as above to see it, then fix through the
loop rather than by hand:

```bash
snodo run "$(cat <<'EOF'
Fix the vCard generator crashing on cards without a photo.

INTENT
`make check` fails: six of ten tests error with
`TypeError: Cannot read properties of undefined (reading 'base64')` from
photoProperty in src/scripts/vcard.js, reached from buildVCard. The photo field
is optional, but the code dereferences it unconditionally, so any card without
one throws before producing output.

CONSTRAINTS
- Fix the defect, not the tests. The failing tests describe correct behaviour.
- Audit every other optional field for the same pattern. A card with only the
  mandatory properties must generate successfully.
- Absent optional fields must emit no property at all, not an empty one.
- Add no dependencies. Do not modify the Makefile, check scripts, or CI.
- Preserve the existing escaping, folding and CRLF behaviour unchanged.

ACCEPTANCE
- `make check` passes, all ten tests.
- A test covers a card carrying only the mandatory properties, so this
  regression cannot return.
EOF
)"
```

```bash
git merge --no-ff $(git branch --format='%(refname:short)' | grep crashing)
make check          # 10 tests, 10 pass
git worktree prune
git branch -D $(git branch --format='%(refname:short)' | grep save-as-contact)
```

> **Merge after every task.** Worktrees branch off `main` and nothing merges on
> success (issue #20). The fix task above branched off a `main` that had never
> received the feature — so it reimplemented the whole thing from scratch rather
> than fixing it. It came out correct, but only by luck.

## 6. Phase 4 — change the hosting decision

Publishing at a custom domain on Cloudflare contradicts the recorded hosting
decision, and adding a QR code contradicts the release boundary and the
zero-dependency commitment. Those are decisions, so they go back to `decide`.

```bash
snodo mode change decide
```

```bash
snodo run "$(cat <<'EOF'
Revise the hosting and release-boundary decisions.

INTENT
The project will be published at a custom domain on Cloudflare Pages, deployed
with wrangler, and the first release must include an on-screen QR code. Two
recorded decisions no longer hold. Revise them.

CONSTRAINTS
- Supersede ADR-0002. Hosting is Cloudflare Pages, deployed via wrangler, served
  at a custom domain. Record what this makes harder and what it costs compared
  with what was chosen before, and what happens to the existing GitHub Actions
  workflow.
- Amend the release boundary so an on-screen QR code carrying the share URL is
  in scope for v1, and remove it from the deferred register if it is listed
  there.
- Resolve the conflict this creates. QR generation conventionally requires a
  library, and the project is committed to zero runtime dependencies. Decide
  between relaxing that commitment for this case and implementing the encoder
  directly, and justify the choice on evidence rather than preference —
  including the size and maintenance cost of each option.
- Do not re-decide anything else. If a revision forces a change to another
  record, say so rather than editing it.
- No implementation.

ACCEPTANCE
- The superseding records state alternatives, consequences, and what each makes
  harder.
- The dependency question is settled explicitly, with the reasoning recorded.
- No record contradicts another; superseded records are marked as such.
EOF
)"
```

Result: ADR-0008 supersedes ADR-0002 (Cloudflare Pages + wrangler), ADR-0009
keeps zero dependencies and commits to a hand-written QR encoder, and ADR-0005
and ADR-0007 are amended for scope.

```bash
git merge --no-ff $(git branch --format='%(refname:short)' | grep hosting-and-release)
make check && git worktree prune
```

## 7. Phase 5 — deploy pipeline

```bash
snodo mode change scaffold
```

```bash
snodo run "$(cat <<'EOF'
Replace the deploy pipeline with the one the records now specify.

INTENT
Hosting moved to Cloudflare Pages served at a custom domain, deployed with
wrangler. The repository still builds and deploys to GitHub Pages. Bring the
pipeline in line with the records. No product behaviour.

CONSTRAINTS
- Implement exactly what the superseding hosting record specifies. Do not
  re-decide the host, the tool, or the domain.
- Remove or repurpose the existing GitHub Pages workflow so the repository has
  one deploy path, not two that disagree.
- Deployment credentials must never be committed. Document which secrets are
  required, where they are set, and what a contributor without them can still
  do.
- The verification command must keep passing with no credentials present, and
  must not require network access or a Cloudflare account. A contributor with
  neither must still be able to build and test.
- Provide a documented way to preview the built site locally.
- Add no runtime dependencies. Build-time and deploy tooling is not a runtime
  dependency, but say so explicitly if you add any, and keep the dependency
  boundary check passing.
- Update the README where it describes hosting or deployment.

ACCEPTANCE
- The verification command passes from a clean checkout with no credentials.
- One deploy path exists, matching the records.
- A local preview command is documented and works.
- Required secrets are documented and absent from the repository.
EOF
)"
```

Manual steps snodo cannot do — create the Pages project, authenticate, attach
the domain:

```bash
npx wrangler login
npx wrangler pages project create <project-name> --production-branch main
make check && npx wrangler pages deploy dist --project-name <project-name>
```

Then attach the custom domain in the Cloudflare dashboard under
**Workers & Pages → your project → Custom domains**, with the domain's DNS
already on Cloudflare. For CI, put `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` in the repository secrets.

## 8. Phase 6 — the remaining features

<!-- TODO: three build tasks, in this order. Each merged before the next. -->

**8.1 — the card editor.** Fields, live preview, no persistence.

**8.2 — the share URL.** Encode the card into a link; the page renders from it
on a cold visit with no storage.

**8.3 — the QR code.** The hardest task in the project: a hand-written encoder,
no library. Scope it tightly — byte mode, one error-correction level, automatic
version sizing — and require a test that **decodes** the output back to the
input rather than asserting the bitmap looks right.

## 9. Key Empirical Findings

### 9.1 Execution vs. Judgement
Three major defects occurred across the runbook iterations:
1. **Broken test command recorded in an ADR**: `node --test tests/` in ADR-0004 passed `decide` and `scaffold-gate` judges. Caught by `quality` on execution.
2. **Toolchain shipped with a failing gate**: `scaffold` toolchain setup passed LLM review; failed on `make check` execution.
3. **Unguarded optional field crashing on minimal card**: `photoProperty` dereferenced `undefined` in `vcard.js`. Passed pre-execute review; caught by `quality` test execution.

> **Finding 1 (corrected)**: Pre-execute judgement of a *proposal* has repeatedly caught real defects; post-execute judgement of *artifacts* has been weak, and execution is what catches those.
>
> The original finding — "every defect was caught by `quality`, the only validator that executes something; read-only judges passed everything" — was true when written and is now too broad. The evidence since:
>
> - **Pre-execute `architecture` has rejected real defects before any code existed.** It rejected a spec whose acceptance criteria were unsatisfiable under the recorded Node floor (Node LTS ≥ 20, §0), citing ADR-0001; it rejected a second spec that asserted the dependency guard was unaffected when the accumulated failure showed that guard rejecting the new import; and it blocked a task because a stored card had to carry a template id while the schema had no such column — the last independently corroborated in `docs/architecture/maturity-assessment-2026-08.md` (§Decision records). These are proposal-stage catches: the judge read the plan and the ADRs and found the plan inconsistent with them, before execution could run.
> - **Post-execute judgement of artifacts has been weak.** The `acceptance` validator's deterministic canary rejects a real omission (Fixes #59) — a missing required test/ADR — proving the judge *can* fire. But in a live run it returned MET for a criterion naming a command it could not run ("make check passes") while `quality`, which had actually executed the command, held its failing output in the same cycle (Fixes #75, now fixed: execution criteria are UNCHECKABLE by construction for a read-only judge, and a read-only verdict that contradicts an execution result is superseded). The standing finding that read-only judges pass everything was qualified, not overturned, by the canary.
> - **Execution is what catches the runbook's defects.** All three defects above were caught by `quality` running tests, not by a judge reading the tree.
>
> *What remains uncertain*: the pre-execute rejections are real but few — the sample is too small to claim pre-execute judgement is reliable in general, only that it is not uniformly permissive. The `acceptance` canary proves the judge can reject a planted omission; it has not been observed rejecting in a live run, and #75 shows the failure mode it still had. Whether post-execute judgement of artifacts is trustworthy in production is open; the evidence says execution is the load-bearing gate.

### 9.2 Recurring Defect Shapes
Across fifty closed issues and runbook logs, defects consistently followed two primary structural patterns:
- **Safety properties degrading to warnings**: e.g., worktree creation failing on empty repos and silently running unisolated (Fixes #2, #29 / ADR 025); truncation proceeding on partial output (Fixes #39); missing read tools evaluating on imagination (Fixes #31).
- **Operational faults reported as policy judgements**: e.g., missing test commands or exit code 127 (`tsc: not found`) being classified as policy rejections, triggering human adjudication and recovery loops (Fixes #27, #33 / ADR 015).

### 9.3 Silent Gates & Verification Hardening
Several gates were found reporting green while enforcing nothing:
- **CI running post-merge**: `.github/workflows/ci.yml` ran on main after merges landed, acting as a post-mortem rather than a gate (Fixes #56 / ADR 025).
- **Self-reported gate results**: Merges were authorized by agent text summaries rather than empirical verification records (Fixes #57).
- **Global coverage drift**: `--cov-fail-under=63` allowed 0%-coverage modules to merge undetected (Fixes #61 / ADR 032).
- **A broken CI workflow shipped silently**: `ci.yml` was invalid YAML for several merges (the patch-coverage step embedded unindented Python in a `run: |` block scalar); every run failed at startup with no log and no test output, and nothing noticed because the merge path ran its gates locally. A local-suite canary now validates every file under `.github/workflows/` (Fixes #74).
- **A stale CI conclusion quoted as current**: the merge gate reported a conclusion without saying when it ran or against which commit, so after a fix landed on main it kept quoting a stale failed run on the branch (Fixes #76). Conclusions now carry run id, commit, and time, and a run on an old commit is reported `stale`.

*Remediation*: The verification hardening suite introduced first-class `verification_executed` audit trail events (ADR 031), patch coverage enforcement over modified lines (ADR 032), mandatory gate canaries proving gates can fail (Fixes #58), out-of-date validator set notices (Fixes #59), a CI-workflow canary (Fixes #74), and a merge gate that polls for a conclusion and distinguishes startup/cancelled/timeout failures (Fixes #72, #76).

---

## 10. Rough Edges & Resolution Status

| Original Friction | Operational Impact | Resolution / Current Status |
|---|---|---|
| `init --template greenfield` raised `KeyError` | Template registration gap | **Fixed** in Issue #19 (auto-registered in CLI) |
| `SNODO_TOKEN_SECRET` warning on every run | Read like a Python runtime crash | **Fixed** in Issue #42 (clean CLI notice) |
| `PolicyAction` msgpack deserialization warning | Deprecation notice in LangGraph | **Fixed** in Issue #43 (registered with msgpack) |
| Worktree creation on empty repo silent fallback | Safety property lost by default | **Fixed** in ADR 025 / Issue #29 (fails loud with `WorktreeError`) |
| Operational faults entering recovery | Config errors caused 4 paid cycles | **Fixed** in ADR 015 / Issue #27 (surfaces as `validator_error`) |
| Recovery synthesising fixes to `.snodo/protocol.yml` | Coder attempted to alter policy | **Fixed** in ADR 026 & ADR 027 (protected surface & halt blocker) |
| Coder output truncation proceeding on partial output | Validator judged incomplete code | **Fixed** in Issue #39 (fails execute step) & Issue #67 (observed vs inferred diagnosis) |
| Worktree isolation vs dependency installation | `npm test` failed with exit 127 | **Resolved**: `test_command` explicitly specifies dependency installation |
| Task sequences not merging on success | Required manual git merges | **Fixed** in ADR 020 (`_merge_on_success` auto-merges on pass) |
| Unverified merges landing on main | Risk of unverified code merging | **Fixed** in ADR 031 (`verification_executed` audit event required) |
| Repeat file reads inflating transcript | Token waste & prompt growth | **Fixed** in ADR 033 (repeat read memory & turn pointers) |
| In-place coder changes not reviewable | Post-execute validators reviewed the previous commit | **Fixed** in ADR 030 (base class owns the commit) |
| Coder capability negotiation by `hasattr` | Silent divergence between adapters | **Fixed** in ADR 035 (declared interface + conformance test) |
| OpenCode path status unclear | Container adapter blinded reviewers for months | **Fixed** in ADR 034 (declared experimental) |
| Operator review outcomes unmeasured | No evidence for unattended-operation claims | **Fixed** in ADR 036 (`snodo task review` / `snodo task report`) |
