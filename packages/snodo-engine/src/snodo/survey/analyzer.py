"""Repository analysis engine for discovering module boundaries and tooling.

FILE: snodo/survey/analyzer.py

Discovers observable facts about an existing repository:
- Module boundaries from manifests that are actually present (workspace
  declarations plus nested package.json / pyproject.toml / Cargo.toml /
  go.mod / pom.xml files, even when nothing upstream declares them)
- Languages per module, read from the repository's own files only
- Test commands, confirmed by the contents of the marker file rather than
  assumed from the marker file's mere presence
- Documentation and decision record locations
- Repository-level tooling

Marker files used for module boundary discovery:
- package.json (Node.js)
- pyproject.toml (Python)
- Cargo.toml (Rust)
- go.mod (Go)
- pom.xml (Maven)
- pubspec.yaml (Dart/Flutter)

A manifest inside a dotted directory (for example .opencode/) is tooling
configuration, not a module of the product, and is excluded. Files under
dependency and build trees (node_modules, dist, vendor, Pods, ...) are
vendored, not the repository's own source, and are excluded from language
detection.

Division of labour: everything above is arithmetic and runs unconditionally.
Two questions are not arithmetic and go to a judge only when one is provided:

- Is a manifest-backed work a product module, or scaffolding that exists to
  hold a doc site or a test harness? Nothing on disk distinguishes them
  reliably.
- Is a source-dense directory that declares no manifest we parse a module
  boundary? A boundary is a boundary whether or not it declares itself in a
  format we recognise.

The deterministic pass proposes these subjects together with an evidence
dossier it gathered from the filesystem; the judge only classifies. Its
verdicts are accepted only when they cite files that exist inside the
subject's own (non-vendored, non-hidden) source, so a conclusion is
attributable to evidence a reader can go and look at and disagree with.
When no judge is available, a judge call fails, or the judge abstains, the
deterministic result stands and the judgement is reported as not made.

Test command detection — the marker must actually declare the command:
- package.json  → npm test, only if scripts.test exists
- pyproject.toml / setup.cfg / setup.py → pytest, only if pytest is
  configured or declared as a dependency
- Cargo.toml → cargo test (built into the toolchain that the manifest pins)
- go.mod → go test ./... (built into the toolchain that the manifest pins)
- Makefile → make test, only if the Makefile actually has a test target

A marker file is evidence that a command might exist, not that it does;
when nothing confirms, no test command is reported.

Never infers protocol requirements from absence of practices.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import tomllib
import yaml

from snodo.survey.models import (
    JudgementRecord,
    ModuleInfo,
    RepositorySurvey,
    SurveyFinding,
    UnmadeJudgement,
)

_logger = logging.getLogger(__name__)

# Workspace marker files and their significance
_WORKSPACE_MARKERS: List[Tuple[str, str]] = [
    ("package.json", "npm"),
    ("pyproject.toml", "python"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "maven"),
    ("pubspec.yaml", "dart"),
]

# Manifest file names that mark a module boundary when found nested in the tree
_MODULE_MANIFEST_NAMES: Set[str] = {name for name, _ in _WORKSPACE_MARKERS}

# Directory names that are dependency, build, or cache trees — never the
# repository's own source.  Anything starting with "." is excluded too
# (hidden tooling directories such as .opencode, .venv, .git).
_EXCLUDED_DIR_NAMES: Set[str] = {
    "node_modules",
    "bower_components",
    "vendor",
    "dist",
    "build",
    "out",
    "target",
    "venv",
    "__pycache__",
    "Pods",      # iOS dependency tree, installed alongside the app it serves
    "Carthage",  # iOS dependency checkouts, same
}


def _is_excluded_dir(name: str) -> bool:
    """A directory whose contents are not the repository's own source."""
    return name.startswith(".") or name in _EXCLUDED_DIR_NAMES


def _iter_own_source_dirs(root: Path):
    """Walk a directory tree, pruning dependency/build/hidden directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded_dir(d))
        yield Path(dirpath), dirnames, filenames


# Language detection rules by file extensions
_LANGUAGE_EXTS = {
    "python": {".py", ".pyx", ".pyi"},
    "typescript": {".ts", ".tsx"},
    "javascript": {".js", ".jsx", ".mjs", ".cjs"},
    "rust": {".rs"},
    "go": {".go"},
    "java": {".java"},
    "kotlin": {".kt", ".kts"},
    "c": {".c", ".h"},
    "cpp": {".cc", ".cpp", ".cxx", ".h", ".hpp"},
    "csharp": {".cs"},
    "php": {".php"},
    "ruby": {".rb"},
    "shell": {".sh", ".bash"},
    "yaml": {".yml", ".yaml"},
    "svelte": {".svelte"},
    "astro": {".astro"},
    "swift": {".swift"},
    "dart": {".dart"},
}

# Reverse map: extension → languages it can indicate
_EXT_TO_LANGUAGES: Dict[str, List[str]] = {}
for _lang, _exts in _LANGUAGE_EXTS.items():
    for _ext in _exts:
        _EXT_TO_LANGUAGES.setdefault(_ext, []).append(_lang)


def _read_json_file(path: Path) -> Optional[dict]:
    """Read and parse a JSON file, returning None on error."""
    try:
        return json.loads(path.read_text())
    except Exception as e:
        _logger.debug("Could not read JSON file %s: %s", path, e)
        return None


def _read_toml_file(path: Path) -> Optional[dict]:
    """Read and parse a TOML file, returning None on error."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        _logger.debug("Could not read TOML file %s: %s", path, e)
        return None


