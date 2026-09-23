"""Include the repository's authoritative guide docs in built distributions."""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


GUIDE_MARKER = "<!-- snodo-guide"


def guide_files(root):
    return [path for path in Path(root).glob("*.md")
            if GUIDE_MARKER in path.read_text(encoding="utf-8")]


class BuildPyWithGuideDocs(build_py):
    def run(self):
        super().run()
        package_root = Path(__file__).parent
        staged_sources = package_root / "guide_sources"
        docs_root = staged_sources if staged_sources.is_dir() else package_root.parent.parent / "docs"
        target = Path(self.build_lib) / "snodo" / "mcp" / "guide_docs"
        target.mkdir(parents=True, exist_ok=True)
        for source in guide_files(docs_root):
            shutil.copy2(source, target / source.name)


class SdistWithGuideDocs(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        package_root = Path(__file__).parent
        docs_root = package_root.parent.parent / "docs"
        target = Path(base_dir) / "guide_sources"
        target.mkdir(parents=True, exist_ok=True)
        for source in guide_files(docs_root):
            shutil.copy2(source, target / source.name)


setup(cmdclass={"build_py": BuildPyWithGuideDocs, "sdist": SdistWithGuideDocs})
