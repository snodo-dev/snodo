"""Protocol-versus-code drift: what survey reports for a governed repository.

FILE: snodo/survey/drift.py

A repository without a protocol asks what governing it would mean. A repository
with one asks whether the protocol still describes the code. The analysis is
the same analysis — only the closing question differs — and this module answers
that second question from the survey's observable facts plus the protocol's own
declared fields.

Four comparisons are made, and only where the protocol's shape supports them:

* ``protocol-module-paths`` — a path the protocol governs that no longer exists.
* ``module-coverage`` — a module present in the code that the protocol's
  declared modules do not reach.
* ``decision-records`` — a decision-record directory the protocol names that
  has moved or emptied.
* ``test-commands`` — a test command the protocol assumes that no longer
  resolves against the repository.

Modules are optional (ADR 041), and most protocols declare none. For such a
protocol the three module comparisons are reported as *not made*, with the
reason: a protocol that does not use modules is not drifting from its code by
not naming one, and walking the discovered modules to call every one of them
ungoverned would produce a page of findings that mean nothing. The report says
which shape it is looking at, so a reader knows what was weighed.

The same discipline governs paths elsewhere. Only ``Module.paths``,
``Module.decisions_path`` and the ``test_command`` key of a tooling map are
schema-anchored enough to be resolved against the filesystem. A validator's
``tooling`` map carries arbitrary values — model identifiers such as
``openai/gpt-4o-mini``, command fragments, settings — so a value is never
treated as a path because it happens to contain a slash. A test command is
read the other way round: it is never asked which of its tokens are paths
(that is how a flag becomes a finding), only whether it names a runner from a
closed vocabulary and whether the repository's own marker files confirm that
runner. A command naming no runner from that vocabulary is reported as not
compared. Where a comparison cannot be made honestly it is not made, and the
omission is in the report.

Nothing here writes to the repository, executes anything in it, or proposes a
change to it: drift is reported to a person who decides. The protocol may be
what moved and the code may be right, and a finding is phrased so that reading
it does not require having already settled which side is at fault. Nothing
here depends on the workstation either: a command is compared against what the
repository declares, never against the PATH of the machine running survey, so
the same repository gives the same report on any machine.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from snodo.survey.analyzer import (
    _TEST_MARKERS,
    _has_glob_magic,
    _iter_own_source_dirs,
    _normalize_module_path,
)
from snodo.survey.models import (
    ComparisonNotMade,
    DriftAgreement,
    DriftFinding,
    ProtocolDrift,
    RepositorySurvey,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; survey stays import-light
    from snodo.compiler.models import Protocol

CHECK_MODULE_PATHS = "protocol-module-paths"
CHECK_MODULE_COVERAGE = "module-coverage"
CHECK_DECISION_RECORDS = "decision-records"
CHECK_TEST_COMMANDS = "test-commands"
CHECK_TOOLING_VALUES = "validator-tooling"

# A closed vocabulary: a token in a protocol's test command that names a
# runner, mapped to the command the survey's own marker machinery emits for
# that runner. Nothing outside this table is inferred to be anything at all —
# a token that is not listed here is simply not consulted.
_RUNNER_VOCABULARY: Dict[str, str] = {
    "pytest": "pytest",
    "py.test": "pytest",
    "nosetests": "pytest",
    "npm": "npm test",
    "pnpm": "npm test",
    "yarn": "npm test",
    "cargo": "cargo test",
    "go": "go test ./...",
    "make": "make test",
}

# tooling.test_command values that mean "no runner configured here" rather than
# naming one: the legacy template placeholder, and the shipped no-op default
# every template falls back to. A test asserts that this predicate agrees with
# the one in snodo.validators.quality, which is what executes them: the two
# must not quietly disagree about what "not configured" means.
_NO_RUNNER_PLACEHOLDERS = {"", "REPLACE_ME"}


def _asserts_no_runner(command: str) -> bool:
    """True when a test_command value claims nothing about the repository.

    Such a value defers to auto-detection at run time, so comparing it to the
    code would report a divergence the protocol never asserted.
    """
    stripped = command.strip()
    return (
        stripped.upper() in _NO_RUNNER_PLACEHOLDERS
        or stripped.startswith("echo ")
    )


# ---------------------------------------------------------------------------
# Declared paths: what the schema says is a path, and where it points
# ---------------------------------------------------------------------------


def _declared_path_root(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a declared path to the repository location it denotes.

    Returns ``(posix_root, None)`` when the declaration names a location the
    filesystem can be asked about, or ``(None, reason)`` when it does not —
    a pattern with no literal root, a location outside the repository, or an
    empty value. A pattern like ``services/api/**`` (ADR 041's shape) resolves
    to its literal root ``services/api``: that is the directory whose existence
    the declaration asserts.
    """
    value = _normalize_module_path(str(raw or ""))
    if not value:
        return None, "the declaration is empty"
    pure = Path(value.replace("\\", "/"))
    if pure.is_absolute():
        return None, f"'{raw}' is an absolute path, outside the repository"
    segments = value.split("/")
    if any(segment == ".." for segment in segments):
        return None, f"'{raw}' escapes the repository root"
    literal: List[str] = []
    for segment in segments:
        if _has_glob_magic(segment):
            break
        literal.append(segment)
    if not literal:
        return None, f"'{raw}' is a pattern with no literal root"
    return "/".join(literal), None


