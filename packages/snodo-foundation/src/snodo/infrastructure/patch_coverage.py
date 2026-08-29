"""Patch / diff coverage measurement and enforcement.

FILE: snodo/infrastructure/patch_coverage.py (Fixes #61)

Measures test coverage over lines touched by a change (git diff against base ref),
preventing 0%-coverage modules from passing global coverage thresholds.

Behaviors:
- Branch with several commits: diff measures <base_ref>..HEAD across all branch commits.
- Code deletion only / non-Python changes: 0 added executable lines -> 100% diff coverage (exempt).
- Uncovered added code: fails if patch coverage < min_patch_coverage (default: 80%).
"""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class LineDiffCoverage:
    """Coverage breakdown for changed lines in a single file."""
    file_path: str
    added_executable_lines: Set[int]
    covered_lines: Set[int]
    missed_lines: Set[int]

    @property
    def total_lines(self) -> int:
        return len(self.added_executable_lines)

    @property
    def coverage_percentage(self) -> float:
        if self.total_lines == 0:
            return 100.0
        return (len(self.covered_lines) / self.total_lines) * 100.0


@dataclass(frozen=True)
class PatchCoverageResult:
    """Result of patch coverage measurement over a diff."""
    base_ref: str
    total_executable_lines: int
    covered_executable_lines: int
    missed_executable_lines: int
    coverage_percentage: float
    file_coverages: List[LineDiffCoverage]
    missed_line_map: Dict[str, List[int]]


def _is_target_python_file(path_str: str) -> bool:
    """Return True if path_str is a source Python file to measure."""
    path_str = path_str.replace("\\", "/")
    if not path_str.endswith(".py"):
        return False
    # Ignore test files, studies, experiments, scratch
    ignore_prefixes = ("tests/", "studies/", "experiments/", "scratch/", "docs/")
    if any(path_str.startswith(prefix) or f"/{prefix}" in path_str for prefix in ignore_prefixes):
        return False
    return True


