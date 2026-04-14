from collections.abc import Iterable
from datetime import date, datetime, time
from enum import Enum
from functools import partial
from typing import Annotated, Literal, Optional, Union
from unittest.mock import call
from uuid import UUID

import pytest
from linkml_runtime.linkml_model import SlotDefinition
from linkml_runtime.linkml_model.meta import AnonymousSlotExpression
from pydantic import (
    UUID3,
    UUID4,
    AfterValidator,
    AnyUrl,
    BaseModel,
    BeforeValidator,
    Field,
    PlainValidator,
    StringConstraints,
    UrlConstraints,
    WrapValidator,
    condate,
    conlist,
)

from pydantic2linkml.gen_linkml import LinkmlGenerator, SlotGenerator, ANY_CLASS_DEF
from pydantic2linkml.tools import (
    fetch_defs,
    get_all_modules,
    get_field_schema,
    get_uuid_regex,
)

TRANSLATOR_PACKAGE = "pydantic2linkml"


def has_exactly_one_truthy(iterable: Iterable) -> bool:
    """
    Determine if exactly one element in an iterable is truthy.

    :param iterable: The iterable
    """
    return sum(map(bool, iterable)) == 1


def in_exactly_one_string(substr: str, str_lst: list[str]) -> bool:
    """
    Determine if exactly one string in a list of strings contains a given substring.

    :param substr: The substring
    :param str_lst: The list of strings
    :return: Whether exactly one string contains the substring
    """
    return has_exactly_one_truthy(substr in note for note in str_lst)


def in_no_string(substr: str, str_lst: list[str]) -> bool:
    """
    Determine if no string in a list of strings contains a given substring.

    :param substr: The substring
    :param str_lst: The list of strings
    :return: Whether no string contains the substring
    """
    return not any(substr in note for note in str_lst)


def verify_str_lst(
    substr: str,
    condition: bool,
    str_lst: list[str],
):
    """
    Verify the presence of a substring in exactly one of the strings in a given list
    if a given condition is met and the absence of the substring in all the strings
    in the list if the condition is not met.

    :param substr: The substring
    :param condition: The condition
    :param str_lst: The list of strings
    """
    if condition:
        assert in_exactly_one_string(substr, str_lst)
    else:
        assert in_no_string(substr, str_lst)


def translate_field_to_slot(model: type[BaseModel], fn: str) -> SlotDefinition:
    """
    Translate a field of a Pydantic model to a LinkML slot definition

    :param model: The Pydantic model
    :param fn: The field name of the field to be translated
    """
    return SlotGenerator(get_field_schema(model, fn)).generate()


@pytest.fixture
def models_and_enums(request) -> tuple[set[type[BaseModel]], set[type[Enum]]]:
    """
    Fixture to fetch Pydantic models and enums from named modules and their submodules
    """
    module_names: list[str] = request.param
    return fetch_defs(get_all_modules(module_names))


@pytest.fixture
def linkml_generator(models_and_enums) -> LinkmlGenerator:
    """
    Fixture to obtain a `LinkmlGenerator` object for named modules and their submodules
    """
    return LinkmlGenerator(models=models_and_enums[0], enums=models_and_enums[1])