def _modules_word(owners: List[str]) -> str:
    return "modules" if len(owners) > 1 else "module"


def _subject(owners: List[str], verb: str) -> str:
    """`modules api, web govern` / `module api governs`."""
    plural = len(owners) > 1
    return (
        f"{_modules_word(owners)} {', '.join(owners)} "
        f"{verb if plural else verb + 's'}"
    )


def _decision_claim(owners: List[str], verb: str, root: str) -> str:
    """`modules api, web point their decision records at 'docs/adr'`."""
    possessive = "their" if len(owners) > 1 else "its"
    return f"{_subject(owners, verb)} {possessive} decision records at '{root}'"


def _covers(declared: str, discovered: str) -> bool:
    """True when a declared module root speaks to a discovered boundary.

    Either direction counts: the discovered path inside the declared region, or
    the declared region inside the discovered boundary. Both mean the protocol
    has said something about this part of the repository, which is what a
    coverage question turns on.
    """
    return (
        declared == discovered
        or discovered.startswith(declared + "/")
        or declared.startswith(discovered + "/")
    )


# ---------------------------------------------------------------------------
# Test commands: the one tooling key whose tokens may be read at all
# ---------------------------------------------------------------------------


def _present_roots(project_root: Path, declared_paths: List[str]) -> List[str]:
    """The declared paths that actually exist, as locations to resolve in."""
    present = []
    for raw in declared_paths:
        root, _why = _declared_path_root(raw)
        if root is not None and (project_root / root).exists():
            present.append(root)
    return present


def _tooling_commands(tooling: Dict[str, object]) -> List[str]:
    """Every ``test_command`` value in a tooling map, and nothing else.

    Only that key is read. Every other entry — a model identifier such as
    ``openai/gpt-4o-mini``, a timeout, a settings blob — carries no schema
    promise about what it denotes, so no slash inside one is a path.
    """
    value = tooling.get("test_command")
    return [value] if isinstance(value, str) else []


def _runners_named(command: str) -> Set[str]:
    """The runners a test command names, from the closed vocabulary above.

    Each token is compared to the vocabulary by basename only, so ``uv run
    pytest`` is read as pytest and ``--cov=src/app`` is read as nothing. An
    empty result means survey has no honest opinion about the command.
    """
    named: Set[str] = set()
    for token in command.replace("\n", " ").split():
        if token.startswith("-"):
            continue
        name = token.rsplit("/", 1)[-1].lower()
        if name in _RUNNER_VOCABULARY:
            named.add(_RUNNER_VOCABULARY[name])
    return named


