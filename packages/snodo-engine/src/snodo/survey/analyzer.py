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

A manifest inside a dotted directory (for example .opencode/) is tooling
configuration, not a module of the product, and is excluded. Files under
dependency and build trees (node_modules, dist, vendor, ...) are vendored,
not the repository's own source, and are excluded from language detection.

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
from typing import Callable, Dict, List, Optional, Set, Tuple

import tomllib
import yaml

from snodo.survey.models import ModuleInfo, RepositorySurvey, SurveyFinding

_logger = logging.getLogger(__name__)

# Workspace marker files and their significance
_WORKSPACE_MARKERS: List[Tuple[str, str]] = [
    ("package.json", "npm"),
    ("pyproject.toml", "python"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pom.xml", "maven"),
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


def analyze_repository(project_root: Path) -> RepositorySurvey:
    """Analyze a repository and discover observable facts.

    Returns a RepositorySurvey containing:
    - Discovered modules (from workspace declarations and nested manifests)
    - Languages per module (repository source only, no vendored trees)
    - Test commands (confirmed against marker file contents)
    - Decision record locations
    - Repository-level tooling

    Never infers governance requirements from absence of practices.
    """
    survey = RepositorySurvey()
    findings: List[SurveyFinding] = []

    # Discover module boundaries
    module_boundaries = _discover_module_boundaries(project_root)

    if module_boundaries:
        findings.append(
            SurveyFinding(
                message="Multiple modules detected from workspace declarations and nested manifests.",
                evidence=[f"Found {len(module_boundaries)} module boundary(s)"],
            )
        )

        for module_id, paths in module_boundaries:
            evidence: List[str] = []

            # Detect languages in this module
            languages = _detect_languages(project_root, paths)
            if languages:
                evidence.append(f"Languages: {', '.join(languages)}")

            # Detect test command for this module
            test_cmd, marker = _detect_test_command(project_root, paths)
            if test_cmd:
                evidence.append(f"Test command confirmed from {marker}")

            # Detect decisions path for this module
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

            module = ModuleInfo(
                module_id=module_id,
                paths=paths,
                languages=languages,
                test_command=test_cmd,
                test_marker_file=marker,
                decisions_path=decisions_path,
                evidence=evidence,
            )
            survey.modules.append(module)
    else:
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