class TestLinkmlGenerator:
    @pytest.mark.parametrize(
        "models_and_enums",
        [
            ["dandischema.models"],
            # === Remove all tests associated with aind_data_schema until works for
            # dandischema is complete ===
            pytest.param(
                ["aind_data_schema.components.coordinates"], marks=pytest.mark.xfail
            ),
            # Naming conflict at this one
            pytest.param(
                ["aind_data_schema.components.devices"], marks=pytest.mark.xfail
            ),
            pytest.param(
                ["aind_data_schema.components.reagent"], marks=pytest.mark.xfail
            ),
            pytest.param(
                ["aind_data_schema.components.stimulus"], marks=pytest.mark.xfail
            ),
            pytest.param(["aind_data_schema.components.tile"], marks=pytest.mark.xfail),
            pytest.param(
                ["aind_data_schema.core.acquisition"], marks=pytest.mark.xfail
            ),
            pytest.param(
                ["aind_data_schema.core.data_description"], marks=pytest.mark.xfail
            ),
            pytest.param(["aind_data_schema.core.instrument"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.metadata"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.procedures"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.processing"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.rig"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.session"], marks=pytest.mark.xfail),
            pytest.param(["aind_data_schema.core.subject"], marks=pytest.mark.xfail),
            # TODO: Add test cases with list containing multiple module names
        ],
        indirect=True,
    )
    def test_instantiation_with_definitions_in_module(self, models_and_enums):
        """
        Test instantiation of a `LinkmlGenerator` object with Pydantic models and enums
            from named modules and their submodules
        """
        LinkmlGenerator(models=models_and_enums[0], enums=models_and_enums[1])

    # noinspection PyTestParametrized
    @pytest.mark.parametrize(
        "models_and_enums", [["dandischema.models"]], indirect=True
    )
    def test_generate(self, linkml_generator):
        linkml_generator.generate()

    def test_class_description_from_docstring(self):
        """
        Test that a model with a docstring produces a ClassDefinition with the
        correct description, and a model without a docstring produces
        description=None.
        """
        from tests.assets.mock_module0 import A, B

        models = [A, B]
        generator = LinkmlGenerator(models=models, enums=[])
        schema = generator.generate()

        class_a = schema.classes["A"]
        assert class_a.description == "Model A docstring."

        class_b = schema.classes["B"]
        assert class_b.description is None

    def test_overriding_fields_considered_in_top_level_slot(self):
        """
        Regression test: when a subclass overrides a field with a different
        type, the top-level slot must not keep the range of the base class's
        field. The overriding field occurrences should also participate in the
        consistency check performed by `_add_slot`.
        """

        class Base(BaseModel):
            x: int

        class Sub(Base):
            x: str  # type: ignore[assignment]

        generator = LinkmlGenerator(models=[Base, Sub], enums=[])
        schema = generator.generate()

        # The top-level slot should not claim a consistent range because
        # Base.x is int and Sub.x is str.
        assert schema.slots["x"].range is None


class TestSlotGenerator:
    def test_instantiation(self):

        class Foo(BaseModel):
            x: int

        field_schema = get_field_schema(Foo, "x")

        slot_gen = SlotGenerator(field_schema)

        assert slot_gen._slot.name == "x"
        assert slot_gen._field_schema == field_schema

        # Test the _schema_type_to_method mapping at selective keys
        assert slot_gen._schema_type_to_method["any"] == slot_gen._any_schema
        assert slot_gen._schema_type_to_method["bool"] == slot_gen._bool_schema
        assert slot_gen._schema_type_to_method["model"] == slot_gen._model_schema

        assert not slot_gen._used

    def test_any_schema(self):
        from typing import Any

        class Foo(BaseModel):
            x: Any

        slot = translate_field_to_slot(Foo, "x")
        assert slot.range == "Any"

    def test_none_schema(self):

        class Foo(BaseModel):
            x: None

        slot = translate_field_to_slot(Foo, "x")
        assert len(slot.notes) == 1
        assert (
            slot.notes[0] == f"{TRANSLATOR_PACKAGE}: LinkML does not have null values. "
            f"(For details, see https://github.com/orgs/linkml/discussions/1975)."
        )

    def test_bool_schema(self):

        class Foo(BaseModel):
            x: bool

        slot = translate_field_to_slot(Foo, "x")
        assert slot.range == "boolean"

    @pytest.mark.parametrize(
        ("multiple_of", "le", "lt", "ge", "gt", "expected_max", "expected_min"),
        [
            (2, 100, 101, 0, -1, 100, 0),
            (2, 100, 200, -10, -100, 100, -10),
            (2, 200, 100, -100, -10, 99, -9),
            (None, 200, 100, -100, -10, 99, -9),
            (2, 200, None, -100, None, 200, -100),
            (2, None, 100, None, -10, 99, -9),
            (None, None, None, None, None, None, None),
        ],
    )
    def test_int_schema(self, multiple_of, le, lt, ge, gt, expected_max, expected_min):
        class Foo(BaseModel):
            x: int = Field(..., multiple_of=multiple_of, le=le, ge=ge, lt=lt, gt=gt)

        slot = translate_field_to_slot(Foo, "x")

        assert slot.range == "integer"

        if multiple_of is not None:
            assert len(slot.notes) == 1
            assert f"a multiple of {multiple_of}" in slot.notes[0]
        else:
            assert len(slot.notes) == 0

        assert slot.maximum_value == expected_max
        assert slot.minimum_value == expected_min

    @pytest.mark.parametrize("allow_inf_nan", [True, False, None])
    @pytest.mark.parametrize("multiple_of", [2, 42, None])
    @pytest.mark.parametrize("le", [100, -11, None])
    @pytest.mark.parametrize("ge", [10, -42, None])
    @pytest.mark.parametrize("lt", [10, -11, None])
    @pytest.mark.parametrize("gt", [100, -120, None])
    def test_float_schema(self, allow_inf_nan, multiple_of, le, ge, lt, gt):
        class Foo(BaseModel):
            x: float = Field(
                ...,
                allow_inf_nan=allow_inf_nan,
                multiple_of=multiple_of,
                le=le,
                ge=ge,
                lt=lt,
                gt=gt,
            )

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "float"
        verify_notes(
            "LinkML does not have support for `'+inf'`, `'-inf'`, and `'NaN'`",
            allow_inf_nan is None or allow_inf_nan,
        )
        verify_notes(f"multiple of {multiple_of}", multiple_of is not None)
        assert slot.maximum_value == le
        assert slot.minimum_value == ge
        verify_notes(f"less than {lt}", lt is not None)
        verify_notes(f"greater than {gt}", gt is not None)

    @pytest.mark.parametrize(
        ("allow_inf_nan", "max_digits", "decimal_places"),
        [(True, None, None), (False, 3, 42), (None, 2, 4), (False, None, 2)],
    )
    @pytest.mark.parametrize("multiple_of", [2, 42, None])
    @pytest.mark.parametrize("le", [100, -11, None])
    @pytest.mark.parametrize("ge", [10, -42, None])
    @pytest.mark.parametrize("lt", [10, -11, None])
    @pytest.mark.parametrize("gt", [100, -120, None])
    def test_decimal_schema(
        self, allow_inf_nan, max_digits, decimal_places, multiple_of, le, ge, lt, gt
    ):
        from decimal import Decimal

        class Foo(BaseModel):
            x: Decimal = Field(
                ...,
                allow_inf_nan=allow_inf_nan,
                multiple_of=multiple_of,
                le=le,
                ge=ge,
                lt=lt,
                gt=gt,
                max_digits=max_digits,
                decimal_places=decimal_places,
            )

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "decimal"
        verify_notes(
            "LinkML does not have support for `'+inf'`, `'-inf'`, and `'NaN'`",
            allow_inf_nan,
        )
        verify_notes(f"max number of {max_digits} digits", max_digits is not None)
        verify_notes(
            f"max number of {decimal_places} decimal places", decimal_places is not None
        )
        verify_notes(f"multiple of {multiple_of}", multiple_of is not None)
        assert slot.maximum_value == le
        assert slot.minimum_value == ge
        verify_notes(f"less than {lt}", lt is not None)
        verify_notes(f"greater than {gt}", gt is not None)

    @pytest.mark.parametrize(
        ("pattern", "max_length", "min_length", "output_pattern"),
        [
            (None, 10, 4, r"^(?=.{4,10}$)"),
            (None, None, 4, r"^(?=.{4,}$)"),
            (None, 10, None, r"^(?=.{,10}$)"),
            (r"^[a-zA-Z0-9]+", 3, 2, r"^(?=.{2,3}$)[a-zA-Z0-9]+"),
            (r"^[a-zA-Z0-9]+", None, 2, r"^(?=.{2,}$)[a-zA-Z0-9]+"),
            (r"^[a-zA-Z0-9]+", 3, None, r"^(?=.{,3}$)[a-zA-Z0-9]+"),
            (ptrn := r"^[a-zA-Z0-9]+", None, None, ptrn),
            (r".*", 10, 4, r"^(?=.{4,10}$).*"),
            (r".*", None, 4, r"^(?=.{4,}$).*"),
            (r".*", 10, None, r"^(?=.{,10}$).*"),
            (ptrn := r".*", None, None, ptrn),
            (None, None, None, None),
        ],
    )
    @pytest.mark.parametrize("strip_whitespace", [True, False, None])
    @pytest.mark.parametrize("to_lower", [True, False, None])
    @pytest.mark.parametrize("to_upper", [True, False, None])
    def test_str_schema(
        self,
        pattern,
        max_length,
        min_length,
        strip_whitespace,
        to_lower,
        to_upper,
        output_pattern,
    ):
        class Foo(BaseModel):
            # noinspection PyTypeHints
            x: Annotated[
                str,
                StringConstraints(
                    pattern=pattern,
                    max_length=max_length,
                    min_length=min_length,
                    strip_whitespace=strip_whitespace,
                    to_lower=to_lower,
                    to_upper=to_upper,
                ),
            ]

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "string"
        assert slot.pattern == output_pattern
        verify_notes(
            f"The max length constraint of {max_length} is incorporated",
            max_length is not None,
        )
        verify_notes(
            f"The min length constraint of {min_length} is incorporated",
            min_length is not None,
        )
        verify_notes(
            "stripping leading and trailing whitespace in LinkML",
            strip_whitespace,
        )
        verify_notes(
            "Unable to express the option of converting the string to lowercase",
            to_lower,
        )
        verify_notes(
            "Unable to express the option of converting the string to uppercase",
            to_upper,
        )

    @pytest.mark.parametrize("le", [date(2022, 1, 1), None])
    @pytest.mark.parametrize("ge", [date(2022, 2, 1), None])
    @pytest.mark.parametrize("lt", [date(2022, 3, 1), None])
    @pytest.mark.parametrize("gt", [date(2000, 1, 4), None])
    @pytest.mark.parametrize("now_op", ["past", "future", None])
    @pytest.mark.parametrize("now_utc_offset", [10, -20, None])
    def test_date_schema(self, le, ge, lt, gt, now_op, now_utc_offset):
        class Foo(BaseModel):
            x: condate(le=le, ge=ge, lt=lt, gt=gt)

        field_schema = get_field_schema(Foo, "x")

        # There is no interface for end users to set values for
        # "now_op" and "now_utc_offset" keys.
        # Here, we manually set them in the schema directly.
        if now_op is not None:
            field_schema.schema["now_op"] = now_op
        if now_utc_offset is not None:
            field_schema.schema["now_utc_offset"] = now_utc_offset

        slot = SlotGenerator(field_schema).generate()
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "date"
        verify_notes(
            "Unable to express the restriction of being less than or equal to",
            le is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than or equal to",
            ge is not None,
        )
        verify_notes(
            "Unable to express the restriction of being less than a date",
            lt is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than a date",
            gt is not None,
        )
        verify_notes(
            "Unable to express the restriction of being before or after",
            now_op is not None,
        )
        verify_notes(
            "Unable to express the utc offset of the current date in the restriction",
            now_utc_offset is not None,
        )

    @pytest.mark.parametrize("le", [time(12, 0, 0), None])
    @pytest.mark.parametrize("ge", [time(14, 0, 0), None])
    @pytest.mark.parametrize("lt", [time(15, 30, 0), None])
    @pytest.mark.parametrize("gt", [time(10, 15, 0), None])
    @pytest.mark.parametrize("tz_constraint", ["aware", 42, None])
    @pytest.mark.parametrize("microseconds_precision", ["error", None])
    def test_time_schema(self, le, ge, lt, gt, tz_constraint, microseconds_precision):
        class Foo(BaseModel):
            x: time = Field(le=le, ge=ge, lt=lt, gt=gt)

        field_schema = get_field_schema(Foo, "x")

        # There is no interface for end users to set values for
        # the "tz_constraint" and "microseconds_precision" keys.
        # Here, we manually set them in the schema directly.
        if tz_constraint is not None:
            field_schema.schema["tz_constraint"] = tz_constraint
        if microseconds_precision is not None:
            field_schema.schema["microseconds_precision"] = microseconds_precision

        slot = SlotGenerator(field_schema).generate()
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "time"
        verify_notes(
            "Unable to express the restriction of being less than or equal to",
            le is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than or equal to",
            ge is not None,
        )
        verify_notes(
            "Unable to express the restriction of being less than a time",
            lt is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than a time",
            gt is not None,
        )
        verify_notes(
            f"Unable to express the timezone constraint of {tz_constraint}",
            tz_constraint is not None,
        )
        verify_notes(
            f"Unable to express the microseconds precision constraint of "
            f"{microseconds_precision}",
            microseconds_precision is not None,
        )

    @pytest.mark.parametrize("le", [datetime(2022, 1, 1, 12, 0, 0), None])
    @pytest.mark.parametrize("ge", [datetime(2022, 2, 1, 14, 0, 0), None])
    @pytest.mark.parametrize("lt", [datetime(2044, 3, 1, 15, 30, 0), None])
    @pytest.mark.parametrize("gt", [datetime(2000, 1, 4, 10, 15, 0), None])
    @pytest.mark.parametrize("now_op", ["future", None])
    @pytest.mark.parametrize("tz_constraint", ["native", 42, None])
    @pytest.mark.parametrize("now_utc_offset", [10, -20, None])
    @pytest.mark.parametrize("microseconds_precision", ["truncate", "error", None])
    def test_datetime_schema(
        self,
        le,
        ge,
        lt,
        gt,
        now_op,
        tz_constraint,
        now_utc_offset,
        microseconds_precision,
    ):
        class Foo(BaseModel):
            x: datetime = Field(le=le, ge=ge, lt=lt, gt=gt)

        field_schema = get_field_schema(Foo, "x")

        # There is no interface for end users to set values for
        # the "now_op", "tz_constraint", "now_utc_offset",
        # and "microseconds_precision" keys.
        # Here, we manually set them in the schema directly.
        if now_op is not None:
            field_schema.schema["now_op"] = now_op
        if tz_constraint is not None:
            field_schema.schema["tz_constraint"] = tz_constraint
        if now_utc_offset is not None:
            field_schema.schema["now_utc_offset"] = now_utc_offset
        if microseconds_precision is not None:
            field_schema.schema["microseconds_precision"] = microseconds_precision

        slot = SlotGenerator(field_schema).generate()
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "datetime"
        verify_notes(
            "Unable to express the restriction of being less than or equal to",
            le is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than or equal to",
            ge is not None,
        )
        verify_notes(
            "Unable to express the restriction of being less than a datetime",
            lt is not None,
        )
        verify_notes(
            "Unable to express the restriction of being greater than a datetime",
            gt is not None,
        )
        verify_notes(
            "Unable to express the restriction of being before or after",
            now_op is not None,
        )
        verify_notes(
            f"Unable to express the timezone constraint of {tz_constraint}",
            tz_constraint is not None,
        )
        verify_notes(
            "Unable to express the utc offset of the current datetime "
            "in the restriction",
            now_utc_offset is not None,
        )
        if microseconds_precision != "truncate":
            verify_notes(
                f"Unable to express the microseconds precision constraint of "
                f"{microseconds_precision}",
                microseconds_precision is not None,
            )

    @pytest.mark.parametrize(
        ("literal_specs", "are_literals_supported", "expected_slot_attrs"),
        [
            # Single string literal
            (
                Literal["hello"],
                True,
                {"range": "string", "equals_string": "hello"},
            ),
            # Single integer literal
            (
                Literal[42],
                True,
                {"range": "integer", "equals_number": 42},
            ),
            # Multiple string literals
            (
                Literal["hello", "world"],
                True,
                {
                    "range": "string",
                    "any_of": [
                        AnonymousSlotExpression(equals_string="hello"),
                        AnonymousSlotExpression(equals_string="world"),
                    ],
                },
            ),
            # Multiple integer literals
            (
                Literal[1, 2, 3],
                True,
                {
                    "range": "integer",
                    "any_of": [
                        AnonymousSlotExpression(equals_number=1),
                        AnonymousSlotExpression(equals_number=2),
                        AnonymousSlotExpression(equals_number=3),
                    ],
                },
            ),
            # Mixed string and integer literals
            (
                Literal[4, "hello", 1, "you"],
                True,
                {
                    "range": "Any",
                    "any_of": [
                        AnonymousSlotExpression(range="integer", equals_number=4),
                        AnonymousSlotExpression(range="string", equals_string="hello"),
                        AnonymousSlotExpression(range="integer", equals_number=1),
                        AnonymousSlotExpression(range="string", equals_string="you"),
                    ],
                },
            ),
            # Unsupported: contains `None`
            (Literal[4, "hello", 1, None, "you"], False, None),
            # Unsupported: `bool` literal
            (Literal[True], False, None),
        ],
    )
    def test_literal_schema(
        self, literal_specs, are_literals_supported, expected_slot_attrs
    ):

        class Foo(BaseModel):
            x: literal_specs

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        verify_notes(
            "Unable to express the restriction of being one of the elements",
            not are_literals_supported,
        )

        if are_literals_supported:
            assert slot.range == expected_slot_attrs["range"]
            assert slot.equals_string == expected_slot_attrs.get("equals_string")
            assert slot.equals_number == expected_slot_attrs.get("equals_number")
            assert slot.any_of == expected_slot_attrs.get("any_of", [])
        else:
            # The `range` and `any_of` meta slots should be unset
            assert slot.range is None
            assert len(slot.any_of) == 0

    @pytest.mark.parametrize(
        ("enum_cls", "missing_call_back"),
        [
            (Enum("Greeting", "HELLO GOODBYE"), None),
            (Enum("Color", "GREEN BLUE RED"), lambda x: x + 1),
        ],
    )
    def test_enum_schema(self, enum_cls, missing_call_back):

        class Foo(BaseModel):
            x: enum_cls

        field_schema = get_field_schema(Foo, "x")

        # There is no interface for end users to set values for
        # the "missing" key. Here, we manually set it in the schema directly.
        if missing_call_back is not None:
            field_schema.schema["missing"] = missing_call_back

        slot = SlotGenerator(field_schema).generate()
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == enum_cls.__name__
        verify_notes(
            "Unable to express calling "
            f"{missing_call_back.__name__ if missing_call_back else ''}",
            missing_call_back is not None,
        )

    @pytest.mark.parametrize(
        ("item_type", "is_item_type_list_type", "expected_range"),
        [
            (int, False, "integer"),
            (list[int], True, None),
            (str, False, "string"),
            (list[list[str]], True, None),
        ],
    )
    @pytest.mark.parametrize(
        ("min_len", "max_len"), [(1, 10), (0, 5), (None, 11), (3, None), (None, None)]
    )
    def test_list_schema(
        self, item_type, is_item_type_list_type, expected_range, min_len, max_len
    ):
        class Foo(BaseModel):
            x: conlist(item_type, min_length=min_len, max_length=max_len)

        slot = translate_field_to_slot(Foo, "x")

        if is_item_type_list_type:
            assert in_exactly_one_string("Translation is incomplete", slot.notes)
        else:
            assert in_no_string("Translation is incomplete", slot.notes)
            assert slot.multivalued
            assert slot.minimum_cardinality == (
                # This is needed due to Pydantic's behavior:
                # The any argument for `min_length` of `conlist` that is less than
                # or equal to 0 is ignored.
                None if (min_len is not None and min_len <= 0) else min_len
            )
            assert slot.maximum_cardinality == max_len
            assert slot.range == expected_range

    def test_dict_schema(self):
        class Foo(BaseModel):
            x: dict[int, str]

        slot = translate_field_to_slot(Foo, "x")

        assert in_exactly_one_string("`dict` types are yet to be supported", slot.notes)

    def test_function_after_schema(self):
        def validator_func(v):
            return v

        self._test_function_schema(AfterValidator(validator_func), "after", True)

    def test_function_before_schema(self):
        def validator_func(v):
            return v

        self._test_function_schema(BeforeValidator(validator_func), "before", True)

    def test_function_wrap_schema(self):
        def validator_func(v, _handler):
            return v

        self._test_function_schema(WrapValidator(validator_func), "wrap", True)

    def test_function_plain_schema(self):
        def validator_func(v):
            return v

        self._test_function_schema(PlainValidator(validator_func), "plain", False)

    @staticmethod
    def _test_function_schema(
        validator: Union[
            AfterValidator, BeforeValidator, WrapValidator, PlainValidator
        ],
        validator_type: Literal["after", "before", "wrap", "plain"],
        translation_propagated: bool,
    ):
        """
        Helper function called by tests for translation of function schemas
        """

        class Foo(BaseModel):
            x: Annotated[int, validator]

        slot = translate_field_to_slot(Foo, "x")

        assert in_exactly_one_string(
            "Unable to translate the logic contained "
            f"in the {validator_type} validation function, {validator.func!r}",
            slot.notes,
        )

        if translation_propagated:
            # Verify the translation is properly propagated to the next level
            assert slot.range == "integer"
        else:
            # Verify the translation is not propagated to the next level
            assert slot.range is None

    @pytest.mark.parametrize(
        (
            "apply_optional",
            "default",
            "default_type",
            "type_supported",
            "default_factory",
            "expected_ifabsent",
            "expected_range",
        ),
        [
            (False, True, bool, True, None, "True", "boolean"),
            (True, False, bool, True, None, "False", "boolean"),
            (False, 42, int, True, None, "int(42)", "integer"),
            (True, 42, int, True, None, "int(42)", "integer"),
            (False, "Hello", str, True, None, "string(Hello)", "string"),
            (False, 3.14, float, True, None, "float(3.14)", "float"),
            (False, date(2022, 2, 1), date, True, None, "date(2022-02-01)", "date"),
            (True, None, type(None), True, None, None, None),
            (True, None, int, True, None, None, "integer"),
            (True, None, str, True, None, None, "string"),
            (False, None, int, True, None, None, "integer"),
            (False, None, str, True, None, None, "string"),
            (False, time(3, 24, 3), time, False, None, None, "time"),
            (False, None, int, True, lambda: 42, None, "integer"),
            (True, None, str, True, lambda: "Hello, world", None, "string"),
        ],
    )
    @pytest.mark.parametrize("on_error", ["raise", "omit", "default", None])
    @pytest.mark.parametrize(
        "validate_default",
        [
            True,
            False,
            None,
        ],
    )
    def test_default_schema(
        self,
        apply_optional,
        default,
        default_type,
        type_supported,
        # Note: When the `default_factory` param of `Field` is provided and is not
        # `None` the `default` param of `Field` must be left unset.
        default_factory,
        on_error,
        validate_default,
        expected_ifabsent,
        expected_range,
    ):
        type_annotation = Optional[default_type] if apply_optional else default_type
        field_specs = (
            Field(
                default,
                validate_default=validate_default,
            )
            # Use this condition to provide `None` as the `default`
            if default_factory is None
            else Field(
                default_factory=default_factory, validate_default=validate_default
            )
        )

        class Foo(BaseModel):
            x: type_annotation = field_specs

        field_schema = get_field_schema(Foo, "x")

        # There is no interface for end users to set value for
        # the "on_error" key. Here, we manually set it in the schema directly.
        if on_error is not None:
            field_schema.schema["on_error"] = on_error

        slot = SlotGenerator(field_schema).generate()
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert not slot.required
        assert slot.ifabsent == expected_ifabsent
        verify_notes(
            f"Unable to set a default value of {default!r} in LinkML. "
            f"Default values of type {default_type} are not supported.",
            not type_supported,
        )
        verify_notes(
            "The translation of `Optional` in Python may need further adjustments.",
            default_factory is None and default is not None and apply_optional,
        )
        verify_notes(
            f"Unable to express the default factory, {default_factory!r}, in LinkML.",
            default_factory is not None,
        )
        verify_notes(
            f"Unable to express the `on_error` option of {on_error} in LinkML.",
            on_error is not None and on_error != "raise",
        )

        # Verify the translation is propagated to the next level
        assert slot.range == expected_range

    @pytest.mark.parametrize("has_default", [True, False])
    def test_nullable_schema(self, has_default):
        field_specs = Field(42) if has_default else Field(...)

        class Foo(BaseModel):
            x: Optional[int] = field_specs

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        verify_notes(
            "The translation of `Optional` for a required field may require "
            "further adjustments.",
            not has_default,
        )

        # Verify the translation is propagated to the next level
        assert slot.range == "integer"

    def test_union_schema(self):
        class Bar1(BaseModel):
            y: int

        class Bar2(BaseModel):
            z: str

        class Foo0(BaseModel):
            x: Union[int, str]

        # === A case, customized, of type choices expressed as tuples ===
        field_schema = get_field_schema(Foo0, "x")
        field_schema.schema["choices"] = [
            (c, "label")
            for c in field_schema.schema["choices"]
            if not isinstance(c, tuple)
        ]
        slot = SlotGenerator(field_schema).generate()

        # `notes=slot.notes` is used instead of `notes=ANY` because
        # `SlotDefinition.__eq__` serializes fields before comparing, which
        # causes `unittest.mock.ANY` to be treated as the literal string
        # `'<ANY>'` rather than a wildcard. The note content is checked
        # separately below.
        assert slot == SlotDefinition(name="x", required=True, notes=slot.notes)
        assert in_exactly_one_string(
            "The union core schema contains a tuple as a choice. "
            "Tuples as choices are yet to be supported.",
            slot.notes,
        )

        # === Union of base types and models ===
        class Foo1(BaseModel):
            x: Union[int, Bar1, str]

        assert translate_field_to_slot(Foo1, "x") == SlotDefinition(
            name="x",
            range=ANY_CLASS_DEF.name,
            required=True,
            any_of=[
                AnonymousSlotExpression(range="integer"),
                AnonymousSlotExpression(range="Bar1"),
                AnonymousSlotExpression(range="string"),
            ],
        )

        # === Unions of two models ===
        class Foo2(BaseModel):
            x: Union[Bar1, Bar2]

        assert translate_field_to_slot(Foo2, "x") == SlotDefinition(
            name="x",
            range=ANY_CLASS_DEF.name,
            required=True,
            any_of=[
                AnonymousSlotExpression(range="Bar1"),
                AnonymousSlotExpression(range="Bar2"),
            ],
        )

        # === Union of base types, lists, and models ===
        class Foo3(BaseModel):
            x: Union[int, list[Bar1], list[str], Bar2]

        assert translate_field_to_slot(Foo3, "x") == SlotDefinition(
            name="x",
            range=ANY_CLASS_DEF.name,
            required=True,
            any_of=[
                AnonymousSlotExpression(range="integer"),
                AnonymousSlotExpression(range="Bar1", multivalued=True),
                AnonymousSlotExpression(range="string", multivalued=True),
                AnonymousSlotExpression(range="Bar2"),
            ],
        )

        # === Nested unions ===
        class Foo4(BaseModel):
            x: Union[int, Union[str, Bar1]]

        slot = translate_field_to_slot(Foo4, "x")
        assert slot == SlotDefinition(
            name="x",
            range=ANY_CLASS_DEF.name,
            required=True,
            any_of=[
                AnonymousSlotExpression(range="integer"),
                AnonymousSlotExpression(range="string"),
                AnonymousSlotExpression(range="Bar1"),
            ],
        )

    def test_tagged_union_schema(self):
        class Cat(BaseModel):
            pet_type: Literal["cat"]
            meows: int

        class Dog(BaseModel):
            pet_type: Literal["dog"]
            barks: float

        class Lizard(BaseModel):
            pet_type: Literal["reptile", "lizard"]
            scales: bool

        class Foo(BaseModel):
            pet: Union[Cat, Dog, Lizard] = Field(..., discriminator="pet_type")

        slot = translate_field_to_slot(Foo, "pet")

        assert in_exactly_one_string(
            "Tagged union types are yet to be supported", slot.notes
        )

    def test_chain_schema(self, mocker):
        class Foo(BaseModel):
            x: Union[int, str] = Field(..., pattern=r"^[0-9a-z]+$")

        field_schema = get_field_schema(Foo, "x")
        slot_generator = SlotGenerator(field_schema)

        # Initiate monitoring of the slot generation process
        spy = mocker.spy(slot_generator, "_shape_slot")

        slot = slot_generator.generate()

        assert in_exactly_one_string(
            "Warning: Pydantic core schema of type `'chain'` is encountered.",
            slot.notes,
        )

        # Sure that translation is propagated to each schema in the chain
        calls = (
            call(schema_in_chain) for schema_in_chain in field_schema.schema["steps"]
        )
        spy.assert_has_calls(calls, any_order=True)

    def test_model_schema(self):
        class Bar(BaseModel):
            y: int

        class Foo(BaseModel):
            x: Bar

        slot = translate_field_to_slot(Foo, "x")

        assert slot.range == "Bar"

    @pytest.mark.parametrize(
        ("max_length", "allowed_schemes", "expected_pattern"),
        [
            (100, ["http", "https"], r"^(?=.{,100}$)(?i:http|https)://[^\s]+$"),
            (42, ["http"], r"^(?=.{,42}$)(?i:http)://[^\s]+$"),
            (None, ["http", "https"], r"^(?i:http|https)://[^\s]+$"),
            (50, None, r"^(?=.{,50}$)[^\s]+://[^\s]+$"),
            (None, None, r"^[^\s]+://[^\s]+$"),
        ],
    )
    @pytest.mark.parametrize("host_required", [True, False, None])
    @pytest.mark.parametrize("default_host", ["example.com", None])
    @pytest.mark.parametrize("default_port", [42, None])
    @pytest.mark.parametrize("default_path", ["/path", None])
    def test_url_schema(
        self,
        max_length,
        allowed_schemes,
        host_required,
        default_host,
        default_port,
        default_path,
        expected_pattern,
    ):
        class Foo(BaseModel):
            x: Annotated[
                AnyUrl,
                UrlConstraints(
                    max_length,
                    allowed_schemes,
                    host_required,
                    default_host,
                    default_port,
                    default_path,
                ),
            ]

        slot = translate_field_to_slot(Foo, "x")
        verify_notes = partial(verify_str_lst, str_lst=slot.notes)

        assert slot.range == "uri"
        assert slot.pattern == expected_pattern
        verify_notes(
            "Unable to express the `host_required` option in LinkML.",
            host_required is not None,
        )
        verify_notes(
            "Unable to express the `default_host` option in LinkML.",
            default_host is not None,
        )
        verify_notes(
            "Unable to express the `default_port` option in LinkML.",
            default_port is not None,
        )
        verify_notes(
            "Unable to express the `default_path` option in LinkML.",
            default_path is not None,
        )

    @pytest.mark.parametrize(
        ("uuid_type", "expected_pattern"),
        [
            (UUID, get_uuid_regex()),
            (UUID3, get_uuid_regex(3)),
            (UUID4, get_uuid_regex(4)),
        ],
    )
    def test_uuid_schema(self, uuid_type, expected_pattern):
        class Foo(BaseModel):
            x: uuid_type

        slot = translate_field_to_slot(Foo, "x")

        assert slot.range == "string"
        assert slot.pattern == expected_pattern

    @pytest.mark.parametrize(
        ("title", "description"),
        [
            ("My Title", "My description"),
            ("My Title", None),
            (None, "My description"),
            (None, None),
        ],
    )
    def test_title_and_description(self, title, description):
        field_kwargs = {}
        if title is not None:
            field_kwargs["title"] = title
        if description is not None:
            field_kwargs["description"] = description

        class Foo(BaseModel):
            x: str = Field(**field_kwargs)

        slot = translate_field_to_slot(Foo, "x")

        assert slot.title == title
        assert slot.description == description

    def test_subschema_excludes_field_level_properties(self):
        class Foo(BaseModel):
            x: str = Field(title="T", description="D")

        field_schema = get_field_schema(Foo, "x")._replace(is_subschema=True)
        slot = SlotGenerator(field_schema).generate()

        assert slot.required is None
        assert slot.title is None
        assert slot.description is None