def _confirmed_markers(project_root: Path) -> Dict[str, Set[str]]:
    """Repository directory → the test commands its own marker files confirm.

    Built with the analyzer's confirmation rules — a marker file must actually
    declare the runner, not merely exist — so the drift report and the survey
    report cannot disagree about what the repository declares. A Makefile
    belongs to the repository root, as ``_detect_test_command`` treats it.
    """
    found: Dict[str, Set[str]] = {}
    for dirpath, _dirnames, filenames in _iter_own_source_dirs(project_root):
        rel = dirpath.relative_to(project_root).as_posix()
        at_root = rel == "."
        commands: Set[str] = set()
        for marker, confirm in _TEST_MARKERS:
            if marker not in filenames:
                continue
            if marker == "Makefile" and not at_root:
                continue
            confirmed = confirm(dirpath / marker)
            if confirmed:
                commands.add(confirmed)
        if commands:
            found[rel] = commands
    return found


def _scope_entries(
    markers: Dict[str, Set[str]], roots: Optional[List[str]]
) -> Dict[str, Set[str]]:
    """The confirmed markers a scope sees: its own directories and the root.

    The root is always in view: for a module, a runner declared at the
    repository root governs it, and reporting its absence from the module's own
    subtree would be a finding about layout rather than about drift. ``roots``
    None means the whole repository.
    """
    if roots is None:
        return markers
    normalized = [root for root in (_normalize_module_path(r) for r in roots) if root]
    visible: Dict[str, Set[str]] = {}
    for rel, commands in markers.items():
        if rel == "." or any(_covers(root, rel) for root in normalized):
            visible[rel] = commands
    return visible


# ---------------------------------------------------------------------------
# The comparisons
# ---------------------------------------------------------------------------


def _collect_protocol_paths(protocol: "Protocol") -> Tuple[Dict[str, Set[str]], List[str]]:
    """Declared module roots by location, plus the omissions found on the way."""
    roots: Dict[str, Set[str]] = {}
    skipped: List[str] = []
    for module in protocol.modules:
        for raw in module.paths:
            root, why = _declared_path_root(raw)
            if root is None:
                skipped.append(f"module '{module.module_id}' path '{raw}': {why}")
                continue
            roots.setdefault(root, set()).add(module.module_id)
    return roots, skipped


def _compare_module_paths(
    project_root: Path,
    protocol: "Protocol",
    findings: List[DriftFinding],
    agreements: List[DriftAgreement],
    not_made: List[ComparisonNotMade],
) -> Dict[str, Set[str]]:
    """A path the protocol governs whose location no longer exists."""
    roots, skipped = _collect_protocol_paths(protocol)
    present: List[str] = []
    for root in sorted(roots):
        owners = sorted(roots[root])
        if (project_root / root).exists():
            present.append(f"{root} (governing {', '.join(owners)})")
        else:
            findings.append(
                DriftFinding(
                    check=CHECK_MODULE_PATHS,
                    subject=root,
                    claim=f"{_subject(owners, 'govern')} '{root}'",
                    observation=f"nothing exists at '{root}' in the repository",
                    evidence=[f"declared by: {', '.join(owners)}"],
                )
            )
    if present:
        agreements.append(
            DriftAgreement(
                check=CHECK_MODULE_PATHS,
                statement=(
                    f"{len(present)} of {len(roots)} declared module path(s) "
                    "still name a location in the repository."
                ),
                evidence=present,
            )
        )
    if skipped:
        not_made.append(
            ComparisonNotMade(
                check=CHECK_MODULE_PATHS,
                reason=(
                    "these declarations name no location a filesystem can be "
                    "asked about: " + "; ".join(skipped)
                ),
            )
        )
    return roots


