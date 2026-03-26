import re
from enum import Enum, auto
from operator import itemgetter
from pathlib import Path
from typing import ClassVar, Optional, Type, cast

import pytest
import yaml
from linkml_runtime.linkml_model import SlotDefinition
from pydantic import BaseModel, RootModel, ValidationError
from pydantic_core import core_schema

from pydantic2linkml.exceptions import (
    InvalidLinkMLSchemaError,
    NameCollisionError,
    SlotExtensionError,
    YAMLContentError,
)
from pydantic2linkml.tools import (
    add_section_breaks,
    apply_schema_overlay,
    apply_yaml_deep_merge,
    bucketize,
    ensure_unique_names,
    fetch_defs,
    force_to_set,
    get_field_schema,
    get_locally_defined_fields,
    get_non_empty_meta_slots,
    get_parent_models,
    get_slot_usage_entry,
    get_uuid_regex,
    normalize_whitespace,
    remove_schema_key_duplication,
    resolve_ref_schema,
    sort_dict,
)

# A minimal YAML dict suitable as schema_yml input for apply_schema_overlay
# and apply_yaml_deep_merge tests.  Written in the canonical form produced by
# yaml_dumper.dumps so that round-tripping through SchemaDefinition leaves the
# content unchanged (aside from key reordering, which dict equality ignores).
SAMPLE_SCHEMA_YML = (
    "id: https://example.com/test\n"
    "name: original-name\n"
    "default_prefix: https://example.com/test/\n"
    "imports:\n"
    "  - linkml:types\n"
    "classes:\n"
    "  Foo:\n"
    "    name: Foo\n"
    "    description: original description\n"
)


def test_get_parent_models():
    class Foo:
        pass

    class Bar:
        pass

    class Baz:
        pass

    class A(BaseModel):
        pass

    class B(Foo, A):
        pass

    class C(B, Bar):
        pass

    class X(BaseModel):
        pass

    class Z(X, Baz, C):
        pass

    with pytest.raises(ValueError, match="`model` cannot be `pydantic.BaseModel`"):
        get_parent_models(BaseModel)
    assert get_parent_models(A) == []
    assert get_parent_models(B) == [A]
    assert get_parent_models(C) == [B]
    assert get_parent_models(Z) == [X, C]


class TestResolveRefSchema:
    def test_valid_input(self):
        """
        Test with valid input
        """

        class A(BaseModel):
            pass

        class B(BaseModel):
            x: A
            y: Optional[A]
            z: "B"

        a_schema = A.__pydantic_core_schema__
        b_schema = B.__pydantic_core_schema__

        assert_err_msg = (
            "Wrong assumption about Pydantic behavior. Please re-write the test."
        )

        # If these two assertions fail, it doesn't mean `resolve_ref_schema` is wrong.
        # It only means the assumption on how Pydantic represents models in `CoreSchema`
        # is wrong, and we have to find another way to test `resolve_ref_schema`.
        assert a_schema["type"] == "model", assert_err_msg
        assert b_schema["type"] == "definitions", assert_err_msg

        resolved_a_schema = resolve_ref_schema(a_schema, a_schema)
        resolved_b_schema = resolve_ref_schema(b_schema, b_schema)
        assert resolved_a_schema["type"] == "model"
        assert resolved_a_schema["cls"] is A
        assert resolved_b_schema["type"] == "model"
        assert resolved_b_schema["cls"] is B

        x_field_schema = cast(
            core_schema.DefinitionReferenceSchema,
            cast(core_schema.ModelFieldsSchema, resolved_b_schema["schema"])["fields"][
                "x"
            ]["schema"],
        )

        # If this assertion fails, it doesn't mean `resolve_ref_schema` is wrong.
        # It only means the assumption about how Pydantic uses `definition-ref` is
        # wrong, and we have to find another way to test `resolve_ref_schema`.
        assert x_field_schema["type"] == "definition-ref", assert_err_msg

        assert (
            resolve_ref_schema(
                x_field_schema,
                b_schema,
            )["cls"]
            is A
        )

    def test_invalid_input(self):
        """
        Test with invalid input, i.e. context` is not a `DefinitionsSchema` object when
        `maybe_ref_schema` is a `DefinitionsSchema` or `DefinitionReferenceSchema`.
        """

        class A(BaseModel):
            pass

        class B(A):
            x: A
            y: "B"

        with pytest.raises(
            ValueError, match="`context` must be a `DefinitionsSchema` object"
        ):
            resolve_ref_schema(B.__pydantic_core_schema__, A.__pydantic_core_schema__)

    def test_missing_definition(self):
        """
        Test the case where the provided context does not have the corresponding schema
        for the provided reference schema.
        """

        class A(BaseModel):
            pass

        class B(BaseModel):
            x: A
            y: "B"

        class C(BaseModel):
            a: A
            c: "C"

        with pytest.raises(RuntimeError, match="not found in provided context"):
            resolve_ref_schema(C.__pydantic_core_schema__, B.__pydantic_core_schema__)