def parse_git_diff_added_lines(
    project_root: str,
    base_ref: Optional[str] = None,
    diff_text: Optional[str] = None,
) -> Tuple[str, Dict[str, Set[int]]]:
    """Parse git diff output to map file path -> set of 1-based added line numbers.

    If diff_text is provided, it is parsed directly. Otherwise git is invoked.
    """
    resolved_base = base_ref or "origin/main"

    if diff_text is None:
        root_path = Path(project_root).resolve()
        # Find merge-base or fall back
        try:
            mb_proc = subprocess.run(  # noqa: S603 - argv list, no shell; resolved_base (a ref) is one element, never interpreted
                ["git",  # noqa: S607 - git resolved from PATH by design
                 "merge-base", resolved_base, "HEAD"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if mb_proc.returncode == 0 and mb_proc.stdout.strip():
                resolved_base = mb_proc.stdout.strip()
            else:
                resolved_base = "HEAD~1"
        except Exception:
            resolved_base = "HEAD~1"

        try:
            diff_proc = subprocess.run(  # noqa: S603 - argv list, no shell; resolved_base is one element, never interpreted
                ["git",  # noqa: S607 - git resolved from PATH by design
                 "diff", "-U0", f"{resolved_base}..HEAD"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if diff_proc.returncode == 0:
                diff_text = diff_proc.stdout
            else:
                # Fallback to unstaged / staged working tree diff
                diff_proc = subprocess.run(
                    ["git",  # noqa: S607 - git resolved from PATH by design
                     "diff", "-U0", "HEAD"],
                    cwd=str(root_path),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                diff_text = diff_proc.stdout if diff_proc.returncode == 0 else ""
        except Exception:
            diff_text = ""

    added_lines: Dict[str, Set[int]] = {}
    current_file: Optional[str] = None
    current_line_no = 0

    hunk_header_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in (diff_text or "").splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            if not _is_target_python_file(current_file):
                current_file = None
            continue
        if line.startswith("--- "):
            continue

        if current_file is None:
            continue

        match = hunk_header_re.match(line)
        if match:
            current_line_no = int(match.group(1))
            continue

        if line.startswith("+") and not line.startswith("+++"):
            added_lines.setdefault(current_file, set()).add(current_line_no)
            current_line_no += 1
        elif not line.startswith("-"):
            current_line_no += 1

    return resolved_base, added_lines


def parse_coverage_xml(xml_content_or_path: str) -> Dict[str, Dict[int, bool]]:
    """Parse coverage.xml content or file path into file_path -> {line_num: covered_bool}."""
    path_or_xml = Path(xml_content_or_path)
    if path_or_xml.exists() and path_or_xml.is_file():
        tree = ET.parse(path_or_xml)  # noqa: S314 - XML is snodo's own pytest-cov coverage.xml emitted by the CI gate, not external input
        root = tree.getroot()
    else:
        root = ET.fromstring(xml_content_or_path)  # noqa: S314 - XML is snodo's own pytest-cov coverage.xml emitted by the CI gate, not external input

    coverage_map: Dict[str, Dict[int, bool]] = {}

    for pkg in root.findall(".//class"):
        filename = pkg.get("filename", "")
        if not filename:
            continue
        filename = filename.replace("\\", "/")

        line_map: Dict[int, bool] = {}
        for line in pkg.findall("./lines/line"):
            line_no = int(line.get("number", "0"))
            hits = int(line.get("hits", "0"))
            if line_no > 0:
                line_map[line_no] = hits > 0

        if line_map:
            coverage_map[filename] = line_map

    return coverage_map


def calculate_patch_coverage(
    added_lines_map: Dict[str, Set[int]],
    coverage_map: Dict[str, Dict[int, bool]],
    base_ref: str = "origin/main",
) -> PatchCoverageResult:
    """Calculate patch/diff coverage for added/modified executable lines."""
    total_exec = 0
    covered_exec = 0
    missed_exec = 0
    file_coverages: List[LineDiffCoverage] = []
    missed_line_map: Dict[str, List[int]] = {}

    for file_path, added_lines in added_lines_map.items():
        # Match file in coverage_map (handling relative paths)
        file_cov_map = None
        for cov_path, cov_lines in coverage_map.items():
            if cov_path == file_path or cov_path.endswith(f"/{file_path}") or file_path.endswith(f"/{cov_path}"):
                file_cov_map = cov_lines
                break

        if not file_cov_map:
            continue

        exec_added = set()
        covered = set()
        missed = set()

        for line_no in added_lines:
            if line_no in file_cov_map:
                exec_added.add(line_no)
                if file_cov_map[line_no]:
                    covered.add(line_no)
                else:
                    missed.add(line_no)

        if exec_added:
            total_exec += len(exec_added)
            covered_exec += len(covered)
            missed_exec += len(missed)
            file_coverages.append(
                LineDiffCoverage(
                    file_path=file_path,
                    added_executable_lines=exec_added,
                    covered_lines=covered,
                    missed_lines=missed,
                )
            )
            if missed:
                missed_line_map[file_path] = sorted(list(missed))

    percentage = 100.0 if total_exec == 0 else (covered_exec / total_exec) * 100.0

    return PatchCoverageResult(
        base_ref=base_ref,
        total_executable_lines=total_exec,
        covered_executable_lines=covered_exec,
        missed_executable_lines=missed_exec,
        coverage_percentage=percentage,
        file_coverages=file_coverages,
        missed_line_map=missed_line_map,
    )


def enforce_patch_coverage(
    result: PatchCoverageResult, min_patch_coverage: float = 80.0
) -> Tuple[bool, str]:
    """Enforce patch coverage threshold. Returns (passed, summary_message)."""
    if result.total_executable_lines == 0:
        return (
            True,
            "Patch coverage: 100.0% (N/A — no added executable Python lines in diff)",
        )

    if result.coverage_percentage >= min_patch_coverage:
        return (
            True,
            f"Patch coverage PASSED: {result.coverage_percentage:.1f}% "
            f"({result.covered_executable_lines}/{result.total_executable_lines} lines covered, "
            f"target: >={min_patch_coverage:.1f}%)",
        )

    details = []
    for file_path, lines in result.missed_line_map.items():
        line_str = ", ".join(str(line_num) for line_num in lines)
        details.append(f"  {file_path}: lines [{line_str}]")

    detail_msg = "\n".join(details)
    return (
        False,
        f"Patch coverage FAILED: {result.coverage_percentage:.1f}% is below target "
        f"{min_patch_coverage:.1f}% ({result.missed_executable_lines} uncovered line(s) in diff):\n{detail_msg}",
    )
