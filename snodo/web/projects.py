"""Read-only discovery of Snodo projects for the local web dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from snodo.infrastructure.paths import resolve_project_root


@dataclass(frozen=True)
class Project:
    """A discovered project, identified by its folder name and absolute path."""

    name: str
    path: Path


def discover_projects(roots: Iterable[str | Path], max_depth: int = 3) -> list[Project]:
    """Find projects below *roots*, without descending into a found project."""
    excluded = {"node_modules", ".venv", "build", "dist"}
    found: dict[Path, Project] = {}

    def visit(directory: Path, depth: int) -> None:
        try:
            if (directory / ".snodo").is_dir():
                resolved = directory.resolve()
                found.setdefault(resolved, Project(directory.name, resolved))
                return
            if depth >= max_depth:
                return
            children = sorted(
                (entry for entry in directory.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name,
            )
        except OSError:
            return

        for child in children:
            if child.name.startswith(".") or child.name in excluded:
                continue
            visit(child, depth + 1)

    for root in roots:
        visit(Path(root).expanduser(), 0)

    return sorted(found.values(), key=lambda project: project.name)


def resolve_projects(
    roots: Iterable[str | Path], cwd: str | Path
) -> list[Project]:
    """Discover configured roots, or resolve only the project containing cwd."""
    roots = list(roots)
    if roots:
        return discover_projects(roots)

    project_root = resolve_project_root(str(cwd))
    if project_root is None:
        return []
    path = Path(project_root).resolve()
    return [Project(path.name, path)]