def _read_yaml_file(path: Path) -> Optional[dict]:
    """Read and parse a YAML file, returning None on error."""
    try:
        return yaml.safe_load(path.read_text())
    except Exception as e:
        _logger.debug("Could not read YAML file %s: %s", path, e)
        return None


def _read_text_file(path: Path) -> Optional[str]:
    """Read a text file, returning None on error."""
    try:
        return path.read_text(errors="replace")
    except Exception as e:
        _logger.debug("Could not read file %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Test command confirmation
#
# Each confirmer inspects the *contents* of a marker file and returns the
# test command only when the file actually provides it.  Presence of the
# file alone is never proof.
# ---------------------------------------------------------------------------

def _confirm_npm_test(marker_path: Path) -> Optional[str]:
    """npm test is real only if package.json declares a test script."""
    data = _read_json_file(marker_path)
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if isinstance(scripts, dict) and scripts.get("test"):
        return "npm test"
    return None


def _requirement_name(req: object) -> str:
    """Bare package name of a PEP 508 / poetry requirement string."""
    if not isinstance(req, str):
        return ""
    return re.split(r"[<>=!~;\[\s]", req.strip(), maxsplit=1)[0].strip().lower()


def _pytest_in_requirements(reqs: object) -> bool:
    if not isinstance(reqs, (list, tuple)):
        return False
    return any(_requirement_name(r) == "pytest" for r in reqs)


def _confirm_pyproject_pytest(marker_path: Path) -> Optional[str]:
    """pytest is real only if pyproject.toml configures or requires it."""
    data = _read_toml_file(marker_path)
    if not isinstance(data, dict):
        return None

    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    if "pytest" in tool:
        return "pytest"

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    if _pytest_in_requirements(project.get("dependencies")):
        return "pytest"
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for reqs in optional.values():
            if _pytest_in_requirements(reqs):
                return "pytest"

    # PEP 735 dependency groups ([dependency-groups] tables or include lists)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for reqs in groups.values():
            if _pytest_in_requirements(reqs):
                return "pytest"
    elif isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and _pytest_in_requirements(group.get("requirements")):
                return "pytest"

    # Poetry-style dependencies and dev-dependencies
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    if _pytest_in_requirements(poetry.get("dependencies")) or _pytest_in_requirements(
        poetry.get("dev-dependencies")
    ):
        return "pytest"

    return None


def _confirm_setup_cfg_pytest(marker_path: Path) -> Optional[str]:
    """pytest is real only if setup.cfg carries a [tool:pytest] section."""
    text = _read_text_file(marker_path)
    if text and re.search(r"^\s*\[tool:pytest\]", text, re.MULTILINE):
        return "pytest"
    return None


def _confirm_setup_py_pytest(marker_path: Path) -> Optional[str]:
    """pytest from setup.py only if the file actually references pytest."""
    text = _read_text_file(marker_path)
    if text and re.search(r"\bpytest\b", text):
        return "pytest"
    return None


def _confirm_cargo_test(marker_path: Path) -> Optional[str]:
    """cargo test ships with the toolchain a valid Cargo.toml pins."""
    data = _read_toml_file(marker_path)
    if isinstance(data, dict) and ("package" in data or "workspace" in data):
        return "cargo test"
    return None


def _confirm_go_test(marker_path: Path) -> Optional[str]:
    """go test ships with the toolchain a valid go.mod pins."""
    text = _read_text_file(marker_path)
    if text and re.search(r"^module\s+\S+", text, re.MULTILINE):
        return "go test ./..."
    return None


def _confirm_makefile_test(marker_path: Path) -> Optional[str]:
    """make test is real only if the Makefile defines a test target."""
    text = _read_text_file(marker_path)
    if not text:
        return None
    for line in text.splitlines():
        if not line or line[0] in " \t#":
            continue  # recipe body or comment
        target_part, sep, _ = line.partition(":")
        if not sep or "=" in target_part:
            continue  # not a rule line
        if "test" in target_part.split():
            return "make test"
    return None


# Test command detection rules: marker file → confirmer
_TEST_MARKERS: List[Tuple[str, Callable[[Path], Optional[str]]]] = [
    ("package.json", _confirm_npm_test),
    ("pyproject.toml", _confirm_pyproject_pytest),
    ("setup.py", _confirm_setup_py_pytest),
    ("setup.cfg", _confirm_setup_cfg_pytest),
    ("Cargo.toml", _confirm_cargo_test),
    ("Makefile", _confirm_makefile_test),
    ("go.mod", _confirm_go_test),
]


def _detect_npm_workspaces(project_root: Path) -> List[str]:
    """Extract workspace paths from package.json."""
    package_json = project_root / "package.json"
    if not package_json.exists():
        return []

    data = _read_json_file(package_json)
    if not data:
        return []

    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, list):
        return workspaces
    if isinstance(workspaces, dict):
        return workspaces.get("packages", [])
    return []