def _compare_module_coverage(
    survey: RepositorySurvey,
    roots: Dict[str, Set[str]],
    findings: List[DriftFinding],
    agreements: List[DriftAgreement],
) -> None:
    """A module the code shows that the protocol's modules do not reach.

    Called only for a protocol that declares modules: for one that does not,
    coverage is not a comparison it makes (see ``compare_protocol``).
    """
    discovered: Dict[str, Set[str]] = {}
    origins: Dict[str, str] = {}
    for module in survey.modules:
        for path in module.paths:
            normalized = _normalize_module_path(path)
            if normalized and normalized != ".":
                discovered.setdefault(normalized, set()).add(module.module_id)
                origins.setdefault(normalized, module.origin)

    covered: List[str] = []
    for path in sorted(discovered):
        owner = next(
            (declared for declared in sorted(roots) if _covers(declared, path)), None
        )
        if owner is None:
            findings.append(
                DriftFinding(
                    check=CHECK_MODULE_COVERAGE,
                    subject=path,
                    claim=(
                        f"the protocol scopes governance to "
                        f"{len(roots)} declared module path(s)"
                    ),
                    observation=(
                        f"the boundary the code shows here "
                        f"({'/'.join(sorted(discovered[path]))}) is inside "
                        "none of them"
                    ),
                    evidence=[f"discovered by survey from the code ({origins[path]})"],
                )
            )
        else:
            covered.append(f"{path} → {owner}")
    if covered:
        unreached = len(discovered) - len(covered)
        agreements.append(
            DriftAgreement(
                check=CHECK_MODULE_COVERAGE,
                statement=(
                    f"{len(covered)} of {len(discovered)} module boundaries the "
                    "code shows "
                    f"{'is' if len(discovered) == 1 else 'are'} reached by a "
                    + ("declared module." if not unreached else f"declared module; "
                       f"{unreached} {'is' if unreached == 1 else 'are'} not.")
                ),
                evidence=covered,
            )
        )


def _has_decision_records(dirpath: Path) -> bool:
    """Whether a directory holds decision records at all."""
    try:
        return next(dirpath.rglob("*.md"), None) is not None
    except OSError:
        return False


def _compare_decision_records(
    project_root: Path,
    protocol: "Protocol",
    findings: List[DriftFinding],
    agreements: List[DriftAgreement],
    not_made: List[ComparisonNotMade],
) -> None:
    """A decision-record location the protocol names that moved or emptied."""
    named: Dict[str, Set[str]] = {}
    unresolvable: List[str] = []
    for module in protocol.modules:
        declared = module.decisions_path
        if not declared:
            continue
        root, why = _declared_path_root(declared)
        if root is None:
            unresolvable.append(f"module '{module.module_id}': {why}")
            continue
        named.setdefault(root, set()).add(module.module_id)

    if unresolvable:
        not_made.append(
            ComparisonNotMade(
                check=CHECK_DECISION_RECORDS,
                reason=(
                    "these declarations name no directory a filesystem can be "
                    "asked about: " + "; ".join(unresolvable)
                ),
            )
        )

    if not named:
        not_made.append(
            ComparisonNotMade(
                check=CHECK_DECISION_RECORDS,
                reason=(
                    "no module declares a decisions_path, and the protocol has "
                    "no protocol-level decision-record field: prose that "
                    "describes where records live is not a path the survey can "
                    "resolve, so this protocol's decision-record location was "
                    "not compared"
                ),
            )
        )
        return

    settled: List[str] = []
    for root in sorted(named):
        owners = sorted(named[root])
        dirpath = project_root / root
        subject = f"{root} ({', '.join(owners)})"
        if not dirpath.is_dir():
            findings.append(
                DriftFinding(
                    check=CHECK_DECISION_RECORDS,
                    subject=root,
                    claim=_decision_claim(owners, "point", root),
                    observation=f"'{root}' is not a directory in the repository",
                    evidence=[f"declared by: {', '.join(owners)}"],
                )
            )
        elif not _has_decision_records(dirpath):
            findings.append(
                DriftFinding(
                    check=CHECK_DECISION_RECORDS,
                    subject=root,
                    claim=_decision_claim(owners, "hold", root),
                    observation=f"'{root}' exists but holds no markdown files",
                    evidence=[f"declared by: {', '.join(owners)}"],
                )
            )
        else:
            settled.append(subject)
    if settled:
        agreements.append(
            DriftAgreement(
                check=CHECK_DECISION_RECORDS,
                statement=(
                    f"{len(settled)} declared decision-record location(s) exist "
                    "and hold markdown files."
                ),
                evidence=settled,
            )
        )


