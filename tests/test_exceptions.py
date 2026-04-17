import pytest

from pydantic2linkml.exceptions import SlotUsageGenerationError


class TestSlotUsageGenerationError:
    """Tests for SlotUsageGenerationError sorting behavior."""

    @pytest.mark.parametrize(
        ("missing", "constraint", "expected_missing", "expected_constraint"),
        [
            (["Zeta", "alpha", "Beta"], None, ["alpha", "Beta", "Zeta"], []),
            (None, ["Zeta", "alpha", "Beta"], [], ["alpha", "Beta", "Zeta"]),
            (
                ["c", "a", "B"],
                ["Z", "x", "Y"],
                ["a", "B", "c"],
                ["x", "Y", "Z"],
            ),
            ({"b", "a"}, None, ["a", "b"], []),
        ],
    )
    def test_sorts_inputs(
        self, missing, constraint, expected_missing, expected_constraint
    ):
        err = SlotUsageGenerationError(
            missing_meta_slots=missing,
            varied_constraint_meta_slots=constraint,
        )
        assert err.missing_meta_slots == expected_missing
        assert err.varied_constraint_meta_slots == expected_constraint

    def test_raises_value_error_when_both_empty(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            SlotUsageGenerationError(
                missing_meta_slots=[], varied_constraint_meta_slots=[]
            )

    def test_raises_value_error_when_both_none(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            SlotUsageGenerationError()

    @pytest.mark.parametrize(
        ("missing", "constraint"),
        [
            (["c", "a"], None),
            (None, ["z", "x"]),
        ],
    )
    def test_str(self, missing, constraint):
        err = SlotUsageGenerationError(
            missing_meta_slots=missing,
            varied_constraint_meta_slots=constraint,
        )
        expected = (
            f"Target slot definition has missing meta slots, "
            f"{err.missing_meta_slots}, and varied constraint meta slots, "
            f"{err.varied_constraint_meta_slots}"
        )
        assert str(err) == expected

    @pytest.mark.parametrize(
        ("missing", "constraint"),
        [
            (["c", "a"], None),
            (None, ["z", "x"]),
        ],
    )
    def test_repr(self, missing, constraint):
        err = SlotUsageGenerationError(
            missing_meta_slots=missing,
            varied_constraint_meta_slots=constraint,
        )
        expected = (
            f"SlotUsageGenerationError"
            f"(missing_meta_slots={err.missing_meta_slots!r}, "
            f"varied_constraint_meta_slots={err.varied_constraint_meta_slots!r})"
        )
        assert repr(err) == expected
