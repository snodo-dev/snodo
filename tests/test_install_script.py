from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from snodo.cli.banner import plain_wordmark


INSTALLER = Path(__file__).parents[1] / "docs" / "install.sh"
WORDMARK = (
    "╭──  ┌─╮  ╭─╮  ╭─┤  ╭─╮   ╭─┤  ╭─╮  ╷   ╷\n"
    "╰─╮  │ │  │ │  │ │  │ │   │ │  ├─╯  ╰╮ ╭╯\n"
    "──╯  ╵ ╵  ╰─╯  ╰─╯  ╰─╯ ▪ ╰─╯  ╰──   ╰─╯"
)


def test_installer_wordmark_matches_cli() -> None:
    source = INSTALLER.read_text()
    match = re.search(r"<<'WORDMARK'\n(.*?)\nWORDMARK", source, re.DOTALL)
    assert match is not None, "installer plain wordmark block was not found"
    installer_rows = match.group(1).splitlines()
    cli_rows = plain_wordmark().splitlines()[:3]
    cli_rows[2] = re.sub(r"  \S+$", "", cli_rows[2])
    assert installer_rows == cli_rows


def run_installer(tmp_path: Path, *, present: bool) -> tuple[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    marker = tmp_path / "installed"
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        f"if [ \"$1 $2 $3\" = 'tool install snodo' ] || [ \"$1 $2 $3\" = 'tool upgrade snodo' ]; then touch '{marker}'; "
        f"cat > '{bin_dir}/snodo' <<'SNODO'\n#!/bin/sh\necho 'snodo 2.0.0'\nSNODO\nchmod +x '{bin_dir}/snodo'; fi\n"
        "if [ \"$1\" = --version ]; then echo 'uv 0.9.0'; fi\n"
    )
    (bin_dir / "uv").chmod(0o755)
    (bin_dir / "snodo").write_text(
        "#!/bin/sh\n"
        f"if [ -f '{marker}' ]; then echo 'snodo 2.0.0'; else echo 'snodo 1.0.0'; fi\n"
    )
    (bin_dir / "snodo").chmod(0o755)
    if not present:
        (bin_dir / "snodo").unlink()
    env = os.environ.copy()
    env.update(PATH=f"{bin_dir}:/usr/bin:/bin", HOME=str(tmp_path), NO_COLOR="")
    env.pop("NO_COLOR")
    result = subprocess.run(
        ["sh", str(INSTALLER)], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout, calls.read_text()


def test_installs_when_snodo_is_missing(tmp_path: Path) -> None:
    output, calls = run_installer(tmp_path, present=False)
    assert calls.splitlines() == ["--version", "tool install snodo"]
    assert "Installed snodo 2.0.0" in output


def test_upgrades_existing_snodo(tmp_path: Path) -> None:
    output, calls = run_installer(tmp_path, present=True)
    assert calls.splitlines() == ["--version", "tool upgrade snodo"]
    assert "Updated snodo 1.0.0 → snodo 2.0.0" in output


def test_piped_output_is_plain_and_includes_wordmark(tmp_path: Path) -> None:
    output, _ = run_installer(tmp_path, present=False)
    assert "\x1b" not in output
    assert WORDMARK in output
