"""Packaging metadata for the installed web dashboard."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_web_server_dependencies_are_declared_without_other_changes() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    dependencies = project["project"]["dependencies"]
    assert "starlette>=1.8.0" in dependencies
    assert "uvicorn>=0.54.0" in dependencies
    assert [
        dependency
        for dependency in dependencies
        if dependency not in {"starlette>=1.8.0", "uvicorn>=0.54.0"}
    ] == [
        "snodo-core==0.28.3",
        "snodo-tools==0.28.3",
        "snodo-foundation==0.28.3",
        "snodo-engine==0.28.3",
        "snodo-mcp==0.28.3",
        "snodo-provider-github==0.28.3",
        "pydantic>=2.12.0",
        "pyyaml>=6.0",
        "typer>=0.9.0",
        "textual>=1.0.0",
        "httpx>=0.27.0",
        "GitPython>=3.1.0",
        "litellm>=1.106",
        "rich>=13.0.0",
    ]


def test_web_static_assets_are_in_package_data() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    package_data = project["tool"]["setuptools"]["package-data"]["snodo"]
    assert "web/static/*" in package_data