def _detect_python_workspaces(project_root: Path) -> List[str]:
    """Extract workspace paths from pyproject.toml."""
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return []

    data = _read_toml_file(pyproject)
    if not data:
        return []

    # PEP 735 / poetry-style workspaces
    workspace_cfg = data.get("tool", {}).get("poetry", {}).get("packages", [])
    if workspace_cfg:
        return [p.get("include", "") for p in workspace_cfg if p.get("include")]

    # Pydantic workspace style
    workspace_cfg = data.get("tool", {}).get("pydantic", {}).get("workspaces", [])
    if workspace_cfg:
        return workspace_cfg

    return []


def _detect_cargo_workspaces(project_root: Path) -> List[str]:
    """Extract workspace paths from Cargo.toml."""
    cargo_toml = project_root / "Cargo.toml"
    if not cargo_toml.exists():
        return []

    data = _read_toml_file(cargo_toml)
    if not data:
        return []

    return data.get("workspace", {}).get("members", [])


def _detect_go_workspaces(project_root: Path) -> List[str]:
    """Extract workspace paths from go.work (Go 1.18+)."""
    go_work = project_root / "go.work"
    if not go_work.exists():
        return []

    content = go_work.read_text()
    modules = []
    for line in content.splitlines():
        if line.startswith("use"):
            # "use ./path/to/module" or "use (./a ./b)"
            match = re.search(r'use\s+(?:\(.*?\)|(\S+))', line, re.DOTALL)
            if match:
                path = match.group(1)
                if path:
                    modules.append(path)
    return modules


def _normalize_module_path(raw: str) -> str:
    """Normalize a declared workspace path to a repo-relative posix path."""
    path = raw.strip().rstrip("/")
    if path.startswith("./"):
        path = path[2:]
    return path


def _has_glob_magic(raw: str) -> bool:
    """True for workspace patterns like packages/* — not concrete paths."""
    return any(ch in raw for ch in "*?[]{")


def _discover_declared_modules(project_root: Path) -> List[Tuple[str, List[str]]]:
    """Module boundaries announced by root-level workspace declarations."""
    modules: List[Tuple[str, List[str]]] = []

    for ws_paths in (
        _detect_npm_workspaces(project_root),
        _detect_python_workspaces(project_root),
        _detect_cargo_workspaces(project_root),
        _detect_go_workspaces(project_root),
    ):
        for i, ws_path in enumerate(ws_paths):
            if not isinstance(ws_path, str) or _has_glob_magic(ws_path):
                # Glob patterns are expanded by the nested-manifest scan below
                continue
            path = _normalize_module_path(ws_path)
            if not path or path == ".":
                continue
            module_id = Path(path).name or f"module_{i}"
            modules.append((module_id, [path]))

    return modules


def _discover_manifest_modules(project_root: Path) -> List[Tuple[str, List[str]]]:
    """Module boundaries from manifests actually present in the tree.

    A repository that grew into services rather than being scaffolded as a
    workspace has nested manifests with nothing upstream declaring them.
    A nested manifest is a boundary whether or not something announces it.
    Manifests inside hidden directories (e.g. .opencode/) are tooling
    configuration, not modules of the product, and are skipped.
    """
    modules: List[Tuple[str, List[str]]] = []

    for dirpath, _dirnames, filenames in _iter_own_source_dirs(project_root):
        if dirpath == project_root:
            continue  # the root manifest is the repository, not a module
        rel = dirpath.relative_to(project_root).as_posix()
        if any(name in filenames for name in _MODULE_MANIFEST_NAMES):
            modules.append((dirpath.name or rel, [rel]))

    return modules


def _discover_module_boundaries(project_root: Path) -> List[Tuple[str, List[str]]]:
    """Discover module boundaries from declarations and present manifests.

    Returns list of (module_id, paths) tuples, deduplicated by path.
    """
    modules: List[Tuple[str, List[str]]] = []
    seen: Set[str] = set()

    for module_id, paths in (
        _discover_declared_modules(project_root) + _discover_manifest_modules(project_root)
    ):
        key = paths[0] if paths else module_id
        if key in seen:
            continue
        seen.add(key)
        modules.append((module_id, paths))

    return modules