class TestGetFieldSchema:
    def test_valid_input(self):
        """
        Test with valid input
        """

        class A(BaseModel):
            a: int
            b: str

        class B(A):
            x: A
            y: "B"

        a_schema = A.__pydantic_core_schema__
        b_schema = B.__pydantic_core_schema__

        assert_err_msg = (
            "Wrong assumption about Pydantic behavior. Please re-write the test."
        )

        # If these two assertions fail, it doesn't mean `get_field_schema` is wrong.
        # It only means the assumption on how Pydantic represents models in `CoreSchema`
        # is wrong, and we have to find another way to test `get_field_schema`.
        assert a_schema["type"] == "model", assert_err_msg
        assert b_schema["type"] == "definitions", assert_err_msg

        a_field_schema_from_a = get_field_schema(A, "a")
        a_field_schema_from_b = get_field_schema(B, "a")
        assert (
            a_field_schema_from_a.schema
            == a_field_schema_from_b.schema
            == {"type": "int"}
        )
        assert a_field_schema_from_a.context == A.__pydantic_core_schema__
        assert a_field_schema_from_b.context == B.__pydantic_core_schema__
        assert a_field_schema_from_a.field_info is A.model_fields["a"]
        assert a_field_schema_from_b.field_info is B.model_fields["a"]

        b_field_schema_from_a = get_field_schema(A, "b")
        b_field_schema_from_b = get_field_schema(B, "b")
        assert (
            b_field_schema_from_a.schema
            == b_field_schema_from_b.schema
            == {"type": "str"}
        )
        assert b_field_schema_from_a.context == A.__pydantic_core_schema__
        assert b_field_schema_from_b.context == B.__pydantic_core_schema__
        assert b_field_schema_from_a.field_info is A.model_fields["b"]
        assert b_field_schema_from_b.field_info is B.model_fields["b"]

        # Verify the resolution of the field schema
        x_field_schema = get_field_schema(B, "x")
        assert x_field_schema.schema["type"] == "model"
        assert x_field_schema.schema["cls"] is A
        y_field_schema = get_field_schema(B, "y")
        assert y_field_schema.schema["type"] == "model"
        assert y_field_schema.schema["cls"] is B

    def test_input_without_model_fields(self):
        """
        Test input model without model fields
        """
        # noinspection PyPep8Naming
        Pets = RootModel[list[str]]

        with pytest.raises(
            NotImplementedError,
            match="This function currently doesn't support the inner schema of",
        ):
            get_field_schema(Pets, "root")


def test_get_locally_defined_fields():
    class A(BaseModel):
        a: str
        b: int
        c: ClassVar[str]

    class B(A):
        # Overriding definitions
        a: Optional[str]

        # New definitions
        x: float
        y: ClassVar[int]
        z: bool

    new, overriding = get_locally_defined_fields(B)

    assert set(new.keys()) == {"x", "z"}
    assert set(overriding.keys()) == {"a"}

    assert new["x"].schema == {"type": "float"}
    assert new["z"].schema == {"type": "bool"}

    assert overriding["a"].schema == {"type": "nullable", "schema": {"type": "str"}}


