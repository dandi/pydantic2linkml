import pytest

from pydantic2linkml.exceptions import SlotUsageGenerationError


class TestSlotUsageGenerationError:
    """Tests for SlotUsageGenerationError sorting behavior."""

    def test_sorts_missing_meta_slots(self):
        err = SlotUsageGenerationError(missing_meta_slots=["Zeta", "alpha", "Beta"])
        assert err.missing_meta_slots == ["alpha", "Beta", "Zeta"]
        assert err.varied_constraint_meta_slots == []

    def test_sorts_varied_constraint_meta_slots(self):
        err = SlotUsageGenerationError(
            varied_constraint_meta_slots=["Zeta", "alpha", "Beta"]
        )
        assert err.missing_meta_slots == []
        assert err.varied_constraint_meta_slots == ["alpha", "Beta", "Zeta"]

    def test_sorts_both_lists(self):
        err = SlotUsageGenerationError(
            missing_meta_slots=["c", "a", "B"],
            varied_constraint_meta_slots=["Z", "x", "Y"],
        )
        assert err.missing_meta_slots == ["a", "B", "c"]
        assert err.varied_constraint_meta_slots == ["x", "Y", "Z"]

    def test_accepts_set(self):
        err = SlotUsageGenerationError(
            missing_meta_slots={"b", "a"},
        )
        assert err.missing_meta_slots == ["a", "b"]

    def test_accepts_generator(self):
        def gen():
            yield "c"
            yield "a"

        err = SlotUsageGenerationError(missing_meta_slots=gen())
        assert err.missing_meta_slots == ["a", "c"]

    def test_raises_value_error_when_both_empty(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            SlotUsageGenerationError(
                missing_meta_slots=[], varied_constraint_meta_slots=[]
            )

    def test_raises_value_error_when_both_none(self):
        with pytest.raises(ValueError, match="must be non-empty"):
            SlotUsageGenerationError()

    @pytest.mark.parametrize(
        ("missing", "constraint", "expected_str"),
        [
            (
                ["c", "a"],
                None,
                "Target slot definition has missing meta slots, "
                "['a', 'c'], and varied constraint meta slots, []",
            ),
            (
                None,
                ["z", "x"],
                "Target slot definition has missing meta slots, "
                "[], and varied constraint meta slots, ['x', 'z']",
            ),
        ],
    )
    def test_str(self, missing, constraint, expected_str):
        err = SlotUsageGenerationError(
            missing_meta_slots=missing,
            varied_constraint_meta_slots=constraint,
        )
        assert str(err) == expected_str

    @pytest.mark.parametrize(
        ("missing", "constraint", "expected_repr"),
        [
            (
                ["c", "a"],
                None,
                "SlotUsageGenerationError"
                "(missing_meta_slots=['a', 'c'], "
                "varied_constraint_meta_slots=[])",
            ),
            (
                None,
                ["z", "x"],
                "SlotUsageGenerationError"
                "(missing_meta_slots=[], "
                "varied_constraint_meta_slots=['x', 'z'])",
            ),
        ],
    )
    def test_repr(self, missing, constraint, expected_repr):
        err = SlotUsageGenerationError(
            missing_meta_slots=missing,
            varied_constraint_meta_slots=constraint,
        )
        assert repr(err) == expected_repr