# ---------------------------------------------------------------------------
# Undeclared boundary candidates (arithmetic: propose, never promote)
#
# A directory that holds a body of the repository's own source without a
# manifest this walk can parse (an Xcode app tree, a native project) is a
# boundary the filesystem shows but no declaration announces. Proposing it
# is arithmetic; deciding whether it is a module needs the judgement of a
# reader who knows what the repository is for — so candidates are handed to
# the judge and never become modules on the deterministic pass alone.
# ---------------------------------------------------------------------------

# Own-source files needed before a manifest-less directory is worth asking
# about (a handful of scripts is not a boundary).
_CANDIDATE_MIN_SOURCE_FILES = 25


def _count_own_source_files(dirpath: Path, skip_dirs: Optional[Set[Path]] = None) -> int:
    """Count files with a recognised source extension, pruning vendored trees.

    Directories in *skip_dirs* (absolute paths) are not counted either: a
    directory whose sources all live inside already-claimed modules holds no
    source of its own and is not a boundary.
    """
    count = 0
    if not dirpath.is_dir():
        return 0
    skip = skip_dirs or set()
    for current, dirnames, filenames in os.walk(dirpath):
        cur = Path(current)
        dirnames[:] = [
            d for d in dirnames
            if not _is_excluded_dir(d) and (cur / d) not in skip
        ]
        count += sum(
            1 for name in filenames
            if Path(name).suffix.lower() in _EXT_TO_LANGUAGES
        )
    return count


# Declarations a directory may make in formats the deterministic pass does
# not parse.  These never create a module by themselves — they are recorded
# in the evidence dossier so the judge sees what the directory declares.
_UNSEEN_PACKAGE_SIGNALS = (
    "*.xcodeproj", "*.xcworkspace", "Podfile", "Podfile.lock",
    "AndroidManifest.xml", "gradlew", "build.gradle", "build.gradle.kts",
    "settings.gradle", "Info.plist",
)


def _boundary_signals(dirpath: Path) -> List[str]:
    """Repo-relative paths of declarations this directory makes outside our parse set."""
    signals: List[str] = []
    for pattern in _UNSEEN_PACKAGE_SIGNALS:
        if "*" in pattern:
            hits = sorted(dirpath.glob(pattern))
        else:
            hits = [dirpath / pattern] if (dirpath / pattern).is_file() else []
        for hit in hits:
            signals.append(hit.relative_to(dirpath.parent).as_posix())
    return signals


def _discover_undeclared_candidates(
    project_root: Path,
    claimed_paths: Set[str],
) -> List[Tuple[str, List[str]]]:
    """Source-dense directories that declare no manifest this walk recognises.

    Only applies when the root itself declares no manifest: a root package
    declaration covers its whole tree, and the directories inside it are
    that package's internals, not sibling boundaries.

    The walk skips dependency/build/hidden trees and directories already
    claimed as modules. The first directory that qualifies is recorded and
    not descended into, so the candidate is the outermost boundary.
    """
    if any((project_root / name).is_file() for name in _MODULE_MANIFEST_NAMES):
        return []

    candidates: List[Tuple[str, List[str]]] = []
    claimed_abs = {project_root / p for p in claimed_paths}

    def walk(rel: str) -> None:
        dirpath = project_root / rel if rel else project_root
        if rel and rel in claimed_paths:
            return
        if rel and _count_own_source_files(dirpath, skip_dirs=claimed_abs) >= _CANDIDATE_MIN_SOURCE_FILES:
            candidates.append((dirpath.name, [rel]))
            return
        try:
            children = sorted(
                entry.name for entry in dirpath.iterdir()
                if entry.is_dir() and not _is_excluded_dir(entry.name)
            )
        except OSError:
            return
        for child in children:
            walk(f"{rel}/{child}" if rel else child)

    walk("")
    return candidates


def _detect_languages(project_root: Path, paths: Optional[List[str]] = None) -> List[str]:
    """Detect programming languages from the repository's own files.

    Dependency, build, and hidden trees (node_modules, .venv, dist, ...)
    are excluded: they are vendored, not the repository's source.
    """
    found: Set[str] = set()
    search_paths = [project_root] if not paths else [project_root / p for p in paths]

    for search_path in search_paths:
        if not search_path.exists():
            continue
        for dirpath, _dirnames, filenames in _iter_own_source_dirs(search_path):
            for filename in filenames:
                langs = _EXT_TO_LANGUAGES.get(Path(filename).suffix.lower())
                if langs:
                    found.update(langs)

    return sorted(found)


