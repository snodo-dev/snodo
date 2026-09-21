# ADR 051 — Coder containment is a declared property, not a deployment detail

## Status

Accepted. Does not alter [ADR 014](014-trusted-repository-threat-model.md),
which remains the record of snodo's threat model.

## Context

snodo once carried a subsystem called "sandbox": a package in snodo-core, a
`snodo sandbox` sub-app, a `snodo run --sandbox` flag and a worker image. It
put a container around *snodo itself* and ran the whole task inside. It was
removed, because ADR 014 places sandboxing and containerisation out of scope
under the trusted-repository threat model and rejects naming that implies a
posture the tool does not hold, and because the implementation did not do
what its name claimed: it mounted the workspace read-write, so the process it
contained wrote straight into the operator's checkout; it fell back to local
execution with a warning when Docker was unavailable; and it disabled the
network while injecting provider credentials, a combination under which the
default coder could not reach any provider at all. No protocol in any project
asked for it.

Separately, the `opencode` coder grew the ability to run its server in a
container and was driven end to end against a remote Docker daemon. That work
made a distinction visible that the old subsystem never drew. Where the
container runs is not interesting: an operator who wants the work to happen on
another machine can run snodo on that machine, which snodo already supports
locally and remotely. What is interesting is what the coder can touch. A
container that receives a copy of the task worktree, is written to only by the
coder, and is read back as an archive has a property the host path does not:
the coder's writes are confined to that copy, and the only thing that crosses
back is the difference it made. A bind mount has no such property — it is the
operator's own directory under another name.

Observed on a real project: the read-back path compared archive entries
against worktree entries without accounting for the root directory Docker
names the archive after. Nothing matched, so every file in the task worktree
was judged absent from the container and deleted, including the `.git` that
made it a repository, and the archive was unpacked one level down. The coder's
work was correct and invisible; the task halted `no_file_operations`, a
verdict about the task that was really a fault in the transfer. The same
function is reached with the operator's own working tree when isolation is
off. Containment is therefore not only a property worth having — it is a
property that has to be stated, so that the code implementing it can be held
to it.

## Decision

Containment is declared on the coder definition, as `sandboxed`, defaulting to
false. A protocol that wants its coder contained says so; a protocol that says
nothing gets the behaviour it has today.

`sandboxed: true` means exactly this: the coder is given a copy of the task
workspace, it runs against that copy and not against the operator's tree, the
copy is discarded when the task ends, and the only thing taken from it is the
changed content read back into the task worktree. It does not mean that snodo
defends against a hostile protocol, a hostile repository or a hostile coder,
and it must not be described as though it did. ADR 014 stands: protocol files
and project tooling are trusted input, and nothing here changes that.

How a contained coder is reached is the adapter's business, not the
operator's. The `opencode` coder talks HTTP to a server in the container
because the image serves one; a coder built on a host CLI would run that CLI
inside the container instead. Neither belongs in the model string, in a
protocol, or in a setting. Likewise the daemon's location: `DOCKER_HOST`
already says where it is, and a remote daemon is the same declaration as a
local one.

Containment is meaningful only for a coder that executes against a
filesystem. A coder that is an API client writes nothing except the artifact
snodo itself applies, so there is nothing to contain. An adapter that cannot
honour `sandboxed` says so through the path that already reports a setting a
coder does not read, at the point the run is configured — not by silently
ignoring it, and not by quietly running uncontained.

A coder asked to run contained that cannot be — no daemon, no image, a
workspace that cannot be transferred — halts `environment_error`. That is an
existing halt and the correct one: it is a statement about the run, not a
verdict about the task. No new state, severity, halt type or task status is
introduced by this decision.

## Consequences

Whether a task's coder ran contained becomes a fact about that task, declared
before the run and therefore recordable with it, rather than an inference from
which coder happened to be configured. A protocol that declares it gets a
refusal when the environment cannot provide it, instead of an uncontained run
that looks identical in the log.

The transfer becomes load-bearing rather than incidental. Copying in and
reading back is the mechanism that makes the claim true, so a bind mount is
not an optimisation of it — it is a different thing that does not satisfy the
declaration, and an adapter may not substitute one for the other while
reporting `sandboxed`. Code that deletes from the worktree during read-back
carries the weight of that claim: what cannot be established about the
contained side is a reason to refuse, never a reason to delete.

Coders that cannot be contained stay visibly uncontained. The operator learns
this from the same warning that already tells them which settings a coder
reads, rather than from a task that quietly ran somewhere they did not expect.

## Alternatives

Spelling containment into the model address — `@docker/opencode/...` — was
rejected. It puts a deployment fact into an identifier for a model, gives the
same coder and model two addresses that differ only in how they are run, and
does not generalise: there is no `@docker/litellm` worth having. It also
cannot express a remote daemon, so it would read as "local container" while
being neither.

Treating the axis as an execution *host* — host, local daemon, remote daemon —
was rejected. It describes placement, and placement is not the property being
bought; an operator who wants the work elsewhere runs snodo elsewhere. It also
cannot express ephemerality, which is the part that matters.

Reviving the removed sandbox subsystem was rejected. Its boundary was drawn
around snodo rather than around the coder, which is the wrong object: the
engine, the validators and the audit log are snodo's own and are not what
needs confining.

A global setting rather than a coder property was rejected. Containment is not
uniformly meaningful — it is real for a coder that edits a filesystem and
vacuous for one that returns an artifact over an API — and a setting that
means nothing for half its subjects teaches operators to ignore it.
