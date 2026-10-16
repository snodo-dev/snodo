"""Canaries and repository integration for the documentation fragment gate."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "enforce_docs_anchors", REPO_ROOT / "scripts/enforce_docs_anchors.py"
)
anchors = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(anchors)


def make_repo(tmp_path, files):
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: Test\nmarkdown_extensions:\n  - toc:\n      permalink: true\n"
        "  - tables\n  - pymdownx.superfences\n",
        encoding="utf-8",
    )
    for name, text in files.items():
        path = tmp_path / "docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_missing_heading_fails_and_existing_heading_passes(tmp_path, capsys):
    repo = make_repo(tmp_path, {
        "index.md": "# Home\n\n[execution](protocol.md#execution-configuration)\n",
        "protocol.md": "# Protocol\n",
    })
    assert anchors.main(["--repo", str(repo)]) == 1
    output = capsys.readouterr().out
    assert "docs/index.md:3:" in output
    assert "protocol.md#execution-configuration" in output
    (repo / "docs/protocol.md").write_text("## Execution configuration\n", encoding="utf-8")
    assert anchors.main(["--repo", str(repo)]) == 0


@pytest.mark.parametrize("heading,fragment", [
    ("## Hello, *World*! `code` & Stuff", "hello-world-code-stuff"),
    ("Setext heading\n==============", "setext-heading"),
    ("## Repeat\n\n## Repeat", "repeat_1"),
    ("## Caf\u00e9", "cafe"),
    ('<a id="explicit"></a>', "explicit"),
    ('<a name="legacy"></a>', "legacy"),
])
def test_site_anchor_rules(tmp_path, heading, fragment):
    repo = make_repo(tmp_path, {
        "nested/from.md": f"[go](../target.md#{fragment})\n",
        "target.md": heading + "\n",
    })
    assert anchors.check(repo) == []


@pytest.mark.parametrize("link", [
    "[go](target.md#missing)",
    "[go](<target.md#missing> 'title')",
    "[go][destination]\n\n[destination]: target.md#missing",
    "[go][]\n\n[go]: target.md#missing",
    "[go]\n\n[go]: target.md#missing",
    '<a href="target.md#missing">go</a>',
    "[a *formatted* label](target.md#missing)",
])
def test_link_forms_report_usage_line(tmp_path, link):
    repo = make_repo(tmp_path, {
        "index.md": "# Home\n\n" + link + "\n",
        "target.md": "# Target\n",
    })
    failures = anchors.check(repo)
    assert len(failures) == 1
    assert "docs/index.md:3:" in failures[0]
    assert "target.md#missing" in failures[0]


def test_same_page_encoded_fragments_and_queries(tmp_path):
    repo = make_repo(tmp_path, {
        "index.md": '# Home\n\n<a id="some:anchor"></a>\n\n'
        "[self](#home) [encoded](#some%3Aanchor) [query](index.md?q=1#home)\n",
    })
    assert anchors.check(repo) == []


def test_only_local_existing_document_fragments_are_checked(tmp_path):
    repo = make_repo(tmp_path, {"index.md": """# Home

[external](https://example.com/page.md#missing)
[protocol-relative](//example.com/page.md#missing)
[mail](mailto:test@example.com#missing)
[absolute](/index.md#missing)
[missing file](absent.md#missing)
[outside](../README.md#missing)
[asset](image.svg#missing)
[no fragment](index.md)
[empty fragment](index.md#)

`[code](index.md#missing)`

```markdown
[code](index.md#missing)
## Not a heading
```

<!-- [comment](index.md#missing) -->
"""})
    assert anchors.check(repo) == []


def test_code_heading_is_not_an_anchor_and_code_link_is_not_a_source_line(tmp_path):
    repo = make_repo(tmp_path, {"index.md": """# Home

```markdown
[example](#fake)
## Fake
```

[real](#fake)
"""})
    assert anchors.check(repo) == [
        "docs/index.md:8: link `#fake` names no anchor in docs/index.md"
    ]


def test_repeated_broken_links_report_each_line(tmp_path):
    repo = make_repo(tmp_path, {"index.md": "[one](#missing)\n\n[two](#missing)\n"})
    failures = anchors.check(repo)
    assert len(failures) == 2
    assert "index.md:1:" in failures[0]
    assert "index.md:3:" in failures[1]


def test_every_document_is_checked(tmp_path):
    repo = make_repo(tmp_path, {
        "index.md": "# Home\n",
        "specs/design.md": "[bad](../index.md#missing)\n",
        "decisions/001.md": "[bad](../index.md#missing)\n",
    })
    assert len(anchors.check(repo)) == 2


def test_repository_passes():
    assert anchors.check(REPO_ROOT) == []