@pytest.mark.parametrize(
    ("items", "key_func", "value_func", "expected"),
    [
        (
            list(range(10)),
            lambda x: "even" if x % 2 == 0 else "odd",
            None,
            {"even": list(range(0, 10, 2)), "odd": list(range(1, 10, 2))},
        ),
        (
            ("a", "abc", "bmz", "acd", "cad", "cba"),
            itemgetter(0),
            None,
            {"a": ["a", "abc", "acd"], "b": ["bmz"], "c": ["cad", "cba"]},
        ),
        (
            list(range(10)),
            lambda x: "even" if x % 2 == 0 else "odd",
            lambda x: x * 2,
            {"even": list(range(0, 20, 4)), "odd": list(range(2, 20, 4))},
        ),
        (
            ("a", "abc", "bmz", "acd", "cad", "cba"),
            itemgetter(0),
            lambda x: x[0],
            {"a": ["a", "a", "a"], "b": ["b"], "c": ["c", "c"]},
        ),
    ],
)
def test_bucketize(items, key_func, value_func, expected):
    assert bucketize(items, key_func, value_func) == expected


def test_ensure_unique_names():
    class A:
        pass

    class B(BaseModel):
        pass

    class C(Enum):
        C1 = auto()
        C2 = auto()

    class D(Enum):
        D1 = auto()
        D2 = auto()

    class Y:
        pass

    def func() -> list[Type]:
        """
        A internal function used to provide a separate namespace
        """

        class X:
            pass

        # noinspection PyShadowingNames
        class B:
            pass

        # noinspection PyShadowingNames
        class C(BaseModel):
            pass

        # noinspection PyShadowingNames
        class D(Enum):
            D3 = auto()
            D4 = auto()

        class Z(Enum):
            Z1 = auto()
            Z2 = auto()

        return [X, B, C, D, Z]

    local_clses = [A, B, C, D, Y]

    assert ensure_unique_names(*local_clses) is None
    assert ensure_unique_names(*func()) is None

    with pytest.raises(NameCollisionError) as exc_info:
        ensure_unique_names(*local_clses, *func())

    err_str = str(exc_info.value)

    # Assert three collision messages separated by semicolons
    assert err_str.count(";") == 2
    assert err_str.count("Name collision @ B: ") == 1
    assert err_str.count("Name collision @ C: ") == 1
    assert err_str.count("Name collision @ D: ") == 1


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("", ""),
        ("  ", ""),
        ("  a  ", "a"),
        ("a  b", "a b"),
        ("a b  c", "a b c"),
        ("a\nb", "a b"),
        ("a\n\nb", "a b"),
        ("a\n\n\nb", "a b"),
        ("\t ", ""),
        ("\t a \t \n b \t", "a b"),
    ],
)
def test_normalize_whitespace(input_str, expected):
    assert normalize_whitespace(input_str) == expected


def test_fetch_defs():
    from tests.assets import mock_module0, mock_module1

    models, enums = fetch_defs([mock_module0, mock_module1])

    assert models == {
        mock_module0.A,
        mock_module0.B,
        mock_module0.C,
        mock_module1.X,
        mock_module1.Y,
    }
    assert enums == {
        mock_module0.E0,
        mock_module0.E1,
        mock_module1.E2,
        mock_module1.E3,
        mock_module1.E4,
    }


