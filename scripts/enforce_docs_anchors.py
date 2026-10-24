#!/usr/bin/env python
"""Check fragments of local documentation links, leaving file checks to MkDocs.

Render with the site's Python-Markdown extensions rather than maintaining a
second heading slugger or link grammar. This covers reference links, HTML,
Setext headings, inline formatting and duplicate heading IDs as the site does.
Every Markdown file under docs/ is checked; there is no exclusion or baseline.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

import yaml

from markdown import Markdown


class Page(HTMLParser):
    """The IDs and link destinations actually emitted by Markdown."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: set[str] = set()
        self.links: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.anchors.add(attributes["id"])
        if tag == "a":
            if attributes.get("name"):
                self.anchors.add(attributes["name"])
            if attributes.get("href") is not None:
                self.links.append(attributes["href"])

    handle_startendtag = handle_starttag


class Renderer:
    def __init__(self, repo_root: Path) -> None:
        config = yaml.safe_load((repo_root / "mkdocs.yml").read_text(encoding="utf-8"))
        self.extensions = []
        self.configs = {}
        for extension in config.get("markdown_extensions", []):
            if isinstance(extension, dict):
                name, settings = next(iter(extension.items()))
                self.extensions.append(name)
                self.configs[name] = settings or {}
            else:
                self.extensions.append(extension)

    def markdown(self) -> Markdown:
        return Markdown(extensions=self.extensions, extension_configs=self.configs)

    def render(self, text: str) -> tuple[Page, dict]:
        md = self.markdown()
        return Page(md.convert(text)), md.references

    def link_line(self, text: str, href: str, occurrence: int, references: dict) -> int:
        """Locate the source line where a rendered link is completed.

        Only failures need this slower source mapping. Render prefixes with
        the full reference table (including forward definitions). Work backward
        because an incomplete fence can temporarily render its examples as
        links; the final transition, not the first, identifies the real link.
        """
        lines = text.splitlines(keepends=True)
        for end in range(len(lines) - 1, -1, -1):
            md = self.markdown()
            md.references.update(references)
            page = Page(md.convert("".join(lines[:end])))
            if page.links.count(href) < occurrence:
                return end + 1
        return 1


def local_target(source: Path, href: str, docs: Path) -> tuple[Path, str] | None:
    """Only fragments on existing Markdown pages within docs are our remit."""
    try:
        url = urlsplit(href)
    except ValueError:
        return None  # Unrecognized links are already a strict-build error.
    if url.scheme or url.netloc or not url.fragment or url.path.startswith("/"):
        return None
    target = (source.parent / unquote(url.path)).resolve() if url.path else source
    if not target.is_relative_to(docs) or target.suffix.lower() != ".md":
        return None
    if not target.is_file():
        return None  # MkDocs owns missing files, not this check.
    return target, unquote(url.fragment)


def check(repo_root: Path) -> list[str]:
    repo_root = repo_root.resolve()
    docs = repo_root / "docs"
    renderer = Renderer(repo_root)
    texts = {path: path.read_text(encoding="utf-8") for path in sorted(docs.rglob("*.md"))}
    pages = {path: renderer.render(text) for path, text in texts.items()}
    failures: list[str] = []
    for source, (page, references) in pages.items():
        occurrences: Counter[str] = Counter()
        for href in page.links:
            occurrences[href] += 1
            resolved = local_target(source, href, docs)
            if resolved is None:
                continue
            target, fragment = resolved
            target_page = pages.get(target)
            if target_page is None:
                target_page = renderer.render(target.read_text(encoding="utf-8"))
            if fragment in target_page[0].anchors:
                continue
            line = renderer.link_line(texts[source], href, occurrences[href], references)
            failures.append(
                f"{source.relative_to(repo_root).as_posix()}:{line}: "
                f"link `{href}` names no anchor in {target.relative_to(repo_root).as_posix()}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    args = parser.parse_args(argv)
    failures = check(Path(args.repo))
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        print(f"Documentation anchor check failed: {len(failures)} violation(s).")
        return 1
    print("Docs anchors OK: 0 dangling local fragments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