def _compare_test_commands(
    project_root: Path,
    protocol: "Protocol",
    findings: List[DriftFinding],
    agreements: List[DriftAgreement],
    not_made: List[ComparisonNotMade],
) -> None:
    """A test command the protocol assumes that no longer resolves against the repository.

    "Resolves" here means: the repository's own marker files confirm the runner
    the command names. It is not a claim that the command succeeds — that would
    mean executing it, which survey never does and which is ``snodo validate``'s
    and ``snodo ready``'s business — and it is not a claim about the runner
    binary on the workstation, which would make the report a fact about the
    machine running survey rather than about this repository.
    """
    assumed: Dict[str, List[str]] = {}
    scopes: Dict[str, Optional[List[str]]] = {}

    def add(command: str, owner: str, roots: Optional[List[str]]) -> None:
        """Record who assumes a command, widening its scope to the whole repository.

        A command a validator assumes is the protocol's claim about the
        repository; a module's claim is about the module. When both name the
        same command, either scope satisfies the claim, so the wider question is
        the honest one to ask.
        """
        key = command.strip()
        assumed.setdefault(key, []).append(owner)
        if key not in scopes or roots is None:
            scopes[key] = None
        else:
            scopes[key] = list(scopes[key] or []) + list(roots)

    for validator in protocol.validators:
        for command in _tooling_commands(validator.tooling):
            add(command, f"validator '{validator.validator_id}'", None)
    for module in protocol.modules:
        for command in _tooling_commands(module.tooling):
            existing = _present_roots(project_root, module.paths)
            if not existing:
                # The module's paths are the divergence; a runner inside a
                # directory that is not there is not a second finding.
                not_made.append(
                    ComparisonNotMade(
                        check=CHECK_TEST_COMMANDS,
                        reason=(
                            f"module '{module.module_id}' assumes '{command}', but "
                            "none of the paths it owns is in the repository, so the "
                            "command had no scope to resolve in — the missing paths "
                            "are the reported divergence"
                        ),
                    )
                )
                continue
            add(command, f"module '{module.module_id}'", existing)

    if not assumed:
        not_made.append(
            ComparisonNotMade(
                check=CHECK_TEST_COMMANDS,
                reason=(
                    "no validator or module declares a tooling.test_command, so "
                    "there is no test command of the protocol's to resolve "
                    "against the repository"
                ),
            )
        )
        return

    markers = _confirmed_markers(project_root)
    resolving: List[str] = []
    for command in sorted(assumed):
        owners = sorted(set(assumed[command]))
        attribution = " and ".join(owners)
        assumes = "assume" if len(owners) > 1 else "assumes"
        if _asserts_no_runner(command):
            not_made.append(
                ComparisonNotMade(
                    check=CHECK_TEST_COMMANDS,
                    reason=(
                        f"{attribution} carry the placeholder for an unconfigured "
                        "test command, which asserts nothing about the repository"
                    ),
                )
            )
            continue

        wanted = _runners_named(command)
        if not wanted:
            not_made.append(
                ComparisonNotMade(
                    check=CHECK_TEST_COMMANDS,
                    reason=(
                        f"{attribution} assume the command '{command}', which names "
                        "no runner survey recognises; reading its other tokens as "
                        "paths would invent the finding"
                    ),
                )
            )
            continue

        visible = _scope_entries(markers, scopes[command])
        hits = sorted(
            f"{directory}: {', '.join(sorted(commands & wanted))}"
            for directory, commands in visible.items()
            if commands & wanted
        )
        if hits:
            resolving.append(f"{command} ({attribution})")
            continue

        findings.append(
            DriftFinding(
                check=CHECK_TEST_COMMANDS,
                subject=command,
                claim=f"{attribution} {assumes} the test command '{command}'",
                observation=(
                    "no marker file in the repository declares a runner for it"
                ),
                evidence=[
                    "runners the repository does declare here: "
                    + (
                        ", ".join(
                            sorted({c for group in visible.values() for c in group})
                        )
                        or "none"
                    ),
                    "the command's other tokens were not read as paths",
                ],
            )
        )

    if resolving:
        agreements.append(
            DriftAgreement(
                check=CHECK_TEST_COMMANDS,
                statement=(
                    f"{len(resolving)} assumed test command(s) still resolve "
                    "against a runner the repository declares."
                ),
                evidence=resolving,
            )
        )