def _detect_test_command(project_root: Path, paths: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a test command, confirmed by a marker file's contents.

    A marker file is evidence that a command might exist, not that it does:
    each candidate is confirmed against the file before being returned.
    Returns (test_command, marker_file), or (None, None) when nothing
    resolves — which is reported plainly rather than guessed around.
    """
    search_paths = [project_root] if not paths else [project_root / p for p in paths]

    for marker_file, confirm in _TEST_MARKERS:
        for search_path in search_paths:
            # A Makefile belongs to the repository root, not to a module
            base = project_root if marker_file == "Makefile" else search_path
            if not base.exists():
                continue
            marker_path = base / marker_file
            if not marker_path.is_file():
                continue
            test_cmd = confirm(marker_path)
            if test_cmd:
                return (test_cmd, marker_file)

    return (None, None)


def _detect_decision_paths(project_root: Path) -> List[str]:
    """Detect directories containing decision records.

    Looks for docs/decisions, docs/adr, and similar patterns.
    """
    paths = []
    candidates = [
        "docs/decisions",
        "docs/adr",
        "decisions",
        "adr",
        ".adr/decisions",
    ]

    for candidate in candidates:
        path = project_root / candidate
        if path.exists() and path.is_dir():
            # Check if it has any markdown files
            md_files = list(path.glob("*.md"))
            if md_files:
                paths.append(candidate)

    return paths


# ---------------------------------------------------------------------------
# Judgement: the questions that are not arithmetic
#
# A judge is a callable the caller provides — the CLI wires it to the recon
# machinery's read-only agent dispatch.  It receives the evidence dossier
# (subjects plus what the deterministic walk already gathered) and returns
# verdicts, or signals that no verdicts came.  The analyzer never imports an
# agent itself: survey works with no model configured, and a failed or
# abstained judgement degrades to the deterministic result, reported plainly.
# ---------------------------------------------------------------------------

# Judge contract: takes the dossier dict, returns either
#   - a list of verdict dicts: {"subject", "verdict", "reason", "cited_files"}
#   - {"verdicts": [...]} or {"reason": "..."} for why no verdicts came
#   - None when the agent was unavailable or its call failed
Judge = Callable[[Dict[str, Any]], Any]

KIND_BOUNDARY_ROLE = "boundary-role"
KIND_UNDECLARED_BOUNDARY = "undeclared-boundary"

_VERDICTS_BY_KIND: Dict[str, Tuple[str, ...]] = {
    KIND_BOUNDARY_ROLE: ("product", "scaffolding"),
    KIND_UNDECLARED_BOUNDARY: ("module", "not-module"),
}

_JUDGE_QUESTIONS: Dict[str, str] = {
    KIND_BOUNDARY_ROLE: (
        "Is this work a module of the product, or scaffolding that exists to "
        "hold a doc site, a test harness, or tooling?"
    ),
    KIND_UNDECLARED_BOUNDARY: (
        "Is this directory a module boundary of the product, even though it "
        "carries no manifest this survey parses — or is it something else "
        "(generated output, vendored content, internals of another work)?"
    ),
}

# Dossier limits: evidence is a bounded digest of what the walk gathered,
# not an invitation to explore.
_DOSSIER_DEPTH = 2
_DOSSIER_MAX_LISTING = 80
_DOSSIER_MAX_MANIFEST_CHARS = 1500


def _subject_id(kind: str, path: str) -> str:
    return f"{kind}:{path}"


def _clip(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text.strip()[:limit]


def _bounded_listing(
    dirpath: Path,
    max_depth: int = _DOSSIER_DEPTH,
    cap: int = _DOSSIER_MAX_LISTING,
) -> Tuple[List[str], Set[str]]:
    """Depth-limited listing plus the paths it revealed, relative to the root.

    The listing bounds what the judge is shown.  A verdict's citations are
    checked against the filesystem (see _resolve_citation), so the reader
    can always go and look at the specific file a conclusion rests on.
    """
    entries: List[str] = []
    revealed: Set[str] = set()

    def walk(current: Path, depth: int) -> None:
        try:
            children = sorted(current.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except OSError:
            return
        for entry in children:
            if entry.is_dir() and _is_excluded_dir(entry.name):
                continue
            if len(entries) >= cap:
                return
            rel = entry.relative_to(dirpath).as_posix()
            entries.append(f"{rel}/" if entry.is_dir() else rel)
            revealed.add(rel)
            if entry.is_dir() and depth < max_depth:
                walk(entry, depth + 1)

    walk(dirpath, 0)
    return entries, revealed


def _extension_histogram(dirpath: Path) -> Dict[str, int]:
    """Count of own-source files by extension, vendored trees pruned."""
    histogram: Dict[str, int] = {}
    if not dirpath.is_dir():
        return histogram
    for _current, _dirs, files in _iter_own_source_dirs(dirpath):
        for name in files:
            ext = Path(name).suffix.lower() or "(no extension)"
            histogram[ext] = histogram.get(ext, 0) + 1
    return dict(sorted(histogram.items(), key=lambda item: (-item[1], item[0]))[:25])


def _summarize_manifest(path: Path) -> str:
    """A compact structured view of a manifest's contents for the dossier."""
    raw = _read_text_file(path) or ""
    summary: Optional[object] = None
    if path.name == "package.json":
        data = _read_json_file(path)
        if isinstance(data, dict):
            def _names(field: str):
                value = data.get(field)
                return sorted(value) if isinstance(value, dict) else value

            summary = {
                "name": data.get("name"),
                "description": data.get("description"),
                "scripts": _names("scripts"),
                "workspaces": data.get("workspaces"),
                "dependencies": _names("dependencies"),
                "devDependencies": _names("devDependencies"),
            }
    elif path.name in ("pyproject.toml", "Cargo.toml"):
        data = _read_toml_file(path)
        if isinstance(data, dict):
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            package = data.get("package") if isinstance(data.get("package"), dict) else {}
            workspace = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
            summary = {
                "name": project.get("name") or package.get("name"),
                "dependencies": project.get("dependencies") or package.get("dependencies"),
                "workspace_members": workspace.get("members"),
            }
    elif path.name == "go.mod":
        summary = {"first_lines": raw.splitlines()[:8]}
    elif path.name == "pom.xml":
        match = re.search(r"<artifactId>(.*?)</artifactId>", raw)
        summary = {"artifact_id": match.group(1) if match else None}

    text = json.dumps(summary, default=str) if summary is not None else raw
    if len(text) > _DOSSIER_MAX_MANIFEST_CHARS:
        text = text[:_DOSSIER_MAX_MANIFEST_CHARS] + " …(truncated)"
    return text


def _gather_subject_evidence(project_root: Path, rel: str) -> Dict[str, Any]:
    """Evidence dossier for one subject, gathered from the filesystem."""
    dirpath = project_root / rel
    evidence: Dict[str, Any] = {
        "own_source_file_count": _count_own_source_files(dirpath),
        "source_file_histogram": _extension_histogram(dirpath),
    }
    entries, _revealed = _bounded_listing(dirpath)
    evidence["listing"] = entries

    manifests = {}
    for name in sorted(_MODULE_MANIFEST_NAMES):
        mpath = dirpath / name
        if mpath.is_file():
            manifests[f"{rel}/{name}"] = _summarize_manifest(mpath)
    if manifests:
        evidence["manifests"] = manifests

    signals = _boundary_signals(dirpath)
    if signals:
        evidence["boundary_signals"] = signals

    return evidence


def _root_context(project_root: Path) -> Dict[str, Any]:
    """Repository-level context the deterministic pass gathered."""
    context: Dict[str, Any] = {"name": project_root.name}
    root_manifests = sorted(
        name for name in _MODULE_MANIFEST_NAMES if (project_root / name).is_file()
    )
    if root_manifests:
        context["root_manifests"] = root_manifests
    entries, _revealed = _bounded_listing(project_root, max_depth=0)
    context["top_level"] = entries
    for readme in sorted(project_root.glob("README*"))[:1]:
        context["readme_excerpt"] = (_read_text_file(readme) or "")[
            :_DOSSIER_MAX_MANIFEST_CHARS
        ]
    return context


def _build_subjects(
    project_root: Path,
    modules: List[ModuleInfo],
    candidates: List[Tuple[str, List[str]]],
) -> List[Dict[str, Any]]:
    """Judgement subjects: each discovered boundary and each candidate."""
    subjects: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for module in modules:
        if not module.paths:
            continue
        path = module.paths[0]
        if path in seen:
            continue
        seen.add(path)
        subjects.append(_make_subject(project_root, KIND_BOUNDARY_ROLE, path))

    for _cand_id, cand_paths in candidates:
        path = cand_paths[0]
        if path in seen:
            continue
        seen.add(path)
        subjects.append(_make_subject(project_root, KIND_UNDECLARED_BOUNDARY, path))

    return subjects


def _make_subject(project_root: Path, kind: str, path: str) -> Dict[str, Any]:
    return {
        "id": _subject_id(kind, path),
        "kind": kind,
        "path": path,
        "question": _JUDGE_QUESTIONS[kind],
        "evidence": _gather_subject_evidence(project_root, path),
    }


def _consult_judge(
    judge: Optional[Judge],
    judge_mode: str,
    dossier: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], bool, str]:
    """Run the judge once for the whole dossier.

    Returns (verdicts by subject id, agent consulted, batch reason when no
    verdicts came at all).
    """
    if judge is None:
        reason = (
            "deterministic-only run, by request"
            if judge_mode == "off"
            else "no agent was configured or reachable for survey judgements"
        )
        return {}, False, reason

    try:
        outcome = judge(dossier)
    except Exception as e:  # a provider outage must not take survey down
        _logger.debug("Survey judge call failed: %s", e)
        return {}, False, f"the agent call failed: {e}"

    if isinstance(outcome, dict):
        verdicts = outcome.get("verdicts")
        if verdicts is None:
            return {}, False, outcome.get("reason") or "the agent call produced no verdicts"
    elif isinstance(outcome, list):
        verdicts = outcome
    else:
        verdicts = None

    if not isinstance(verdicts, list):
        return {}, False, "the agent call produced no usable verdicts"

    by_id = {
        verdict["subject"]: verdict
        for verdict in verdicts
        if isinstance(verdict, dict) and isinstance(verdict.get("subject"), str)
    }
    return by_id, True, ""