class TestGetUuidRegex:
    @pytest.mark.parametrize(
        ("version", "expected_output"),
        [
            (
                1,
                (
                    r"^(?:urn:uuid:)?"  # Optional "urn:uuid:" prefix
                    r"[0-9a-fA-F]{8}-?"  # 8 hex digits with optional hyphen
                    r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
                    # Version and 3 hex digits with optional hyphen
                    r"1[0-9a-fA-F]{3}-?"
                    # Variant and 3 hex digits with optional hyphen
                    r"[89abAB][0-9a-fA-F]{3}-?"
                    r"[0-9a-fA-F]{12}$"  # 12 hex digits
                ),
            ),
            (
                4,
                (
                    r"^(?:urn:uuid:)?"  # Optional "urn:uuid:" prefix
                    r"[0-9a-fA-F]{8}-?"  # 8 hex digits with optional hyphen
                    r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
                    # Version and 3 hex digits with optional hyphen
                    r"4[0-9a-fA-F]{3}-?"
                    # Variant and 3 hex digits with optional hyphen
                    r"[89abAB][0-9a-fA-F]{3}-?"
                    r"[0-9a-fA-F]{12}$"  # 12 hex digits
                ),
            ),
            (
                None,
                (
                    r"^(?:urn:uuid:)?"  # Optional "urn:uuid:" prefix
                    r"[0-9a-fA-F]{8}-?"  # 8 hex digits with optional hyphen
                    r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
                    r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
                    r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
                    r"[0-9a-fA-F]{12}$"  # 12 hex digits
                ),
            ),
        ],
    )
    def test_valid_input(self, version, expected_output):
        assert get_uuid_regex(version) == expected_output

    @pytest.mark.parametrize("version", [0, 2, 6])
    def test_invalid_input(self, version):
        with pytest.raises(ValueError, match="Invalid UUID version"):
            get_uuid_regex(version)

    @pytest.mark.parametrize(
        ("text", "version", "match_expected"),
        [
            ("60c32af6-4b10-11ef-9ab2-0ecb4bcddcb5", 1, True),
            ("3f46ae03-c654-36b0-a55d-cd0aa042c9f2", 3, True),
            ("6b4c4599-1963-4d01-abbf-abdcb30ad9ff", 4, True),
            ("2cba86aa-e4d3-5340-9c8d-012bfe7d5d9d", 5, True),
            ("2cba86aa-e4d3-5340-9c8d-012bfe7d5d9d", None, True),
            # With some hyphens missing
            ("2cba86aae4d353409c8d012bfe7d5d9d", None, True),
            ("6b4c4599-19634d01-abbfabdcb30ad9ff", 4, True),
            # With mismatched version
            ("60c32af6-4b10-11ef-9ab2-0ecb4bcddcb5", 3, False),
            ("3f46ae03-c654-36b0-a55d-cd0aa042c9f2", 1, False),
            ("6b4c4599-1963-4d01-abbf-abdcb30ad9ff", 1, False),
            ("2cba86aa-e4d3-5340-9c8d-012bfe7d5d9d", 4, False),
            # With wrong variant
            ("60c32af6-4b10-11ef-0ab2-0ecb4bcddcb5", 1, False),
            ("3f46ae03-c654-36b0-755d-cd0aa042c9f2", 3, False),
            ("6b4c4599-1963-4d01-cbbf-abdcb30ad9ff", 4, False),
            ("2cba86aa-e4d3-5340-2c8d-012bfe7d5d9d", 5, False),
            # With wrong variant and version, though version doesn't really matter here
            # With some hyphens missing
            ("12345678123456781234567812345678", 4, False),
            # too long
            ("6b4c4599-1963-4d01-abbf-abdacb30ad9ff", 4, False),
            ("2cba86aae4d353409c8d012bfbe7d5d9d", None, False),
            # too short
            ("6b4c4599-19634d01-abbabdcb30ad9ff", 4, False),
            ("6b4c4599-1963-4d01-abf-abdcb30ad9ff", None, False),
            # too many consecutive hyphens
            ("3f46ae03-c654--36b0-a55d-cd0aa042c9f2", 3, False),
            # Arbitrary strings
            ("Hello world!", 4, False),
            ("Foobar", None, False),
        ],
    )
    @pytest.mark.parametrize("prepend_prefix", [True, False])
    def test_generated_regex_behavior(
        self, text, version, prepend_prefix, match_expected
    ):
        """
        Verify the behavior of the generated regex pattern
        """
        if prepend_prefix:
            text = f"urn:uuid:{text}"

        if match_expected:
            assert re.match(get_uuid_regex(version), text) is not None
        else:
            assert re.match(get_uuid_regex(version), text) is None


@pytest.mark.parametrize(
    ("input_", "expected_out"),
    [
        (None, set()),
        ([1, 2, 3], {1, 2, 3}),
        ({1, 2, 3}, {1, 2, 3}),
        (set(), None),
        ({3, 4, 5}, None),
    ],
)
def test_force_to_set(input_, expected_out):
    if not isinstance(input_, set):
        assert force_to_set(input_) == expected_out
    else:
        assert force_to_set(input_) is input_


