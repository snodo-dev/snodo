from snodo.infrastructure.patch_coverage import (
    parse_git_diff_added_lines,
    parse_coverage_xml,
    calculate_patch_coverage,
    enforce_patch_coverage,
)


SAMPLE_DIFF = """diff --git a/packages/snodo-engine/src/snodo/feature.py b/packages/snodo-engine/src/snodo/feature.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/packages/snodo-engine/src/snodo/feature.py
@@ -0,0 +1,5 @@
+def helper():
+    x = 10
+    y = 20
+    return x + y
+
diff --git a/tests/test_feature.py b/tests/test_feature.py
--- /dev/null
+++ b/tests/test_feature.py
@@ -0,0 +1,2 @@
+def test_something():
+    pass
"""


SAMPLE_COVERAGE_XML = """<?xml version="1.0" ?>
<coverage version="7.1.0" timestamp="1234567890">
  <packages>
    <package name="snodo">
      <classes>
        <class filename="packages/snodo-engine/src/snodo/feature.py" name="feature.py">
          <lines>
            <line hits="1" number="1"/>
            <line hits="1" number="2"/>
            <line hits="0" number="3"/>
            <line hits="0" number="4"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def test_parse_git_diff_added_lines():
    """parse_git_diff_added_lines extracts added lines for target python files."""
    base, added = parse_git_diff_added_lines(".", diff_text=SAMPLE_DIFF)

    assert "packages/snodo-engine/src/snodo/feature.py" in added
    assert added["packages/snodo-engine/src/snodo/feature.py"] == {1, 2, 3, 4, 5}
    # Test files ignored
    assert "tests/test_feature.py" not in added


def test_parse_coverage_xml():
    """parse_coverage_xml extracts line hit counts per file."""
    cov = parse_coverage_xml(SAMPLE_COVERAGE_XML)

    assert "packages/snodo-engine/src/snodo/feature.py" in cov
    assert cov["packages/snodo-engine/src/snodo/feature.py"][1] is True
    assert cov["packages/snodo-engine/src/snodo/feature.py"][2] is True
    assert cov["packages/snodo-engine/src/snodo/feature.py"][3] is False
    assert cov["packages/snodo-engine/src/snodo/feature.py"][4] is False


def test_calculate_patch_coverage():
    """calculate_patch_coverage computes overall diff coverage percentage."""
    base, added = parse_git_diff_added_lines(".", diff_text=SAMPLE_DIFF)
    cov = parse_coverage_xml(SAMPLE_COVERAGE_XML)

    res = calculate_patch_coverage(added, cov, base)

    assert res.total_executable_lines == 4  # Lines 1,2,3,4
    assert res.covered_executable_lines == 2  # Lines 1,2
    assert res.missed_executable_lines == 2  # Lines 3,4
    assert res.coverage_percentage == 50.0
    assert "packages/snodo-engine/src/snodo/feature.py" in res.missed_line_map
    assert res.missed_line_map["packages/snodo-engine/src/snodo/feature.py"] == [3, 4]


def test_enforce_patch_coverage_pass_and_fail():
    """enforce_patch_coverage enforces target threshold and reports missed lines."""
    base, added = parse_git_diff_added_lines(".", diff_text=SAMPLE_DIFF)
    cov = parse_coverage_xml(SAMPLE_COVERAGE_XML)
    res = calculate_patch_coverage(added, cov, base)

    # 50.0% coverage fails min_patch_coverage=80.0%
    ok, msg = enforce_patch_coverage(res, min_patch_coverage=80.0)
    assert ok is False
    assert "Patch coverage FAILED: 50.0%" in msg
    assert "packages/snodo-engine/src/snodo/feature.py: lines [3, 4]" in msg

    # min_patch_coverage=40.0% passes
    ok_pass, msg_pass = enforce_patch_coverage(res, min_patch_coverage=40.0)
    assert ok_pass is True
    assert "Patch coverage PASSED: 50.0%" in msg_pass


def test_code_deletion_only_is_exempt():
    """Code deletion / no added python lines diff is 100% (exempt)."""
    deletion_diff = """diff --git a/packages/snodo-engine/src/snodo/old.py b/packages/snodo-engine/src/snodo/old.py
--- a/packages/snodo-engine/src/snodo/old.py
+++ b/packages/snodo-engine/src/snodo/old.py
@@ -10,3 +0,0 @@
-def old_func():
-    pass
"""
    base, added = parse_git_diff_added_lines(".", diff_text=deletion_diff)
    cov = parse_coverage_xml(SAMPLE_COVERAGE_XML)
    res = calculate_patch_coverage(added, cov, base)

    assert res.total_executable_lines == 0
    assert res.coverage_percentage == 100.0

    ok, msg = enforce_patch_coverage(res, min_patch_coverage=80.0)
    assert ok is True
    assert "no added executable Python lines" in msg