def _report_tooling_values(
    protocol: "Protocol", not_made: List[ComparisonNotMade]
) -> None:
    """Say plainly which tooling values were not read, and why.

    ``tooling`` is a map to arbitrary values on both a validator and a module.
    Only ``test_command`` has a schema-anchored meaning, so every other key is
    reported as untouched rather than guessed at: a value such as
    ``openai/gpt-4o-mini`` contains a slash and denotes no path, and a check
    that resolved it as one would be a confident false finding.
    """
    keys: Set[str] = set()
    for validator in protocol.validators:
        keys.update(key for key in validator.tooling if key != "test_command")
    for module in protocol.modules:
        keys.update(key for key in module.tooling if key != "test_command")
    if keys:
        not_made.append(
            ComparisonNotMade(
                check=CHECK_TOOLING_VALUES,
                reason=(
                    "tooling keys other than test_command carry no schema promise "
                    "about what they denote, so none of "
                    + ", ".join(sorted(f"'{key}'" for key in keys))
                    + " was read as a path"
                ),
            )
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------



def _shape(protocol: "Protocol") -> str:
    if protocol.modules:
        return (
            f"{len(protocol.modules)} declared module(s): "
            + ", ".join(module.module_id for module in protocol.modules)
        )
    return (
        "no declared modules: the protocol governs the repository as a whole, "
        "not named regions of it"
    )


def compare_protocol(
    project_root: Path,
    survey: RepositorySurvey,
    protocol: "Protocol",
) -> ProtocolDrift:
    """Report where a protocol's claims and the surveyed code agree or diverge.

    Read alongside the survey itself: this is not a second analysis but the
    closing question a governed repository can be asked, answered from the
    modules and tooling the protocol declares and the boundaries and commands
    the code shows.
    """
    findings: List[DriftFinding] = []
    agreements: List[DriftAgreement] = []
    not_made: List[ComparisonNotMade] = []
    uses_modules = bool(protocol.modules)

    if uses_modules:
        roots = _compare_module_paths(project_root, protocol, findings, agreements, not_made)
        _compare_module_coverage(survey, roots, findings, agreements)
        _compare_decision_records(project_root, protocol, findings, agreements, not_made)
    else:
        not_made.extend(
            [
                ComparisonNotMade(
                    check=CHECK_MODULE_PATHS,
                    reason=(
                        "the protocol declares no modules, so it governs no "
                        "named path whose existence could have been checked"
                    ),
                ),
                ComparisonNotMade(
                    check=CHECK_MODULE_COVERAGE,
                    reason=(
                        "the protocol declares no modules: module coverage is a "
                        "comparison a module-scoped protocol makes, and calling "
                        "every discovered boundary unmanaged because the "
                        "protocol names no modules would report noise as fact"
                    ),
                ),
                ComparisonNotMade(
                    check=CHECK_DECISION_RECORDS,
                    reason=(
                        "a decisions_path is a field on a module, and this "
                        "protocol declares none; decision records found in the "
                        "code are listed under Decision Records above"
                    ),
                ),
            ]
        )

    _compare_test_commands(project_root, protocol, findings, agreements, not_made)
    _report_tooling_values(protocol, not_made)

    if findings:
        summary = (
            f"The protocol and the code diverge on {len(findings)} point"
            f"{'s' if len(findings) > 1 else ''}; "
            f"{len(agreements)} comparison"
            f"{'s' if len(agreements) != 1 else ''} "
            f"{'agree' if len(agreements) != 1 else 'agrees'}."
        )
    elif agreements:
        summary = (
            "Nothing the survey could compare has diverged: "
            f"{len(agreements)} comparison"
            f"{'s' if len(agreements) != 1 else ''} "
            f"{'agree' if len(agreements) != 1 else 'agrees'}."
        )
    else:
        summary = (
            "The protocol's shape supported none of the comparisons, so this "
            "report says nothing about whether the protocol still fits."
        )

    return ProtocolDrift(
        protocol_id=protocol.protocol_id,
        uses_modules=uses_modules,
        shape=_shape(protocol),
        summary=summary,
        checks_made=len(agreements) + len(findings),
        agreements=agreements,
        divergences=findings,
        not_compared=not_made,
    )