@pytest.mark.parametrize(
    ("input_dict", "key_func", "expected_sorted_dict_items"),
    [
        ({"a": 1, "c": -1, "b": 2}, None, [("a", 1), ("b", 2), ("c", -1)]),
        ({"a": 1, "c": -1, "b": 2}, itemgetter(1), [("c", -1), ("a", 1), ("b", 2)]),
        ({"Ab": 3, "a": 1}, None, [("Ab", 3), ("a", 1)]),
        (
            {"Ab": 3, "a": 1},
            lambda t: itemgetter(0)(t).casefold(),
            [("a", 1), ("Ab", 3)],
        ),
    ],
)
def test_sort_dict(input_dict, key_func, expected_sorted_dict_items):
    result = (
        sort_dict(input_dict, key_func)
        if key_func is not None
        else sort_dict(input_dict)
    )
    assert list(result.items()) == expected_sorted_dict_items


@pytest.mark.parametrize(
    ("slot", "expected_non_empty_meta_slots"),
    [
        (SlotDefinition(name="Foo"), {"name"}),
        (
            SlotDefinition(name="Foo", range="integer", multivalued=True),
            {"name", "range", "multivalued"},
        ),
        (
            SlotDefinition(name="Foo", pattern="", required=False, multivalued=None),
            {"name", "pattern", "required"},
        ),
        (
            SlotDefinition(name="Foo", pattern="", mixins=[], multivalued=None),
            {"name", "pattern"},
        ),
        (
            SlotDefinition(name="Foo", mixins=["bar"], multivalued=None),
            {"name", "mixins"},
        ),
    ],
)
def test_get_non_empty_meta_slots(slot, expected_non_empty_meta_slots):
    assert get_non_empty_meta_slots(slot) == expected_non_empty_meta_slots


@pytest.mark.parametrize(
    ("base", "target", "expected_missing", "expected_varied", "expected_return"),
    [
        # Base and target are the same
        (SlotDefinition("a"), SlotDefinition("a"), [], [], None),
        (
            SlotDefinition("a", required=True, range="integer"),
            SlotDefinition("a", required=True, range="integer"),
            [],
            [],
            None,
        ),
        # Target is missing some required meta slots
        (
            SlotDefinition("a", required=True, range="integer"),
            SlotDefinition("a"),
            ["range", "required"],
            [],
            None,
        ),
        # Values in some meta slots in target are varied
        (SlotDefinition("a"), SlotDefinition("b"), [], ["name"], None),
        # Target is missing some required meta slots, and values in some meta slots in
        # target are varied
        (
            SlotDefinition("a", required=True, range="integer"),
            SlotDefinition("b"),
            ["range", "required"],
            ["name"],
            None,
        ),
        # Target extends base
        (
            SlotDefinition("a", range="integer"),
            SlotDefinition(
                "a",
                range="integer",
                required=True,
                multivalued=False,
                mixins=["b"],
                description="Hello, world!",
            ),
            [],
            [],
            SlotDefinition(
                "a",
                required=True,
                multivalued=False,
                mixins=["b"],
                description="Hello, world!",
            ),
        ),
    ],
)
def test_get_slot_usage_entry(
    base, target, expected_missing, expected_varied, expected_return
):
    if expected_missing or expected_varied:
        with pytest.raises(SlotExtensionError) as exc_info:
            get_slot_usage_entry(base, target)
        error = exc_info.value
        assert error.missing_meta_slots == expected_missing
        assert error.varied_meta_slots == expected_varied
    else:
        assert get_slot_usage_entry(base, target) == expected_return


