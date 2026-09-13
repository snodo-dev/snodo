#!/usr/bin/env python
"""Enforce the documentation-coverage ratchet over every invokable surface.

Why: documentation goes stale silently, and silence reads as authority. Three
surfaces are what a user can actually invoke — the CLI command tree, the MCP
tool registry, and the configuration keys ``snodo config set`` will accept.
None of them were mechanically tied to docs/, so a command could ship with no
page mentioning it and a page could keep describing a command that no longer
exists. Both directions are now checked:

* **Coverage** — every visible leaf command (``snodo job archive``), every MCP
  tool (``run_plan``), and every settable config key (``llm.coder.model``)
  must be mentioned somewhere under docs/. A mention of a command is a
  ``snodo <path>`` sequence; tools and keys by their exact token. *Which*
  page carries the mention is an editorial decision this check deliberately
  does not make.

* **Dangling** — every ``snodo <path>`` sequence, every ``--flag`` after a
  resolved command, and every dotted ``llm.*`` / ``engine.*`` config key in
  the documentation must resolve against the live surface. This catches the
  prose that outlived the code: renamed commands, removed flags, retired
  keys.

What neither direction catches, and does not pretend to: prose that is wrong
rather than absent or unresolvable. A page that says four halt outcomes when
there are five passes both checks. Reducing that surface is the point;
claiming to have solved it would be worse than not checking at all. For the
same reason tool names are not dangling-checked — a bare token like
``read_file`` in prose has no unambiguous "this is a tool reference" sigil,
and inventing one the docs do not use would dictate format. Removed tools
surface through the human review this check exists to narrow.

The ratchet, not the cliff: gaps that exist today are listed in
scripts/docs_coverage_baseline.txt. That file is a debt list that can only be
paid down — a baselined gap may be filled but a new gap may never join it (a
new surface that ships undocumented fails by existing), a filled gap leaves
the list for good (re-undocumenting then fails), and --update-baseline only
ever shrinks it. There is deliberately no suppression comment: an escape
hatch turns a ratchet into a formality.

Scope of the corpus: every ``docs/**/*.md`` except ``docs/decisions/`` (a
decision record describes a moment, not current behaviour) and
``docs/specs/`` (historical design documents, excluded from the published
site by mkdocs). CHANGELOG.md is out for the same reason as the ADRs.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

BASELINE_RELATIVE_PATH = Path("scripts") / "docs_coverage_baseline.txt"
DOCS_DIR_NAME = "docs"
EXCLUDED_DOC_DIRS = {"decisions", "specs"}

# Source-of-truth files for each surface, relative to the repo root.
CLI_ENTRY_MODULE = "snodo.cli.main"
MCP_TOOLS_FILE = Path("packages") / "snodo-mcp" / "src" / "snodo" / "mcp" / "tools.py"
CONFIG_CMD_FILE = Path("snodo") / "cli" / "commands" / "config_cmd.py"
CORE_CONFIG_FILE = (
    Path("packages") / "snodo-core" / "src" / "snodo" / "config.py"
)

# A dotted token is treated as a config-key reference only with one of these
# prefixes, so schema ids (snodo.validate.v1) and paths are never captured.
CONFIG_KEY_PREFIXES = ("llm.", "engine.")

KINDS = ("command", "tool", "config")


class Surface:
    """Everything a user can invoke, as collected from the live code."""

    def __init__(self) -> None:
        # path tuple -> {"opts": frozenset[str], "hidden": bool, "group": bool}
        # for every node of the CLI tree (hidden nodes resolve but are not
        # required to be documented); dotted names -> option names.
        self.commands: dict[tuple[str, ...], dict] = {}
        self.tools: set[str] = set()
        self.config_keys: set[str] = set()
        self.errors: list[str] = []

    def documented_surface(self) -> dict[str, str]:
        """Items that must appear in docs: item-id -> human description."""
        items: dict[str, str] = {}
        for path, info in self.commands.items():
            if not info["group"] and not info["hidden"]:
                items[f"command {' '.join(path)}"] = (
                    f"command `snodo {' '.join(path)}`"
                )
        for tool in self.tools:
            items[f"tool {tool}"] = f"MCP tool `{tool}`"
        for key in self.config_keys:
            items[f"config {key}"] = f"config key `{key}`"
        return items


# ---------------------------------------------------------------------------
# Surface collection (live code)
# ---------------------------------------------------------------------------

def collect_cli_commands(surface: Surface, repo_root: Path) -> None:
    """Walk the real Typer command tree from snodo.cli.main.

    Dynamic on purpose: the CLI mounts sub-apps by auto-discovery and derives
    names from function names, so a static read of snodo/cli/commands/ would
    have to re-implement Typer to know what a user can actually invoke.
    """
    import importlib

    try:
        module = importlib.import_module(CLI_ENTRY_MODULE)
        import typer.main

        root = typer.main.get_command(module.app)
    except Exception as exc:  # noqa: BLE001 - any import failure is fatal here
        surface.errors.append(
            f"cannot load the CLI surface from {CLI_ENTRY_MODULE}: {exc}. "
            "Install the project (uv sync) or fix the import error."
        )
        return

    def opts_of(cmd) -> frozenset[str]:
        return frozenset(
            opt
            for param in getattr(cmd, "params", [])
            for opt in getattr(param, "opts", [])
            if opt.startswith("--")
        )

    root_opts = opts_of(root) | {"--help"}
    surface.commands[()] = {
        "opts": root_opts,
        "hidden": False,
        "group": True,
    }

    def walk(cmd, path: tuple[str, ...]) -> None:
        children = getattr(cmd, "commands", None)
        if children:
            surface.commands[path] = {
                "opts": opts_of(cmd) | {"--help"},
                "hidden": bool(getattr(cmd, "hidden", False)),
                "group": True,
            }
            for name in children:
                walk(children[name], path + (name,))
        else:
            surface.commands[path] = {
                "opts": opts_of(cmd) | {"--help"},
                "hidden": bool(getattr(cmd, "hidden", False)),
                "group": False,
            }

    for name, child in (getattr(root, "commands", None) or {}).items():
        walk(child, (name,))


def _assigned_value(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    return None


def collect_mcp_tools(surface: Surface, repo_root: Path) -> None:
    collect_mcp_tools_from(surface, repo_root / MCP_TOOLS_FILE)


def collect_mcp_tools_from(surface: Surface, path: Path) -> None:
    """Tool names = keys of TOOL_REGISTRY (a literal dict; read statically)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        surface.errors.append(f"cannot read the MCP tool registry at {path}: {exc}")
        return
    value = _assigned_value(tree, "TOOL_REGISTRY")
    if not isinstance(value, ast.Dict):
        surface.errors.append(
            f"TOOL_REGISTRY not found as a dict literal in {path} — the MCP "
            "surface moved; update scripts/enforce_docs_coverage.py to match."
        )
        return
    surface.tools = {
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _string_keys(node: ast.expr | None) -> set[str]:
    """Keys of an ast.Dict with string-literal keys."""
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def collect_config_keys(surface: Surface, repo_root: Path) -> None:
    collect_config_keys_from(
        surface, repo_root / CONFIG_CMD_FILE, repo_root / CORE_CONFIG_FILE
    )


def collect_config_keys_from(
    surface: Surface, config_cmd: Path, core_config: Path
) -> None:
    """Keys ``snodo config set`` will accept, collected from two sources:

    ``llm.*`` from ``_LLM_SETTABLE_KEYS`` in config_cmd.py, ``model`` from the
    ``key == "<lit>"`` comparisons in ``_config_set``, and ``engine.*`` from
    the validated engine-key tuples in ``_config_set`` unioned with the
    engine defaults dict in snodo-core's ConfigManager.
    """
    try:
        cmd_tree = ast.parse(config_cmd.read_text(encoding="utf-8"))
        core_tree = ast.parse(core_config.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        surface.errors.append(f"cannot read the config surface: {exc}")
        return

    llm_keys = _string_keys(_assigned_value(cmd_tree, "_LLM_SETTABLE_KEYS"))
    if not llm_keys:
        surface.errors.append(
            f"_LLM_SETTABLE_KEYS not found in {config_cmd} — the llm.* settable "
            "surface moved; update scripts/enforce_docs_coverage.py to match."
        )
    surface.config_keys |= {f"llm.{key}" for key in llm_keys}

    engine_keys: set[str] = set()
    for node in ast.walk(cmd_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_config_set":
            for cmp in ast.walk(node):
                if not isinstance(cmp, ast.Compare) or len(cmp.ops) != 1:
                    continue
                left, op, right = cmp.left, cmp.ops[0], cmp.comparators[0]
                lit = (
                    right.value
                    if isinstance(right, ast.Constant) and isinstance(right.value, str)
                    else None
                )
                if isinstance(op, ast.Eq) and isinstance(left, ast.Name):
                    if left.id == "key" and lit is not None:
                        surface.config_keys.add(lit)
                elif isinstance(op, ast.In) and isinstance(left, ast.Name):
                    if left.id == "engine_key":
                        engine_keys |= _string_keys_tuple(right)
    for node in ast.walk(core_tree):
        # The engine defaults, in either written form: a literal
        # ``{"engine": {...}}`` entry, or a ``setdefault("engine", {...})``
        # call — both appear in ConfigManager's bootstrap code.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "engine"
                    and isinstance(value, ast.Dict)
                ):
                    engine_keys |= _string_keys(value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"setdefault", "update"}
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "engine"
        ):
            engine_keys |= _string_keys(node.args[1])
    surface.config_keys |= {f"engine.{key}" for key in engine_keys}


def _string_keys_tuple(node: ast.expr) -> set[str]:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {
            el.value
            for el in node.elts
            if isinstance(el, ast.Constant) and isinstance(el.value, str)
        }
    return set()


# ---------------------------------------------------------------------------
# Documentation scan
# ---------------------------------------------------------------------------

def iter_doc_files(repo_root: Path) -> list[Path]:
    """Every markdown page in the documentation corpus (see module docstring)."""
    docs = repo_root / DOCS_DIR_NAME
    files: list[Path] = []
    for path in sorted(docs.rglob("*.md")):
        rel_parts = path.relative_to(docs).parts
        if rel_parts and rel_parts[0] in EXCLUDED_DOC_DIRS:
            continue
        files.append(path)
    return files


_WORD = re.compile(r"(?:--)?[A-Za-z0-9][A-Za-z0-9_.|<>/{}-]*")
# Angle and curly brackets are NOT trimmed: they are what marks a
# metavariable ("snodo <command> --help", "snodo run <task_id>"), and a
# stripped "<command>" is indistinguishable from a real subcommand.
# Keeping the brackets lets _is_command_word reject the placeholder.
_TRIM = "`'\"[](),;:!?.= "
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_INLINE_CODE = re.compile(r"`+([^`\n]+)`+")
_FENCE_COMMENT = re.compile(r"(?:^|\s)#.*$")
# A "snodo" token is not an invocation when glued to these characters on
# either side: path/dotted-name/word fragments before (".snodo", "snodo.cli"),
# possessives, log prefixes and assignments after ("snodo's", "snodo:",
# "snodo="). Set membership, not `in` on a string: the empty string at a
# snippet boundary must count as freestanding.
_NOT_INVOCATION_BEFORE = frozenset(".-_/A-Za-z0-9")
_NOT_INVOCATION_AFTER = frozenset("='.:")


def _clean(token: str) -> str:
    return token.strip(_TRIM).strip("`'\"")


def _is_command_word(token: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9-]*", token) is not None


def iter_code_spans(text: str):
    """Yield (lineno, snippet) for every code context in a markdown page.

    Dangling checks look only inside fenced blocks and inline backtick
    spans: outside them, ``snodo`` is overwhelmingly the project's name in
    prose ("snodo is one tool provider among several"), and scanning prose
    drowns real findings in false positives. Coverage — the lenient
    direction — still reads the whole file. Trailing ``#`` comments in fence
    lines are prose too and are cut.
    """
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            yield lineno, _FENCE_COMMENT.sub("", line).rstrip()
        else:
            for match in _INLINE_CODE.finditer(line):
                yield lineno, match.group(1)


def _span_tokens(snippet: str) -> list[tuple[str, bool]]:
    """(clean token, is-invocation-position) pairs for one code snippet.

    A ``snodo`` token is an invocation only when it is a freestanding word:
    not ``snodo's`` or ``snodo:`` (a log prefix), not ``snodo=`` (an
    assignment), and not the tail of a path or dotted name (``.snodo``,
    ``snodo.cli``).
    """
    tokens: list[tuple[str, bool]] = []
    for match in _WORD.finditer(snippet):
        start, end = match.start(), match.end()
        before = snippet[start - 1] if start else ""
        after = snippet[end : end + 1]
        is_command = (
            _clean(match.group(0)) == "snodo"
            and before not in _NOT_INVOCATION_BEFORE
            and after not in _NOT_INVOCATION_AFTER
        )
        tokens.append((_clean(match.group(0)), is_command))
    return tokens


class ScanResult:
    def __init__(self) -> None:
        self.dangling: list[str] = []  # "<rel>:<line>: <message>"


def _invocation(path: tuple[str, ...]) -> str:
    return ("snodo " + " ".join(path)).strip()


def scan_references(repo_root: Path, surface: Surface) -> ScanResult:
    """Find every dangling command, flag and config-key reference in docs.

    Resolution per ``snodo`` reference: consume word tokens while they name
    children of the current group. A word token that names no child of a
    group is a dangling subcommand. Once a leaf is reached, remaining tokens
    are its arguments and are not command candidates; ``--flag`` tokens must
    be options of the resolved chain, and dotted ``llm.``/``engine.`` tokens
    must be settable config keys.
    """
    result = ScanResult()
    for path in iter_doc_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, snippet in iter_code_spans(text):
            tokens = _span_tokens(snippet)
            positions = [i for i, (_, is_cmd) in enumerate(tokens) if is_cmd]
            for ref_no, start in enumerate(positions):
                end = (
                    positions[ref_no + 1]
                    if ref_no + 1 < len(positions)
                    else len(tokens)
                )
                _scan_reference(
                    result,
                    rel,
                    lineno,
                    [token for token, _ in tokens[start + 1 : end]],
                    surface,
                )
    return result


def _scan_reference(
    result: ScanResult,
    rel: str,
    lineno: int,
    tokens: list[str],
    surface: Surface,
) -> None:
    path: tuple[str, ...] = ()
    resolved_group = True  # current node accepts command-word children
    unresolved = False

    for token in tokens:
        if unresolved:
            break
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            node = surface.commands.get(path)
            allowed = node["opts"] if node else frozenset()
            if flag not in allowed:
                result.dangling.append(
                    f"{rel}:{lineno}: flag `{flag}` is not an option of "
                    f"`{_invocation(path)}` — it was removed, renamed, or "
                    "belongs to a different command; update the page to the "
                    "current surface."
                )
            continue
        if not resolved_group or not _is_command_word(token):
            # Arguments, placeholders and values: anything after a leaf (or
            # after a non-command token) stops command-path resolution.
            resolved_group = False
            continue
        child = path + (token,)
        node = surface.commands.get(child)
        if node is None:
            result.dangling.append(
                f"{rel}:{lineno}: `{_invocation(child)}` names no command "
                f"under `{_invocation(path)}` — it was renamed or removed; "
                "update the page to the current surface."
            )
            unresolved = True
            continue
        path = child
        resolved_group = node["group"]

    # Dotted config-key references anywhere in the segment.
    for token in tokens:
        if not token.startswith(CONFIG_KEY_PREFIXES):
            continue
        key = token.rstrip(".")
        if key not in surface.config_keys:
            result.dangling.append(
                f"{rel}:{lineno}: config key `{key}` is not settable via "
                "`snodo config set` — it was renamed, removed or moved; "
                "update the page to the current surface."
            )


def _coverage_pattern(item_id: str) -> str:
    """Regex that recognises a mention of the item anywhere in a page."""
    kind, _, name = item_id.partition(" ")
    if kind == "command":
        joined = r"\s+".join(re.escape(seg) for seg in name.split(" "))
        return rf"(?<![\w-])snodo\s+{joined}(?![\w-])"
    return rf"(?<![\w.-]){re.escape(name)}(?![\w-])"


def scan_name_mentions(repo_root: Path, items: dict[str, str]) -> set[str]:
    """Which items appear anywhere in the documentation corpus.

    Matching is a word-boundary search over whole files; *where* something
    is mentioned is an editorial choice, only *that* it is mentioned is
    checked. Commands must appear as a ``snodo <path>`` invocation.
    """
    corpus = "\n".join(
        path.read_text(encoding="utf-8") for path in iter_doc_files(repo_root)
    )
    covered: set[str] = set()
    for item_id in items:
        if re.search(_coverage_pattern(item_id), corpus):
            covered.add(item_id)
    return covered


# ---------------------------------------------------------------------------
# Baseline (debt list) and the ratchet
# ---------------------------------------------------------------------------

def parse_baseline(baseline_path: Path) -> dict[str, str]:
    """Read the debt list: one ``<kind> <name...>`` item id per line."""
    entries: dict[str, str] = {}
    if not baseline_path.is_file():
        return entries
    for lineno, raw in enumerate(
        baseline_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, name = line.partition(" ")
        if kind not in KINDS or not name:
            raise ValueError(
                f"{baseline_path}:{lineno}: expected '<kind> <name>' with "
                f"kind in {KINDS}, got: {raw!r}"
            )
        entries[line] = kind
    return entries


def check(repo_root: Path, baseline: dict[str, str]) -> dict:
    """Run both properties against the live surface and the docs corpus."""
    surface = Surface()
    collect_cli_commands(surface, repo_root)
    collect_mcp_tools(surface, repo_root)
    collect_config_keys(surface, repo_root)

    outcome: dict = {
        "failures": [f"surface collection failed: {err}" for err in surface.errors],
        "new_gaps": [],
        "baselined_gaps": [],
        "filled": [],
        "stale": [],
        "checked": surface.documented_surface(),
        "dangling": [],
        "collection_failed": bool(surface.errors),
        "counts": {
            "commands": sum(
                1
                for i in surface.documented_surface()
                if i.startswith("command ")
            ),
            "tools": len(surface.tools),
            "config_keys": len(surface.config_keys),
        },
    }
    if surface.errors:
        return outcome

    scan = scan_references(repo_root, surface)
    outcome["dangling"] = sorted(scan.dangling)
    outcome["failures"].extend(outcome["dangling"])

    covered = scan_name_mentions(repo_root, outcome["checked"])

    gaps = set(outcome["checked"]) - covered
    outcome["new_gaps"] = sorted(gaps - set(baseline))
    outcome["baselined_gaps"] = sorted(gaps & set(baseline))
    # A baselined gap that is now documented leaves the list for good; a
    # baselined item whose surface entry vanished is stale for removal too.
    outcome["filled"] = sorted((set(baseline) & set(outcome["checked"])) - gaps)
    outcome["stale"] = sorted(set(baseline) - set(outcome["checked"]))

    for item_id in outcome["new_gaps"]:
        description = outcome["checked"].get(item_id, item_id)
        outcome["failures"].append(
            f"{item_id}: {description} is invokable but no page under "
            f"{DOCS_DIR_NAME}/ mentions it. Document it (any page, your "
            "call) — a new surface cannot join the baseline; the baseline is "
            "a frozen debt list, not a place to park undocumented new work."
        )
    return outcome


HEADER_LINES = [
    "# Documentation debt list — one entry per invokable surface item that",
    f"# no page under {DOCS_DIR_NAME}/ (minus {'/'.join(sorted(EXCLUDED_DOC_DIRS))}) "
    "mentions.",
    "#",
    "# Each line is '<kind> <name>': command <path words>, tool <name>, or",
    "# config <dotted.key>. The list can only shrink: run",
    "#   uv run python scripts/enforce_docs_coverage.py --update-baseline",
    "# after documenting one of these — the entry leaves for good and an",
    "# undocumented item can never join it. There is no suppression comment",
    "# on purpose: an undocumented user-facing surface is a docs bug, not a",
    "# style choice.",
]


def write_baseline(baseline_path: Path, outcome: dict, is_seed: bool) -> int:
    """Pay the debt down: rewrite the baseline at the current gap set.

    Only ever shrinks it. Refuses while the check fails — which includes any
    gap not already in the baseline, so the update tool cannot admit new
    debt. The one exception is the very first recording: with no baseline at
    all (or an empty one) the current gaps are seeded, so the check is
    useful from day one instead of blocked behind a documentation sprint.
    Dangling references are never seeded — they are fixed, not baselined.
    """
    if outcome["collection_failed"]:
        print("Refusing to write the baseline while the surface cannot be collected:")
        for failure in outcome["failures"]:
            print(f"- {failure}")
        return 1
    if outcome["dangling"]:
        print("Refusing to write the baseline while dangling references exist:")
        for failure in outcome["dangling"]:
            print(f"- {failure}")
        return 1
    if not is_seed and outcome["failures"]:
        print("Refusing to update the baseline while the check fails:")
        for failure in outcome["failures"]:
            print(f"- {failure}")
        return 1
    if is_seed:
        entries = sorted(set(outcome["baselined_gaps"]) | set(outcome["new_gaps"]))
    else:
        entries = sorted(set(outcome["baselined_gaps"]))
    body = "".join(f"{item_id}\n" for item_id in entries)
    baseline_path.write_text(
        "\n".join(HEADER_LINES) + "\n\n" + body, encoding="utf-8"
    )
    print(
        f"Wrote baseline with {len(entries)} undocumented-surface entries "
        f"to {baseline_path}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the documentation-coverage ratchet."
    )
    parser.add_argument(
        "--repo", default=".", help="Repository root (default: current directory)."
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=f"Baseline file (default: <repo>/{BASELINE_RELATIVE_PATH}).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline after documenting gaps (only ever shrinks).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    baseline_path = (
        Path(args.baseline) if args.baseline else repo_root / BASELINE_RELATIVE_PATH
    )
    try:
        baseline = parse_baseline(baseline_path)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1

    outcome = check(repo_root, baseline)

    for failure in outcome["failures"]:
        print(f"FAIL: {failure}")
    for item_id in outcome["filled"]:
        print(
            f"{item_id}: now documented — remove it from the baseline with "
            "--update-baseline; it cannot come back."
        )
    for item_id in outcome["stale"]:
        print(
            f"{item_id}: baseline entry for a surface item that no longer "
            "exists — run --update-baseline to drop it."
        )

    if not outcome["failures"]:
        counts = outcome["counts"]
        print(
            f"Docs ratchet OK: {counts['commands']} commands, "
            f"{counts['tools']} MCP tools and {counts['config_keys']} config "
            f"keys checked; {len(outcome['baselined_gaps'])} undocumented "
            "gaps baselined, 0 dangling references."
        )

    if args.update_baseline:
        # Handled before the failure return so the first recording (an empty
        # baseline, where every gap is a fresh "new gap" and so a failure) can
        # seed the list. write_baseline still refuses on dangling references.
        return write_baseline(baseline_path, outcome, is_seed=not baseline)

    if outcome["failures"]:
        print(
            f"Documentation coverage check failed: "
            f"{len(outcome['failures'])} violation(s)."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