def _apply_verdict(
    project_root: Path,
    subject: Dict[str, Any],
    entry: Optional[Dict[str, Any]],
) -> Tuple[Optional[JudgementRecord], Optional[UnmadeJudgement]]:
    """Validate one verdict; accept it only if attributable to gathered evidence."""
    kind = subject["kind"]
    sid = subject["id"]
    path = subject["path"]

    def gap(reason: str):
        return None, UnmadeJudgement(sid, kind, path, reason)

    if entry is None:
        return gap("the agent returned no verdict for this subject")

    verdict = entry.get("verdict")
    if verdict == "abstain":
        reason = _clip(entry.get("reason"))
        return gap("the agent abstained" + (f": {reason}" if reason else ""))
    if verdict not in _VERDICTS_BY_KIND[kind]:
        return gap(f"unrecognized verdict for this question: {verdict!r}")

    cited = entry.get("cited_files")
    if not isinstance(cited, list) or not cited:
        return gap("the verdict cited no evidence files")
    normalised: List[str] = []
    for cite in cited:
        resolved = _resolve_citation(project_root, path, cite)
        if resolved is None:
            return gap(
                f"the verdict cites {cite!r}, which is not a file of the "
                "subject's own source the reader can go and check"
            )
        normalised.append(resolved)

    record = JudgementRecord(
        subject_id=sid,
        kind=kind,
        subject_path=path,
        verdict=verdict,
        reason=_clip(entry.get("reason")) or "(no reason given)",
        cited_files=normalised,
    )
    return record, None


