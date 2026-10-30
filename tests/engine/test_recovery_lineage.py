import pytest

from snodo.engine.recovery_lineage import belongs_to_recovery_lineage


@pytest.mark.parametrize(
    ("reference", "root_id", "expected"),
    [
        ("1.1_a", "1.1_a", True),
        ("1.1_a_fix_1", "1.1_a", True),
        ("1.1_a_fix_27", "1.1_a", True),
        ("other", "1.1_a", False),
        ("1.1_ab_fix_1", "1.1_a", False),
        ("1.1_a_fix_", "1.1_a", False),
        ("1.1_a_fix_x", "1.1_a", False),
        ("1.1_a_fix_0", "1.1_a", False),
        ("1.1_a_fix_1_extra", "1.1_a", False),
        ("1.1_A_fix_1", "1.1_a", False),
        ("", "1.1_a", False),
        (None, "1.1_a", False),
        (123, "1.1_a", False),
    ],
)
def test_belongs_to_recovery_lineage(reference, root_id, expected):
    assert belongs_to_recovery_lineage(reference, root_id) is expected
