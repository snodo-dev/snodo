"""Readiness assessment engine — derives scaffolding requirements from compiled protocol.

FILE: snodo/readiness/checker.py

Readiness is a property of the method scaffolding relative to the configured
protocol, never of the codebase.

Derives checks dynamically from the compiled Protocol:
- Architecture validators -> decision records committed in git
- Quality validators -> resolvable test command (in protocol or committed marker file)
- Path citations in validator criteria -> paths present and committed in git
- Coder configurations -> configuration files committed in git
- Workstation checks (binaries on PATH, environment variables) -> reported separately, unscored

Ordering: cheapest fix at highest severity first.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from git import Repo

from snodo.compiler.models import Protocol
from snodo.readiness.models import (
    FindingSeverity,
    ReadinessAssessment,
    ReadinessFinding,
    ReadinessKind,
)

# Supported marker files for quality test runner auto-detection
_TEST_MARKERS: List[Tuple[str, str]] = [
    ("package.json", "npm test"),
    ("pyproject.toml", "pytest"),
    ("setup.py", "pytest"),
    ("setup.cfg", "pytest"),
    ("Cargo.toml", "cargo test"),
    ("Makefile", "make test"),
    ("go.mod", "go test ./..."),
]

# Path regex to extract plausible relative file/dir citations from text
_PATH_TOKEN_REGEX = re.compile(
    r'(?<![a-zA-Z0-9_\-\./])'
    r'([a-zA-Z0-9_\-]+/(?:[a-zA-Z0-9_\-\./]+)?|[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})'
    r'(?![a-zA-Z0-9_\-\./])'
)

# Common non-path file-like words to ignore
_PATH_IGNORE_TOKENS = {
    "1.0.0", "0.1.0", "v1.0", "v2.0", "node:test", "node:assert",
    "http:", "https:", "git:", "file:", "true", "false", "null",
}


def _get_git_repo(project_root: Path) -> Optional[Repo]:
    """Return GitPython Repo instance if inside a git repository, else None."""
    try:
        return Repo(str(project_root), search_parent_directories=True)
    except Exception:
        return None


def _is_path_committed(repo: Optional[Repo], rel_path: str) -> bool:
    """Return True if rel_path (file or directory) has at least one committed file in HEAD."""
    if repo is None:
        return False
    try:
        if not repo.head.is_valid():
            return False
        norm = rel_path.rstrip("/\\")
        output = repo.git.ls_tree("-r", "--name-only", "HEAD", norm)
        return bool(output.strip())
    except Exception:
        return False


def _has_committed_markdown_files(repo: Optional[Repo], rel_dir: str) -> bool:
    """Return True if rel_dir has at least one committed .md file in HEAD."""
    if repo is None:
        return False
    try:
        if not repo.head.is_valid():
            return False
        norm = rel_dir.rstrip("/\\")
        output = repo.git.ls_tree("-r", "--name-only", "HEAD", norm)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return any(line.endswith(".md") for line in lines)
    except Exception:
        return False


def _extract_cited_paths(text: str) -> List[str]:
    """Extract plausible repository relative file and directory paths from criteria strings."""
    paths: List[str] = []
    for match in _PATH_TOKEN_REGEX.finditer(text):
        token = match.group(1).strip()
        if token in _PATH_IGNORE_TOKENS or token.startswith((".", "/", "\\")):
            continue
        # Filter for file/directory shapes: contains / or ends with common code/doc extension
        if "/" in token or re.search(r'\.(md|py|json|yml|yaml|toml|ts|js|rs|go|sh|txt|cfg)$', token, re.I):
            paths.append(token.rstrip(".,;:)'\""))
    return paths


def _resolve_model_provider_env(model_name: str) -> Optional[Tuple[str, str]]:
    """Return (env_var_name, provider_name) for a given model string, or None."""
    if not model_name:
        return None
    m = model_name.lower()
    if m.startswith(("claude", "anthropic")):
        return ("ANTHROPIC_API_KEY", "Anthropic")
    if m.startswith(("gpt", "o1", "o3", "openai")):
        return ("OPENAI_API_KEY", "OpenAI")
    if m.startswith(("gemini", "google")):
        return ("GEMINI_API_KEY", "Google Gemini")
    if m.startswith("deepseek"):
        return ("DEEPSEEK_API_KEY", "DeepSeek")
    if m.startswith("openrouter"):
        return ("OPENROUTER_API_KEY", "OpenRouter")
    return None


def assess_readiness(
    project_root: Path,
    protocol: Protocol,
) -> ReadinessAssessment:
    """Assess method scaffolding readiness of project_root relative to protocol.

    Derives all checks dynamically from the compiled protocol.
    Returns a ReadinessAssessment containing the scored repository figure and
    ordered findings (cheapest fix at highest severity first).
    """
    repo = _get_git_repo(project_root)
    all_mode_ids = [m.mode_id for m in protocol.modes]

    # Map each validator to the modes that activate it
    validator_modes: Dict[str, List[str]] = {}
    for mode in protocol.modes:
        for v_id in mode.validators:
            validator_modes.setdefault(v_id, []).append(mode.mode_id)

    for val in protocol.validators:
        if val.validator_id not in validator_modes:
            validator_modes[val.validator_id] = all_mode_ids

    repository_findings: List[ReadinessFinding] = []
    workstation_findings: List[ReadinessFinding] = []
    total_repo_checks = 0

    # ──────────────────────────────────────────────────────────────────────────
    # Check 1: Protocol file committed in git
    # ──────────────────────────────────────────────────────────────────────────
    total_repo_checks += 1
    protocol_rel = ".snodo/protocol.yml"
    protocol_on_disk = (project_root / protocol_rel).exists()
    protocol_committed = _is_path_committed(repo, protocol_rel)

    if not protocol_committed:
        if protocol_on_disk:
            repository_findings.append(
                ReadinessFinding(
                    id="protocol_uncommitted",
                    kind=ReadinessKind.REPOSITORY,
                    severity=FindingSeverity.WARN,
                    modes=all_mode_ids,
                    description=f"Protocol file '{protocol_rel}' exists in working tree but is uncommitted in git HEAD.",
                    remediation=f"git add {protocol_rel} && git commit -m 'chore: commit protocol'",
                    fix_cost=1,
                )
            )
        else:
            repository_findings.append(
                ReadinessFinding(
                    id="protocol_missing",
                    kind=ReadinessKind.REPOSITORY,
                    severity=FindingSeverity.BLOCKER,
                    modes=all_mode_ids,
                    description=f"Protocol file '{protocol_rel}' does not exist.",
                    remediation="Run 'snodo init' to generate a protocol file",
                    fix_cost=2,
                )
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 2: Architecture Validator / Decision Records
    # ──────────────────────────────────────────────────────────────────────────
    architecture_validators = [
        v for v in protocol.validators
        if v.validator_type == "architecture" or any(
            "decision" in c.lower() or "adr" in c.lower() for c in v.criteria
        )
    ]

    for val in architecture_validators:
        total_repo_checks += 1
        modes = validator_modes.get(val.validator_id, all_mode_ids)
        decisions_dir = project_root / "docs" / "decisions"
        has_committed_decisions = _has_committed_markdown_files(repo, "docs/decisions")

        if not has_committed_decisions:
            disk_md_files = (
                list(decisions_dir.glob("*.md")) if decisions_dir.is_dir() else []
            )
            if disk_md_files:
                repository_findings.append(
                    ReadinessFinding(
                        id=f"architecture_decisions_uncommitted:{val.validator_id}",
                        kind=ReadinessKind.REPOSITORY,
                        severity=FindingSeverity.BLOCKER,
                        modes=modes,
                        description=(
                            f"Validator '{val.validator_id}' ({val.validator_type}) requires recorded decisions. "
                            f"Decision record(s) exist in 'docs/decisions/' on disk but are uncommitted in git HEAD; "
                            "task worktrees will not see them."
                        ),
                        remediation="git add docs/decisions/*.md && git commit -m 'docs: commit decision records'",
                        fix_cost=1,
                    )
                )
            else:
                repository_findings.append(
                    ReadinessFinding(
                        id=f"architecture_decisions_missing:{val.validator_id}",
                        kind=ReadinessKind.REPOSITORY,
                        severity=FindingSeverity.BLOCKER,
                        modes=modes,
                        description=(
                            f"Validator '{val.validator_id}' ({val.validator_type}) requires recorded decisions, "
                            "but no decision records exist in 'docs/decisions/'."
                        ),
                        remediation="Create 'docs/decisions/' and commit initial decision records (e.g. docs/decisions/001-init.md)",
                        fix_cost=2,
                    )
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 3: Quality Validator / Test Command Resolution
    # ──────────────────────────────────────────────────────────────────────────
    quality_validators = [v for v in protocol.validators if v.validator_type == "quality"]

    for val in quality_validators:
        total_repo_checks += 1
        modes = validator_modes.get(val.validator_id, all_mode_ids)
        tooling_cmd = val.tooling.get("test_command") if val.tooling else None

        # Check for committed marker files
        detected_marker_cmd = None
        detected_marker_file = None
        uncommitted_marker_file = None

        for marker_file, default_cmd in _TEST_MARKERS:
            if _is_path_committed(repo, marker_file):
                detected_marker_cmd = default_cmd
                detected_marker_file = marker_file
                break
            elif (project_root / marker_file).exists():
                uncommitted_marker_file = marker_file
                detected_marker_cmd = default_cmd

        effective_cmd = tooling_cmd or detected_marker_cmd

        if tooling_cmd:
            pass  # Explicit test_command in protocol satisfies tree readiness
        elif detected_marker_file:
            pass  # Committed marker file satisfies tree readiness
        elif uncommitted_marker_file:
            repository_findings.append(
                ReadinessFinding(
                    id=f"quality_marker_uncommitted:{val.validator_id}",
                    kind=ReadinessKind.REPOSITORY,
                    severity=FindingSeverity.BLOCKER,
                    modes=modes,
                    description=(
                        f"Quality validator '{val.validator_id}' has no tooling.test_command "
                        f"and marker file '{uncommitted_marker_file}' is uncommitted in git HEAD."
                    ),
                    remediation=f"git add {uncommitted_marker_file} && git commit or configure tooling.test_command in protocol.yml",
                    fix_cost=1,
                )
            )
        else:
            repository_findings.append(
                ReadinessFinding(
                    id=f"quality_test_command_unresolvable:{val.validator_id}",
                    kind=ReadinessKind.REPOSITORY,
                    severity=FindingSeverity.BLOCKER,
                    modes=modes,
                    description=(
                        f"Quality validator '{val.validator_id}' cannot resolve a test command: "
                        "no tooling.test_command configured and no test marker file detected in git tree."
                    ),
                    remediation="Set tooling.test_command in protocol.yml (e.g. test_command: 'pytest') or commit a project marker file",
                    fix_cost=2,
                )
            )

        # Workstation Check: is the runner executable on PATH?
        if effective_cmd:
            runner_binary = effective_cmd.strip().split()[0]
            if not shutil.which(runner_binary):
                workstation_findings.append(
                    ReadinessFinding(
                        id=f"quality_runner_missing_on_path:{runner_binary}",
                        kind=ReadinessKind.WORKSTATION,
                        severity=FindingSeverity.WARN,
                        modes=modes,
                        description=(
                            f"Test runner binary '{runner_binary}' for validator '{val.validator_id}' "
                            "is not found on workstation PATH."
                        ),
                        remediation=f"Install '{runner_binary}' or ensure it is available on PATH",
                        fix_cost=3,
                    )
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 4: Paths Cited in Criteria and Constraints
    # ──────────────────────────────────────────────────────────────────────────
    seen_cited_paths: Set[str] = set()
    for val in protocol.validators:
        modes = validator_modes.get(val.validator_id, all_mode_ids)
        combined_text = " ".join(val.criteria) + " " + " ".join(
            c.description + " " + c.expression for c in val.constraints
        )
        for cited_path in _extract_cited_paths(combined_text):
            # Exclude docs/decisions (handled by architecture check) and protocol
            if cited_path.startswith("docs/decisions") or cited_path == ".snodo/protocol.yml":
                continue
            if cited_path in seen_cited_paths:
                continue
            seen_cited_paths.add(cited_path)

            total_repo_checks += 1
            if _is_path_committed(repo, cited_path):
                continue
            elif (project_root / cited_path).exists():
                repository_findings.append(
                    ReadinessFinding(
                        id=f"cited_path_uncommitted:{cited_path}",
                        kind=ReadinessKind.REPOSITORY,
                        severity=FindingSeverity.WARN,
                        modes=modes,
                        description=(
                            f"Path '{cited_path}' cited in criteria of validator '{val.validator_id}' "
                            "exists on disk but is uncommitted in git HEAD."
                        ),
                        remediation=f"git add {cited_path} && git commit",
                        fix_cost=1,
                    )
                )
            else:
                repository_findings.append(
                    ReadinessFinding(
                        id=f"cited_path_missing:{cited_path}",
                        kind=ReadinessKind.REPOSITORY,
                        severity=FindingSeverity.WARN,
                        modes=modes,
                        description=(
                            f"Path '{cited_path}' cited in criteria of validator '{val.validator_id}' "
                            "does not exist in repository."
                        ),
                        remediation=f"Create and commit '{cited_path}'",
                        fix_cost=2,
                    )
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 5: Coder Configuration & Binaries & Credentials
    # ──────────────────────────────────────────────────────────────────────────
    checked_models: Set[str] = set()

    for mode in protocol.modes:
        coder_name = (mode.coder or "").lower()
        mode_id = mode.mode_id

        # Tree checks for coder configs
        if coder_name == "agy":
            for agy_cfg in [".agy", ".gemini"]:
                if (project_root / agy_cfg).exists():
                    total_repo_checks += 1
                    if not _is_path_committed(repo, agy_cfg):
                        repository_findings.append(
                            ReadinessFinding(
                                id=f"coder_config_uncommitted:{coder_name}",
                                kind=ReadinessKind.REPOSITORY,
                                severity=FindingSeverity.WARN,
                                modes=[mode_id],
                                description=f"Coder configuration '{agy_cfg}' for agy exists on disk but is uncommitted in git HEAD.",
                                remediation=f"git add {agy_cfg} && git commit",
                                fix_cost=1,
                            )
                        )
            # Workstation check for agy binary
            if not shutil.which("agy"):
                workstation_findings.append(
                    ReadinessFinding(
                        id="coder_binary_missing:agy",
                        kind=ReadinessKind.WORKSTATION,
                        severity=FindingSeverity.WARN,
                        modes=[mode_id],
                        description=f"Antigravity CLI ('agy') configured for mode '{mode_id}' is not found on PATH.",
                        remediation="Install agy: https://antigravity.google/docs/cli",
                        fix_cost=3,
                    )
                )

        elif coder_name == "opencode-cli":
            for opencode_cfg in ["opencode.json", "opencode.toml"]:
                if (project_root / opencode_cfg).exists():
                    total_repo_checks += 1
                    if not _is_path_committed(repo, opencode_cfg):
                        repository_findings.append(
                            ReadinessFinding(
                                id=f"coder_config_uncommitted:{coder_name}",
                                kind=ReadinessKind.REPOSITORY,
                                severity=FindingSeverity.WARN,
                                modes=[mode_id],
                                description=f"Coder configuration '{opencode_cfg}' for opencode-cli exists on disk but is uncommitted in git HEAD.",
                                remediation=f"git add {opencode_cfg} && git commit",
                                fix_cost=1,
                            )
                        )
            if not shutil.which("opencode"):
                workstation_findings.append(
                    ReadinessFinding(
                        id="coder_binary_missing:opencode",
                        kind=ReadinessKind.WORKSTATION,
                        severity=FindingSeverity.WARN,
                        modes=[mode_id],
                        description=f"OpenCode CLI ('opencode') configured for mode '{mode_id}' is not found on PATH.",
                        remediation="Install opencode: curl -fsSL https://opencode.ai/install | bash",
                        fix_cost=3,
                    )
                )

        elif coder_name == "opencode":
            if not shutil.which("docker"):
                workstation_findings.append(
                    ReadinessFinding(
                        id="coder_binary_missing:docker",
                        kind=ReadinessKind.WORKSTATION,
                        severity=FindingSeverity.WARN,
                        modes=[mode_id],
                        description=f"Docker required for opencode container coder in mode '{mode_id}' is not found on PATH.",
                        remediation="Install and start Docker",
                        fix_cost=3,
                    )
                )

        # Model credential checks (workstation, unscored)
        model_str = mode.coder_config.get("model") if mode.coder_config else None
        if model_str and model_str not in checked_models:
            checked_models.add(model_str)
            prov_env = _resolve_model_provider_env(model_str)
            if prov_env:
                env_var, prov_name = prov_env
                if env_var not in os.environ and not (env_var == "GEMINI_API_KEY" and "GOOGLE_API_KEY" in os.environ):
                    workstation_findings.append(
                        ReadinessFinding(
                            id=f"credential_missing:{env_var}",
                            kind=ReadinessKind.WORKSTATION,
                            severity=FindingSeverity.WARN,
                            modes=[mode_id],
                            description=f"Environment variable '{env_var}' for {prov_name} model '{model_str}' is not set on workstation.",
                            remediation=f"export {env_var}='<your-api-key>'",
                            fix_cost=3,
                        )
                    )

    # Check validator models
    for val in protocol.validators:
        if val.model and val.model not in checked_models:
            checked_models.add(val.model)
            prov_env = _resolve_model_provider_env(val.model)
            if prov_env:
                env_var, prov_name = prov_env
                if env_var not in os.environ and not (env_var == "GEMINI_API_KEY" and "GOOGLE_API_KEY" in os.environ):
                    val_modes = validator_modes.get(val.validator_id, all_mode_ids)
                    workstation_findings.append(
                        ReadinessFinding(
                            id=f"credential_missing:{env_var}",
                            kind=ReadinessKind.WORKSTATION,
                            severity=FindingSeverity.WARN,
                            modes=val_modes,
                            description=f"Environment variable '{env_var}' for {prov_name} model '{val.model}' (validator '{val.validator_id}') is not set on workstation.",
                            remediation=f"export {env_var}='<your-api-key>'",
                            fix_cost=3,
                        )
                    )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 6: Execution Prepare Command (Workstation)
    # ──────────────────────────────────────────────────────────────────────────
    prepare_cmd = getattr(protocol.execution, "prepare_command", None)
    if prepare_cmd:
        prep_binary = prepare_cmd.strip().split()[0]
        if not shutil.which(prep_binary):
            workstation_findings.append(
                ReadinessFinding(
                    id=f"prepare_command_binary_missing:{prep_binary}",
                    kind=ReadinessKind.WORKSTATION,
                    severity=FindingSeverity.WARN,
                    modes=all_mode_ids,
                    description=f"Execution prepare_command binary '{prep_binary}' is not found on workstation PATH.",
                    remediation=f"Install '{prep_binary}' or add it to PATH",
                    fix_cost=3,
                )
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Score calculation (Repository Scaffolding only)
    # ──────────────────────────────────────────────────────────────────────────
    passed_repo_checks = max(0, total_repo_checks - len(repository_findings))
    if total_repo_checks > 0:
        score = round((passed_repo_checks / total_repo_checks) * 100)
    else:
        score = 100

    return ReadinessAssessment(
        protocol_id=protocol.protocol_id,
        score=score,
        total_checks=total_repo_checks,
        passed_checks=passed_repo_checks,
        repository_findings=repository_findings,
        workstation_findings=workstation_findings,
    )
