"""Repository analysis engine for discovering module boundaries and tooling.

FILE: snodo/survey/analyzer.py

Discovers observable facts about an existing repository:
- Module boundaries from workspace markers (package.json, pyproject.toml, etc.)
- Languages per module
- Test commands from marker files or explicit configuration
- Documentation and decision record locations
- Repository-level tooling

Marker files used for module boundary discovery:
- package.json (Node.js/npm workspaces)
- pyproject.toml (Python workspaces)
- Cargo.toml (Rust workspaces)
- go.mod (Go modules)
- pom.xml (Maven modules)
- Project files (.idea/modules.xml for JetBrains)

Test command detection from marker files:
- package.json → npm test
- pyproject.toml / setup.py / setup.cfg → pytest
- Cargo.toml → cargo test
- Makefile → make test
- go.mod → go test ./...

Never infers protocol requirements from absence of practices.
"""

import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

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

# Test command detection rules (same as readiness checker)
_TEST_MARKERS: List[Tuple[str, str]] = [
    ("package.json", "npm test"),
    ("pyproject.toml", "pytest"),
    ("setup.py", "pytest"),
    ("setup.cfg", "pytest"),
    ("Cargo.toml", "cargo test"),
    ("Makefile", "make test"),
    ("go.mod", "go test ./..."),
]

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


def _discover_module_boundaries(project_root: Path) -> List[Tuple[str, List[str]]]:
    """Discover module boundaries from workspace markers.

    Returns list of (module_id, paths) tuples.
    """
    modules: List[Tuple[str, List[str]]] = []

    # NPM workspaces
    npm_ws = _detect_npm_workspaces(project_root)
    for i, ws_path in enumerate(npm_ws):
        module_id = Path(ws_path).name or f"npm_module_{i}"
        modules.append((module_id, [ws_path]))

    # Python workspaces
    py_ws = _detect_python_workspaces(project_root)
    for i, ws_path in enumerate(py_ws):
        module_id = Path(ws_path).name or f"python_module_{i}"
        modules.append((module_id, [ws_path]))

    # Rust workspaces
    cargo_ws = _detect_cargo_workspaces(project_root)
    for cargo_path in cargo_ws:
        module_id = Path(cargo_path).name or "rust_module"
        modules.append((module_id, [cargo_path]))

    # Go workspaces
    go_ws = _detect_go_workspaces(project_root)
    for go_path in go_ws:
        module_id = Path(go_path).name or "go_module"
        modules.append((module_id, [go_path]))

    return modules


def _detect_languages(project_root: Path, paths: Optional[List[str]] = None) -> List[str]:
    """Detect programming languages in a directory or list of paths."""
    extensions: Set[str] = set()
    search_paths = [project_root] if not paths else [project_root / p for p in paths]

    for search_path in search_paths:
        if not search_path.exists():
            continue
        for ext, lang_set in _LANGUAGE_EXTS.items():
            for file in search_path.rglob("*"):
                if file.suffix in lang_set:
                    extensions.add(ext)
                    break

    return sorted(extensions)


def _detect_test_command(project_root: Path, paths: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str]]:
    """Detect test command from marker files.

    Returns (test_command, marker_file) or (None, None).
    """
    search_paths = [project_root] if not paths else [project_root / p for p in paths]

    for marker_file, test_cmd in _TEST_MARKERS:
        for search_path in search_paths:
            if not search_path.exists():
                continue
            marker_path = search_path / marker_file if marker_file != "Makefile" else project_root / marker_file
            if marker_path.exists():
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
    - Discovered modules (from workspace markers)
    - Languages per module
    - Test commands
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
                message="Multiple modules detected from workspace configuration.",
                evidence=[f"Found {len(module_boundaries)} workspace(s)"],
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
                evidence.append(f"Test command detected from {marker}")

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
                    message=f"Test command detected from {marker}",
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
