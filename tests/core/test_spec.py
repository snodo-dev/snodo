"""Spec text helpers shared by the retry path at every layer.

FILE: tests/core/test_spec.py

The retry path has to answer "does this text change the specification?" in the
CLI, in the engine and in the MCP surface, and a layer that answers it
differently is a layer that either reports a replacement that did not happen or
misses one that did. These are the answers, tested once.
"""

from snodo.core.spec import same_spec, spec_text, spec_with_guidance


class TestSpecText:
    def test_missing_and_blank_are_both_empty(self):
        assert spec_text(None) == ""
        assert spec_text("") == ""
        assert spec_text("   \n\t ") == ""

    def test_surrounding_whitespace_is_trimmed(self):
        assert spec_text("  do the thing \n") == "do the thing"

    def test_non_strings_are_treated_as_absent(self):
        assert spec_text(123) == ""


class TestSameSpec:
    def test_layout_differences_do_not_make_a_different_spec(self):
        assert same_spec("add  a\n limiter", "add a limiter")
        assert same_spec("  add a limiter \n", "add a limiter")

    def test_different_words_are_a_different_spec(self):
        assert not same_spec("add a limiter", "add a cache")

    def test_missing_is_not_the_same_as_written(self):
        assert not same_spec("add a limiter", None)
        assert same_spec(None, "")


class TestSpecWithGuidance:
    def test_guidance_joins_the_spec_and_keeps_it_first(self):
        combined = spec_with_guidance("the spec", "and also, note this")
        assert combined == "the spec\n\nand also, note this"
        assert combined.startswith("the spec")

    def test_no_spec_means_the_guidance_is_the_spec(self):
        assert spec_with_guidance("", "note this") == "note this"
        assert spec_with_guidance(None, "note this") == "note this"

    def test_no_guidance_leaves_the_spec_alone(self):
        assert spec_with_guidance("the spec", "") == "the spec"
        assert spec_with_guidance("the spec", None) == "the spec"

    def test_nothing_at_all_is_nothing(self):
        assert spec_with_guidance(None, None) == ""
