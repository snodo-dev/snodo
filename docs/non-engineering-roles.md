# Templates for product, design, docs, and research

Snodo includes templates for work that produces written deliverables. Each role
asks the coder to create reviewable Markdown files in the repository, then
checks the work with role-specific LLM validators, `acceptance`, and `protocol`.
The `quality` validator runs a project command; its shipped default records that
no tests were executed. Each template defaults to `change_request` delivery so
a person can review the proposed documents before they are merged.

Start with `snodo init --template <name>`. Give the task a clear deliverable path
and acceptance criteria so both the coder and the validators know what to check.

## Product

The coder writes product documents as Markdown under `docs/product/`. Work may
include problem statements, evidence, measurable outcomes, scope and non-goals,
user stories, acceptance criteria, or user-facing release notes.

The `product-quality` conventions validator checks that the problem and its
evidence are clear, outcomes are measurable, scope is explicit, user stories
follow INVEST and have testable acceptance criteria, and the document describes
user needs without prescribing a technical solution. It also checks release
notes for clear user-visible changes. `acceptance` compares the artifacts with
the task's acceptance criteria, while `protocol` checks that the request is
product documentation rather than implementation or design work.

## Design

The coder produces Markdown design artifacts, such as a design brief, UX
specification, UI copy, or component specification, under `docs/design/`.

The `design_quality` conventions validator looks for a clear user goal and
context, applicable empty/loading/error/success states, accessibility notes
including WCAG-related notes, contrast, and keyboard use, consistency with the
repository's design system and terminology, and plain, kind, concise UI copy.
`acceptance` checks the task criteria. The `protocol` validator confirms the
requested work is a design deliverable, names a Markdown artifact in
`docs/design/`, and can be reviewed before merge.

## Documentation

The coder writes Markdown in the repository's existing documentation tree,
following the task's specified page or section.

The `docs_quality` conventions validator checks factual accuracy against
repository behavior, task-oriented guidance, runnable examples, and working
links. `acceptance` checks the task criteria. The `protocol` validator confirms
the request is technical documentation, identifies a file in the existing docs
tree, and leaves the change request open for human review.

## Research

The coder writes evidence-led research reports or decision memos as Markdown
under `docs/research/`.

The `research_quality` LLM validator checks that factual claims have verifiable
sources, facts are distinguished from analysis and assumptions, relevant
counter-evidence and limitations are considered, and recommendations follow
from the evidence while accounting for uncertainty. `acceptance` checks the
task criteria, and `protocol_adherence` checks that the request fits research
or decision-memo work and places the Markdown artifact under `docs/research/`.

## Configure the documentation quality check

Each template's `quality` validator is deliberately harmless in a new project:
it reports that no tests were executed. Replace its `tooling.test_command` in
`.snodo/protocol.yml` with documentation tools already installed in your
project. For example, a project with MkDocs can use:

```yaml
validators:
  - validator_id: quality
    validator_type: quality
    evaluation_phase: post_execute
    tooling:
      test_command: "mkdocs build --strict"
```

Choose a command that matches the project's tooling and deliverable paths. A
Markdown lint and link checker can be used where those tools are available. The
quality result reports that command's outcome; the role-specific validator
continues to review the document content.

## Human review and sign-off

With the default `change_request` delivery, Snodo pushes the verified task
branch and opens a change request through the configured code-host provider.
The request contains the Markdown diff for review. A human reads the documents
and validator feedback, asks for changes or approves the request, and merges it
according to the repository's review practice. The default keeps sign-off with
the person responsible for the work; it does not merge the documents
automatically.

Change-request delivery needs the appropriate provider plugin and credentials.
See [running Snodo in CI](running-in-ci.md) and the
[protocol reference](protocol.md#execution-configuration) for delivery details.