class TestApplySchemaOverlay:
    @pytest.mark.parametrize(
        "overlay_content, expected_overrides",
        [
            pytest.param("name: new-name\n", {"name": "new-name"}, id="single_field"),
            pytest.param(
                "name: new-name\ntitle: My Title\n",
                {"name": "new-name", "title": "My Title"},
                id="multiple_fields",
            ),
        ],
    )
    def test_valid_fields_applied(
        self, tmp_path: Path, overlay_content, expected_overrides
    ):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(overlay_content)
        result = apply_schema_overlay(
            schema_yml=SAMPLE_SCHEMA_YML, overlay_file=overlay_file
        )
        assert (
            yaml.safe_load(result)
            == yaml.safe_load(SAMPLE_SCHEMA_YML) | expected_overrides
        )

    @pytest.mark.parametrize(
        "get_path",
        [
            pytest.param(lambda p: p / "no-such-file.yaml", id="nonexistent_file"),
            pytest.param(lambda p: p, id="directory"),
        ],
    )
    def test_invalid_overlay_file_raises_validation_error(
        self, tmp_path: Path, get_path
    ):
        with pytest.raises(ValidationError):
            apply_schema_overlay(
                schema_yml=SAMPLE_SCHEMA_YML, overlay_file=get_path(tmp_path)
            )

    @pytest.mark.parametrize(
        "overlay_content",
        [
            pytest.param("- item1\n- item2\n", id="list"),
            pytest.param("", id="null"),
        ],
    )
    def test_non_dict_overlay_raises_overlay_content_error(
        self, tmp_path: Path, overlay_content
    ):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(overlay_content)
        with pytest.raises(YAMLContentError):
            apply_schema_overlay(
                schema_yml=SAMPLE_SCHEMA_YML, overlay_file=overlay_file
            )

    def test_schema_yml_not_dict_raises_value_error(self, tmp_path: Path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text("name: new-name\n")
        with pytest.raises(ValueError):
            apply_schema_overlay(
                schema_yml="- item1\n- item2\n", overlay_file=overlay_file
            )

    def test_unknown_field_raises_invalid_schema_error(self, tmp_path: Path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text("not_a_field: some_value\n")
        with pytest.raises(InvalidLinkMLSchemaError):
            apply_schema_overlay(
                schema_yml=SAMPLE_SCHEMA_YML, overlay_file=overlay_file
            )

    def test_output_follows_schema_definition_field_order(self, tmp_path: Path):
        # description comes after name in SchemaDefinition; supply them reversed
        schema_yml = (
            "description: some desc\n"
            "name: test-name\n"
            "id: https://example.com/test\n"
        )
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text("title: My Title\n")
        result = apply_schema_overlay(schema_yml=schema_yml, overlay_file=overlay_file)
        keys = list(yaml.safe_load(result).keys())
        assert keys.index("name") < keys.index("description") < keys.index("title")

    def test_unicode_content_preserved(self, tmp_path: Path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text("title: \u00dc n\u00ef c\u00f6d\u00e9\n")
        result = apply_schema_overlay(
            schema_yml=SAMPLE_SCHEMA_YML, overlay_file=overlay_file
        )
        assert yaml.safe_load(result)["title"] == "\u00dc n\u00ef c\u00f6d\u00e9"


class TestApplyYamlDeepMerge:
    @pytest.mark.parametrize(
        "merge_content, expected",
        [
            pytest.param(
                "name: new-name\n",
                {
                    "id": "https://example.com/test",
                    "name": "new-name",
                    "default_prefix": "https://example.com/test/",
                    "imports": ["linkml:types"],
                    "classes": {
                        "Foo": {"name": "Foo", "description": "original description"}
                    },
                },
                id="top_level_scalar_override",
            ),
            pytest.param(
                # Nested dict merge: Foo.title added, Foo.description preserved,
                # Bar added alongside Foo
                "classes:\n"
                "  Foo:\n"
                "    title: new title\n"
                "  Bar:\n"
                "    description: bar desc\n",
                {
                    "id": "https://example.com/test",
                    "name": "original-name",
                    "default_prefix": "https://example.com/test/",
                    "imports": ["linkml:types"],
                    "classes": {
                        "Foo": {
                            "name": "Foo",
                            "description": "original description",
                            "title": "new title",
                        },
                        "Bar": {"name": "Bar", "description": "bar desc"},
                    },
                },
                id="nested_dict_merge",
            ),
            pytest.param(
                # Nested dict override: Foo.description replaced
                "classes:\n  Foo:\n    description: new description\n",
                {
                    "id": "https://example.com/test",
                    "name": "original-name",
                    "default_prefix": "https://example.com/test/",
                    "imports": ["linkml:types"],
                    "classes": {
                        "Foo": {"name": "Foo", "description": "new description"}
                    },
                },
                id="nested_dict_override",
            ),
            pytest.param(
                # Appending to list: always_merger appends elements to lists
                "imports:\n  - linkml:extra\n",
                {
                    "id": "https://example.com/test",
                    "name": "original-name",
                    "default_prefix": "https://example.com/test/",
                    "imports": ["linkml:types", "linkml:extra"],
                    "classes": {
                        "Foo": {"name": "Foo", "description": "original description"}
                    },
                },
                id="append_to_list",
            ),
        ],
    )
    def test_merge_applied(self, tmp_path: Path, merge_content, expected):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text(merge_content)
        result = apply_yaml_deep_merge(
            schema_yml=SAMPLE_SCHEMA_YML, merge_file=merge_file
        )
        assert yaml.safe_load(result) == expected

    @pytest.mark.parametrize(
        "get_path",
        [
            pytest.param(lambda p: p / "no-such-file.yaml", id="nonexistent_file"),
            pytest.param(lambda p: p, id="directory"),
        ],
    )
    def test_invalid_merge_file_raises_validation_error(self, tmp_path: Path, get_path):
        with pytest.raises(ValidationError):
            apply_yaml_deep_merge(
                schema_yml=SAMPLE_SCHEMA_YML, merge_file=get_path(tmp_path)
            )

    @pytest.mark.parametrize(
        "merge_content",
        [
            pytest.param("- item1\n- item2\n", id="list"),
            pytest.param("", id="null"),
        ],
    )
    def test_non_dict_merge_raises_yaml_content_error(
        self, tmp_path: Path, merge_content
    ):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text(merge_content)
        with pytest.raises(YAMLContentError):
            apply_yaml_deep_merge(schema_yml=SAMPLE_SCHEMA_YML, merge_file=merge_file)

    def test_invalid_yaml_in_merge_file_raises_yaml_error(self, tmp_path: Path):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text("key: [unclosed\n")
        with pytest.raises(yaml.YAMLError):
            apply_yaml_deep_merge(schema_yml=SAMPLE_SCHEMA_YML, merge_file=merge_file)

    def test_schema_yml_not_dict_raises_value_error(self, tmp_path: Path):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text("name: new-name\n")
        with pytest.raises(ValueError):
            apply_yaml_deep_merge(
                schema_yml="- item1\n- item2\n", merge_file=merge_file
            )

    def test_schema_yml_invalid_yaml_raises_value_error(self, tmp_path: Path):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text("name: new-name\n")
        with pytest.raises(ValueError):
            apply_yaml_deep_merge(schema_yml="key: [unclosed\n", merge_file=merge_file)

    def test_unicode_content_preserved(self, tmp_path: Path):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text("title: \u00dc n\u00ef c\u00f6d\u00e9\n")
        result = apply_yaml_deep_merge(
            schema_yml=SAMPLE_SCHEMA_YML, merge_file=merge_file
        )
        assert yaml.safe_load(result)["title"] == "\u00dc n\u00ef c\u00f6d\u00e9"

    def test_unknown_field_raises_invalid_schema_error(self, tmp_path: Path):
        merge_file = tmp_path / "merge.yaml"
        merge_file.write_text("not_a_field: some_value\n")
        with pytest.raises(InvalidLinkMLSchemaError):
            apply_yaml_deep_merge(schema_yml=SAMPLE_SCHEMA_YML, merge_file=merge_file)


class TestRemoveSchemaKeyDuplication:
    def test_classes_name_removed(self):
        schema = {"classes": {"Person": {"name": "Person", "description": "A person"}}}
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        assert "name" not in result["classes"]["Person"]
        assert result["classes"]["Person"]["description"] == "A person"

    def test_slots_name_removed(self):
        schema = {"slots": {"age": {"name": "age", "range": "integer"}}}
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        assert "name" not in result["slots"]["age"]
        assert result["slots"]["age"]["range"] == "integer"

    def test_enums_name_removed(self):
        schema = {"enums": {"Status": {"name": "Status"}}}
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        assert "name" not in result["enums"]["Status"]

    def test_slot_usage_name_removed(self):
        schema = {
            "classes": {
                "Employee": {
                    "name": "Employee",
                    "slot_usage": {"age": {"name": "age", "required": True}},
                }
            }
        }
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        assert "name" not in result["classes"]["Employee"]["slot_usage"]["age"]
        assert result["classes"]["Employee"]["slot_usage"]["age"]["required"] is True

    def test_permissible_values_text_removed(self):
        schema = {
            "enums": {
                "Status": {
                    "name": "Status",
                    "permissible_values": {
                        "ACTIVE": {"text": "ACTIVE", "description": "Currently active"}
                    },
                }
            }
        }
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        pv = result["enums"]["Status"]["permissible_values"]["ACTIVE"]
        assert "text" not in pv
        assert pv["description"] == "Currently active"

    def test_prefixes_prefix_prefix_removed(self):
        schema = {
            "prefixes": {
                "linkml": {
                    "prefix_prefix": "linkml",
                    "prefix_reference": "https://w3id.org/linkml/",
                },
                "ex": {
                    "prefix_prefix": "ex",
                    "prefix_reference": "https://example.org/",
                },
            }
        }
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        for prefix_entry in result["prefixes"].values():
            assert "prefix_prefix" not in prefix_entry
            assert "prefix_reference" in prefix_entry

    def test_missing_sections_no_error(self):
        schema = {"id": "https://example.com/test", "name": "test-schema"}
        result = yaml.safe_load(remove_schema_key_duplication(yaml.dump(schema)))
        assert result["id"] == "https://example.com/test"

    def test_round_trip(self):
        from linkml_runtime.dumpers import yaml_dumper
        from tests.assets import mock_module0, mock_module1

        from pydantic2linkml.gen_linkml import translate_defs

        schema = translate_defs([mock_module0.__name__, mock_module1.__name__])
        raw_yml = yaml_dumper.dumps(schema)
        result_yml = remove_schema_key_duplication(raw_yml)
        result = yaml.safe_load(result_yml)

        for prefix_entry in result.get("prefixes", {}).values():
            assert "prefix_prefix" not in prefix_entry
        for cls in result.get("classes", {}).values():
            assert "name" not in cls
            for su in cls.get("slot_usage", {}).values():
                assert "name" not in su
        for slot in result.get("slots", {}).values():
            assert "name" not in slot
        for enum in result.get("enums", {}).values():
            assert "name" not in enum
            for pv in enum.get("permissible_values", {}).values():
                assert "text" not in pv


class TestAddSectionBreaks:
    @pytest.mark.parametrize(
        "yml, kwargs, expected",
        [
            # blank line inserted before a mid-string key (default keys)
            (
                "id: schema\nclasses:\n  Foo: {}\n",
                {},
                "id: schema\n\nclasses:\n  Foo: {}\n",
            ),
            # no break added when key is at position 0
            (
                "classes:\n  Foo: {}\n",
                {},
                "classes:\n  Foo: {}\n",
            ),
            # all three default keys receive breaks
            (
                "id: s\nenums:\n  E: {}\nslots:\n  s: {}\nclasses:\n  C: {}\n",
                {},
                "id: s\n\nenums:\n  E: {}\n\nslots:\n  s: {}\n\nclasses:\n  C: {}\n",
            ),
            # custom keys
            (
                "id: s\nsubsets:\n  sub: {}\n",
                {"keys": ("subsets",)},
                "id: s\n\nsubsets:\n  sub: {}\n",
            ),
            # custom break_str
            (
                "id: s\nclasses:\n  C: {}\n",
                {"break_str": "\n# ---\n"},
                "id: s\n\n# ---\nclasses:\n  C: {}\n",
            ),
            # indented occurrence of a key name is not matched
            (
                "classes:\n  Foo:\n    slots:\n      - bar\n",
                {},
                "classes:\n  Foo:\n    slots:\n      - bar\n",
            ),
            # empty keys tuple — string unchanged
            (
                "id: s\nclasses:\n  C: {}\n",
                {"keys": ()},
                "id: s\nclasses:\n  C: {}\n",
            ),
        ],
    )
    def test_add_section_breaks(self, yml, kwargs, expected):
        assert add_section_breaks(yml, **kwargs) == expected