def _resolve_citation(project_root: Path, subject_path: str, cite: Any) -> Optional[str]:
    """Anchor a cited path to the repository and confirm it is own source.

    Accepts repo-relative or subject-relative citations — 'src/index.ts'
    for subject 'app' means 'app/src/index.ts'.  A citation is verifiable
    only when it exists, sits inside the subject, and is not inside a
    vendored or hidden tree, so a verdict stays attributable to evidence a
    reader can go and look at rather than to the model's impression.
    """
    if not isinstance(cite, str) or not cite.strip():
        return None
    cleaned = cite.strip().replace("\\", "/").lstrip("/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.rstrip("/")
    if not cleaned:
        return None
    for cand in (cleaned, f"{subject_path}/{cleaned}"):
        parts = cand.split("/")
        if any(_is_excluded_dir(p) for p in parts):
            continue
        if cand != subject_path and not cand.startswith(subject_path + "/"):
            continue
        if (project_root / cand).exists():
            return cand
    return None


def _build_module(
    project_root: Path,
    module_id: str,
    paths: List[str],
    origin: Optional[str] = None,
    extra_evidence: Optional[List[str]] = None,
) -> ModuleInfo:
    """Assemble a module's observable facts with the same deterministic machinery."""
    evidence: List[str] = list(extra_evidence or [])

    languages = _detect_languages(project_root, paths)
    if languages:
        evidence.append(f"Languages: {', '.join(languages)}")

    test_cmd, marker = _detect_test_command(project_root, paths)
    if test_cmd:
        evidence.append(f"Test command confirmed from {marker}")

    decisions_path = None
    for candidate_path in ["docs/decisions", "docs/adr"]:
        for module_path in paths:
            full_candidate = f"{module_path}/{candidate_path}"
            candidate_dir = project_root / full_candidate
            if candidate_dir.exists() and list(candidate_dir.glob("*.md")):
                decisions_path = full_candidate
                evidence.append(f"Decision records at {full_candidate}")
                break
        if decisions_path:
            break

    if origin is None:
        declares_manifest = bool(paths) and any(
            (project_root / paths[0] / name).is_file()
            for name in _MODULE_MANIFEST_NAMES
        )
        origin = "manifest" if declares_manifest else "declaration"

    return ModuleInfo(
        module_id=module_id,
        paths=paths,
        languages=languages,
        test_command=test_cmd,
        test_marker_file=marker,
        decisions_path=decisions_path,
        evidence=evidence,
        origin=origin,
    )


def analyze_repository(
    project_root: Path,
    judge: Optional[Judge] = None,
    judge_mode: str = "auto",
) -> RepositorySurvey:
    """Analyze a repository and discover observable facts.

    Returns a RepositorySurvey containing:
    - Discovered modules (from workspace declarations and nested manifests,
      plus any undeclared boundary an accepted judgement promoted)
    - Languages per module (repository source only, no vendored trees)
    - Test commands (confirmed against marker file contents)
    - Decision record locations
    - Judgements the agent made, each citing its evidence files, and the
      judgements that were not made and why

    Without a judge — none provided, call failed, or verdict unverifiable —
    the deterministic result stands and every unmade judgement is listed.
    A manifest-backed work is then kept and a candidate not promoted; the
    report says plainly that the role question was left unanswered rather
    than pretending it was settled.

    Never infers governance requirements from absence of practices.
    """
    survey = RepositorySurvey()
    findings: List[SurveyFinding] = []

    # Deterministic pass: boundaries the filesystem declares in a form we parse
    module_boundaries = _discover_module_boundaries(project_root)
    deterministic_modules = [
        _build_module(project_root, module_id, paths)
        for module_id, paths in module_boundaries
    ]

    # Arithmetic: source-dense directories that declare themselves in a form
    # we do not parse.  Proposed here, decided by judgement or left alone.
    claimed = {paths[0] for _mid, paths in module_boundaries if paths}
    candidates = _discover_undeclared_candidates(project_root, claimed)

    subjects = _build_subjects(project_root, deterministic_modules, candidates)
    if subjects:
        dossier = {
            "repository": _root_context(project_root),
            "subjects": subjects,
        }
        verdicts_by_id, agent_consulted, batch_reason = _consult_judge(
            judge, judge_mode, dossier
        )
    else:
        verdicts_by_id, agent_consulted, batch_reason = {}, False, ""

    records: List[JudgementRecord] = []
    unmade: List[UnmadeJudgement] = []
    for subject in subjects:
        if batch_reason:
            unmade.append(
                UnmadeJudgement(
                    subject["id"], subject["kind"], subject["path"], batch_reason
                )
            )
            continue
        record, missing = _apply_verdict(
            project_root, subject, verdicts_by_id.get(subject["id"])
        )
        if record is not None:
            records.append(record)
        if missing is not None:
            unmade.append(missing)

    survey.agent_consulted = agent_consulted
    survey.judgements = records
    survey.unmade_judgements = unmade

    # Compose the module list from the deterministic pass and the judgements
    scaffolding_paths = {
        record.subject_path
        for record in records
        if record.kind == KIND_BOUNDARY_ROLE and record.verdict == "scaffolding"
    }
    accepted_boundaries = [
        record
        for record in records
        if record.kind == KIND_UNDECLARED_BOUNDARY and record.verdict == "module"
    ]
    final_modules = [
        module
        for module in deterministic_modules
        if module.paths and module.paths[0] not in scaffolding_paths
    ]
    for record in accepted_boundaries:
        final_modules.append(
            _build_module(
                project_root,
                Path(record.subject_path).name,
                [record.subject_path],
                origin="agent-judgement",
                extra_evidence=[
                    f"Boundary accepted by agent judgement: {record.reason} "
                    f"(cited: {', '.join(record.cited_files)})"
                ],
            )
        )
    survey.modules = final_modules

    if module_boundaries:
        findings.append(
            SurveyFinding(
                message="Multiple modules detected from workspace declarations and nested manifests.",
                evidence=[f"Found {len(module_boundaries)} module boundary(s)"],
            )
        )
    if scaffolding_paths:
        findings.append(
            SurveyFinding(
                message="Manifest-backed works classified as scaffolding, not product modules.",
                evidence=[
                    f"{record.subject_path}: {record.reason} "
                    f"(cited: {', '.join(record.cited_files)})"
                    for record in records
                    if record.subject_path in scaffolding_paths
                ],
            )
        )
    for record in accepted_boundaries:
        findings.append(
            SurveyFinding(
                message=f"Undeclared boundary accepted as a module: {record.subject_path}",
                evidence=[
                    record.reason,
                    f"cited: {', '.join(record.cited_files)}",
                ],
            )
        )
    if unmade:
        findings.append(
            SurveyFinding(
                message=(
                    "Boundary judgements were not made for every candidate; "
                    "the deterministic result stands for the subjects listed."
                ),
                evidence=[
                    f"{u.subject_path} ({u.kind}): {u.reason}" for u in unmade
                ],
            )
        )

    if not final_modules:
        # Single package repository
        languages = _detect_languages(project_root)
        if languages:
            survey.languages = languages
            findings.append(
                SurveyFinding(
                    message="Single-package repository detected.",
                    evidence=[f"Languages: {', '.join(languages)}"],
                )
            )

        # Detect test command at repository level
        test_cmd, marker = _detect_test_command(project_root)
        if test_cmd:
            survey.test_command = test_cmd
            survey.test_marker_file = marker
            findings.append(
                SurveyFinding(
                    message=f"Test command confirmed from {marker}",
                    evidence=[f"Command: {test_cmd}"],
                )
            )

    # Detect decision paths
    decision_paths = _detect_decision_paths(project_root)
    if decision_paths:
        survey.decision_paths = decision_paths
        findings.append(
            SurveyFinding(
                message="Decision records directory found",
                evidence=decision_paths,
            )
        )

    survey.findings = findings
    return survey
