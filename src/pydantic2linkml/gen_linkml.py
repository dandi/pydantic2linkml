import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import fields
from datetime import date
from enum import Enum
from functools import partial
from itertools import chain
from operator import itemgetter
from typing import Any, Optional, Union

import pydantic
from linkml_runtime.linkml_model import (
    ClassDefinition,
    EnumDefinition,
    PermissibleValue,
    SchemaDefinition,
    SlotDefinition,
)
from linkml_runtime.linkml_model.meta import AnonymousSlotExpression
from linkml_runtime.utils.schema_builder import SchemaBuilder
from packaging import version
from pydantic import BaseModel

# noinspection PyProtectedMember
from pydantic._internal import _typing_extra

# noinspection PyProtectedMember
from pydantic._internal._core_utils import CoreSchemaOrField
from pydantic.json_schema import CoreSchemaOrFieldType
from pydantic_core import core_schema

from pydantic2linkml.exceptions import (
    GeneratorReuseError,
    SlotExtensionError,
    TranslationNotImplementedError,
)
from pydantic2linkml.tools import (
    FieldSchema,
    LocallyDefinedFields,
    bucketize,
    ensure_unique_names,
    fetch_defs,
    force_to_set,
    get_all_modules,
    get_field_schema,
    get_locally_defined_fields,
    get_parent_models,
    get_slot_usage_entry,
    get_uuid_regex,
    normalize_whitespace,
    resolve_ref_schema,
    sort_dict,
)

pydantic_version = version.parse(pydantic.__version__)

if pydantic_version >= version.parse("2.10"):
    literal_values = _typing_extra.literal_values
else:
    literal_values = _typing_extra.all_literal_values

logger = logging.getLogger(__name__)

# Callable to sort a dictionary by its keys case-insensitively
sort_dict_by_ikeys = partial(sort_dict, key_func=lambda t: itemgetter(0)(t).casefold())

# The LinkML Any type
# For more info, see https://linkml.io/linkml/schemas/advanced.html#linkml-any-type
any_class_def = ClassDefinition(
    name="Any", description="Any object", class_uri="linkml:Any"
)


class LinkmlGenerator:
    """
    Instances of this class are single-use LinkML generators.

    Note:
        Each instance of this class should only be used once to generate
            a LinkML schema.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        id_: Optional[str] = None,
        models: Optional[Iterable[type[BaseModel]]] = None,
        enums: Optional[Iterable[type[Enum]]] = None,
    ):
        """
        :param name: The name of the LinkML schema to be generated
        :param id_: The ID of the LinkML schema to be generated
        :param models: An iterable of Pydantic models to be converted to LinkML classes
            in the generated schema. Note: Any duplicated models will be removed.
        :param enums: An iterable of Enums to be converted to LinkML enums in
            the generated schema. Note: Any duplicated enums will be removed.

        raises NameCollisionError: If there are classes with the same name in the
            combined collection of `models` and `enums`
        """

        def get_case_insensitive_name(cls) -> str:
            return cls.__name__.casefold()

        def to_sorted_lst(
            iterable: Optional[Iterable[Union[type[Enum], type[BaseModel]]]]
        ) -> list[Union[type[Enum], type[BaseModel]]]:
            return sorted(force_to_set(iterable), key=get_case_insensitive_name)

        # Turn models and enums to lists of unique elements sorted by name
        # case-insensitively
        model_lst = to_sorted_lst(models)
        enum_lst = to_sorted_lst(enums)

        ensure_unique_names(*model_lst, *enum_lst)

        # Map of models to their locally defined fields
        self._m_f_map: dict[type[BaseModel], LocallyDefinedFields] = {
            m: get_locally_defined_fields(m) for m in model_lst
        }

        self._enums = enum_lst
        self._sb = SchemaBuilder(name, id_)

        # This changes to True after this generator generates a schema
        # (for preventing issues caused by accidental reuse
        # of this generator). See class docstring for more info.
        self._used = False

    def generate(self) -> SchemaDefinition:
        """
        Generate a LinkML schema from the models and enums provided to this generator.

        :return: The generated LinkML schema
        """
        if self._used:
            raise GeneratorReuseError(self)
        else:
            self._used = True

        self._sb.add_defaults()
        self._establish_supporting_defs()

        self._add_enums()  # Add enums to the schema
        self._add_slots()  # Add slots to the schema
        self._add_classes()  # Add classes to the schema

        return self._sb.schema

    def _add_enums(self) -> None:
        """
        Add LinkML enum representations of the enums in `self._enums` to the schema
        """
        for enum_ in self._enums:
            # All permissible values in the enum in string form
            enum_value_strs = [str(member.value) for member in enum_]

            self._sb.add_enum(
                EnumDefinition(
                    name=enum_.__name__,
                    description=(
                        normalize_whitespace(enum_.__doc__)
                        if enum_.__doc__ is not None
                        else None
                    ),
                    permissible_values=[
                        PermissibleValue(text=value_str, meaning=value_str)
                        for value_str in enum_value_strs
                    ],
                )
            )

    def _add_slots(self) -> None:
        """
        Add the slots construed from the fields in `self._m_f_map` to the schema
        """
        # Extract all the newly defined fields from across all models
        new_fields: Iterable[tuple[str, FieldSchema]] = chain.from_iterable(
            v.new.items() for v in self._m_f_map.values()
        )

        buckets: defaultdict[str, list[FieldSchema]] = bucketize(
            new_fields, key_func=itemgetter(0), value_func=itemgetter(1)
        )

        # Sort the buckets by field name case-insensitively
        sorted_buckets = sort_dict_by_ikeys(buckets)

        # Add the slots to the schema
        for schema_lst in sorted_buckets.values():
            self._add_slot(schema_lst)

    def _add_slot(self, field_schema_lst: list[FieldSchema]) -> None:
        """
        Add a slot to the schema

        :param field_schema_lst: A non-empty list of schemas of fields with the same
            name in different Pydantic models from which a slot is to be generated and
            added
        :raises ValueError: If `field_schema_lst` is empty

        Note: The slot generated is one that contains the maximum set of properties with
            a consistent value across the field schemas provided
        """
        field_schema_lst_len = len(field_schema_lst)

        if field_schema_lst_len == 0:
            raise ValueError("The provided list of field schemas is empty.")

        slot_to_add: SlotDefinition
        if field_schema_lst_len == 1:
            slot_to_add = SlotGenerator(field_schema_lst[0]).generate()
        else:
            # Slots generated from the field schemas respectively
            slots = [SlotGenerator(schema).generate() for schema in field_schema_lst]
            first_slot = slots[0]

            # === Here, a property is a field in the context of a dataclass and a meta
            # slot in the context of a LinkML entity ===

            slot_properties = {f.name for f in fields(SlotDefinition)}

            # Find the set of properties that have a consistent value across all slots
            # generated from the field schemas
            inconsistent_properties = set()
            for p in slot_properties:
                # Determine if the property is inconsistent across the slots
                first_p_value = getattr(first_slot, p)
                for s in slots[1:]:
                    if getattr(s, p) != first_p_value:
                        inconsistent_properties.add(p)
                        break

            consistent_properties = slot_properties.difference(inconsistent_properties)

            slot_to_add = SlotDefinition(
                **{p: getattr(first_slot, p) for p in consistent_properties}
            )

        # Add the slot to the schema
        self._sb.add_slot(slot_to_add)

    def _add_classes(self) -> None:
        """
        Add the classes construed from the models in `self._m_f_map` to the schema
        """
        for model in self._m_f_map:
            self._sb.add_class(self._generate_class(model))

    def _generate_class(self, model: type[BaseModel]) -> ClassDefinition:
        """
        Generate a LinkML class from a given Pydantic model

        :param model: The given Pydantic model
        :return: A LinkML class representing the given Pydantic model in the context
            of the LinkML schema being generated
        """

        def attach_note(note: str) -> None:
            """
            Attach a note to the class definition

            :param note: The note to attach
            """
            notes.append(f"{__package__}: {note}")

        parents = get_parent_models(model)
        local_fields = self._m_f_map[model]
        slot_usage: list[SlotDefinition] = []
        notes: list[str] = []

        # === Handle in heritance specifications ===
        # Set parent class
        is_a = parents[0].__name__ if parents else None

        # Set mixins
        mixins = [parent.__name__ for parent in parents[1:]]
        for m in mixins:
            attach_note(
                f"Warning: LinkML does not support multiple inheritance. {m} is not "
                f"specified as a parent, through the `is_a` meta slot, but as a mixin."
            )

        # === Handle newly defined fields in the model ===
        # Slot representations of the newly defined fields in the model
        new_field_slot_reps = {
            field_name: SlotGenerator(schema).generate()
            for field_name, schema in local_fields.new.items()
        }

        # Set slots with the names of newly defined fields in the model in sorted order
        slots: list[str] = list(new_field_slot_reps.keys())

        # Add slot usage entries for newly defined fields that have a slot
        # representation that is different than the corresponding global slot
        # representation
        for name, new_field_slot_rep in new_field_slot_reps.items():
            # Get the global slot representation of the field
            global_slot = self._sb.schema.slots[name]

            if global_slot != new_field_slot_rep:
                # Create a slot usage entry for the field
                entry = get_slot_usage_entry(global_slot, new_field_slot_rep)
                assert entry is not None
                slot_usage.append(entry)

        # === Handle overriding fields in the model ===
        overriding_field_slot_reps = {
            field_name: SlotGenerator(schema).generate()
            for field_name, schema in local_fields.overriding.items()
        }

        for name, overriding_field_slot_rep in overriding_field_slot_reps.items():
            for parent in parents:
                if name in parent.model_fields:
                    overridden_field_slot_rep = SlotGenerator(
                        get_field_schema(parent, name)
                    ).generate()
                    break
            else:
                # This block should not be reached
                err_msg = (
                    f"Unable to locate a {name} field in any of the parents, {parents}"
                )
                raise RuntimeError(err_msg)

            # At this point, `overridden_field_slot_rep` must be set

            try:
                entry = get_slot_usage_entry(
                    overridden_field_slot_rep, overriding_field_slot_rep
                )
            except SlotExtensionError as e:
                # Attach needed note
                missing_substr = (
                    f"lacks meta slots: {e.missing_meta_slots} "
                    if e.missing_meta_slots
                    else ""
                )
                varied_substr = (
                    f"has changes in value in meta slots: {e.varied_meta_slots} "
                    if e.varied_meta_slots
                    else ""
                )
                substr = "and ".join(s for s in [missing_substr, varied_substr] if s)
                attach_note(
                    f"Impossible to generate slot usage entry for the {name} slot. "
                    f"The slot representation of the {name} field in the "
                    f"{model.__name__} Pydantic model {substr}."
                )
            else:
                if entry is not None:
                    slot_usage.append(entry)

        # Ensure collections in class definition are sorted by name case-insensitively
        slots.sort(key=str.casefold)
        slot_usage.sort(key=lambda s: s.name.casefold())

        return ClassDefinition(
            model.__name__,
            is_a=is_a,
            mixins=mixins,
            slots=slots,
            slot_usage=slot_usage,
            notes=notes,
        )

    def _establish_supporting_defs(self) -> None:
        """
        Establish the supporting definitions in the schema
        """
        # Add an `linkml:Any` class
        self._sb.add_class(any_class_def)


class SlotGenerator:
    """
    Instances of this class are single-use slot generators.

    Note:
        Each instance of this class should only be used once to generate
            a LinkML slot schema.
    """

    def __init__(self, field_schema: FieldSchema):
        """
        :param field_schema: The `FieldSchema` object specifying the Pydantic core
            schema of the corresponding field with context
        """
        self._slot: SlotDefinition = SlotDefinition(name=field_schema.field_name)
        self._field_schema: FieldSchema = field_schema
        self._schema_type_to_method = self._build_schema_type_to_method()

        # This changes to True after this generator generates a slot schema
        # (for preventing issues caused by accidental reuse
        # of this generator). See class docstring for more info.
        self._used: bool = False

    def _build_schema_type_to_method(
        self,
    ) -> dict[CoreSchemaOrFieldType, Callable[[CoreSchemaOrField], None]]:
        """Builds a dictionary mapping schema and field types to methods for
            constructing the LinkML slot schema contained in the current instance

        Returns:
            A dictionary containing the mapping of `CoreSchemaOrFieldType` to a
                handler method for constructing the LinkML slot schema for that type.

        Raises:
            TypeError: If no method has been defined for constructing the slot schema
                for one of the schema or field types
        """
        mapping: dict[CoreSchemaOrFieldType, Callable[[CoreSchemaOrField], None]] = {}
        core_schema_types: list[CoreSchemaOrFieldType] = literal_values(
            CoreSchemaOrFieldType  # type: ignore
        )
        for key in core_schema_types:
            method_name = f"_{key.replace('-', '_')}_schema"
            try:
                mapping[key] = getattr(self, method_name)
            except AttributeError as e:  # pragma: no cover
                raise TypeError(
                    f"No method for constructing the slot schema for "
                    f"core_schema.type={key!r} "
                    f"(expected: {type(self).__name__}.{method_name})"
                ) from e
        return mapping

    def generate(self) -> SlotDefinition:
        """
        Generate a LinkML slot schema from the Pydantic model field schema provided to
            this generator.

        :return: The generated LinkML slot schema
        """
        if self._used:
            raise GeneratorReuseError(self)

        # Initialized the `required` meta slot to `True` since all
        # Pydantic fields are required unless a default value is provided
        self._slot.required = True

        # Shape the contained slot according to core schema of the corresponding field
        self._shape_slot(self._field_schema.schema)

        self._used = True
        return self._slot

    def _shape_slot(self, schema: CoreSchemaOrField) -> None:
        """
        Shape the slot definition contained in this generator
            per the schema provided

        Note:
             This method is inspired by
                `pydantic.json_schema.GenerateJsonSchema.generate_inner()`
        """
        shape_slot_for_specific_schema_type = self._schema_type_to_method[
            schema["type"]
        ]
        shape_slot_for_specific_schema_type(schema)

    def _attach_note(self, note: str) -> None:
        """
        Attach a note to the contained slot definition

        :param note: The note to attach
        """
        self._slot.notes.append(f"{__package__}: {note}")

    def _any_schema(self, _schema: core_schema.AnySchema) -> None:
        """
        Shape the contained slot definition to match any value

        :param _schema: The core schema
        """
        self._slot.range = any_class_def.name

    def _none_schema(self, _schema: core_schema.NoneSchema) -> None:
        """
        Shape the contained slot definition to match `core_schema.NoneSchema`

        :param _schema: The `core_schema.NoneSchema` representing the `None` value
            restriction

        Note in the contained slot definition that the corresponding field in
        a Pydantic model is restricted to `NoneType` yet LinkML does not have
        null values

        Note: Currently, this method does not add any restriction to the contained slot.
        """
        self._attach_note(
            "LinkML does not have null values. "
            "(For details, see https://github.com/orgs/linkml/discussions/1975)."
        )

    def _bool_schema(self, _schema: core_schema.BoolSchema) -> None:
        """
        Shape the contained slot definition to match a Boolean value

        :param _schema: The `core_schema.BoolSchema` representing the boolean value
            restriction
        """
        self._slot.range = "boolean"

    def _int_schema(self, schema: core_schema.IntSchema) -> None:
        """
        Shape the contained slot definition to match an integer value

        :param schema: The `core_schema.IntSchema` representing the integer value
            restriction
        """
        self._slot.range = "integer"

        if "multiple_of" in schema:
            self._attach_note(
                "Unable to express the restriction of being "
                f"a multiple of {schema['multiple_of']}."
            )
        if "le" in schema:
            self._slot.maximum_value = schema["le"]
        if "ge" in schema:
            self._slot.minimum_value = schema["ge"]
        if "lt" in schema:
            self._slot.maximum_value = (
                schema["lt"] - 1
                if self._slot.maximum_value is None
                else min(self._slot.maximum_value, schema["lt"] - 1)
            )
        if "gt" in schema:
            self._slot.minimum_value = (
                schema["gt"] + 1
                if self._slot.minimum_value is None
                else max(self._slot.minimum_value, schema["gt"] + 1)
            )

    # noinspection DuplicatedCode
    def _float_schema(self, schema: core_schema.FloatSchema) -> None:
        """
        Shape the contained slot definition to match a float value

        :param schema: The `core_schema.FloatSchema` representing the float value
            restriction
        """
        self._slot.range = "float"
        if "allow_inf_nan" not in schema or schema["allow_inf_nan"]:
            self._attach_note(
                "LinkML does not have support for `'+inf'`, `'-inf'`, and `'NaN'` "
                "values. Support for these values is not translated."
            )
        if "multiple_of" in schema:
            self._attach_note(
                "Unable to express the restriction of being "
                f"a multiple of {schema['multiple_of']}."
            )
        if "le" in schema:
            self._slot.maximum_value = schema["le"]
        if "ge" in schema:
            self._slot.minimum_value = schema["ge"]
        if "lt" in schema:
            self._attach_note(
                f"Unable to express the restriction of being less than {schema['lt']}. "
                f"For details, see https://github.com/orgs/linkml/discussions/2144"
            )
        if "gt" in schema:
            self._attach_note(
                f"Unable to express the restriction of being greater than "
                f"{schema['gt']}. "
                f"For details, see https://github.com/orgs/linkml/discussions/2144"
            )

    # noinspection DuplicatedCode
    def _decimal_schema(self, schema: core_schema.DecimalSchema) -> None:
        """
        Shape the contained slot definition to match a decimal value

        :param schema: The `core_schema.DecimalSchema` representing the decimal value
            restriction
        """
        self._slot.range = "decimal"

        if schema.get("allow_inf_nan"):
            self._attach_note(
                "LinkML does not have support for `'+inf'`, `'-inf'`, and `'NaN'` "
                "values. Support for these values is not translated."
            )
        if "multiple_of" in schema:
            self._attach_note(
                "Unable to express the restriction of being "
                f"a multiple of {schema['multiple_of']}."
            )
        if "le" in schema:
            self._slot.maximum_value = schema["le"]
        if "ge" in schema:
            self._slot.minimum_value = schema["ge"]
        if "lt" in schema:
            self._attach_note(
                f"Unable to express the restriction of being less than {schema['lt']}. "
                f"For details, see https://github.com/orgs/linkml/discussions/2144"
            )
        if "gt" in schema:
            self._attach_note(
                f"Unable to express the restriction of being greater than "
                f"{schema['gt']}. "
                f"For details, see https://github.com/orgs/linkml/discussions/2144"
            )
        if "max_digits" in schema:
            self._attach_note(
                "Unable to express the restriction of max number "
                f"of {schema['max_digits']} digits within a `Decimal` value."
            )
        if "decimal_places" in schema:
            self._attach_note(
                "Unable to express the restriction of max number of "
                f"{schema['decimal_places']} decimal places within a `Decimal` value."
            )

    def _str_schema(self, schema: core_schema.StringSchema) -> None:
        """
        Shape the contained slot definition to match a string value

        :param schema: The `core_schema.StringSchema` representing the string value
            restriction
        """
        self._slot.range = "string"

        if "pattern" in schema:
            self._slot.pattern = schema["pattern"]

        max_length: Optional[int] = schema.get("max_length")
        min_length: Optional[int] = schema.get("min_length")

        if max_length is not None:
            self._attach_note(
                "LinkML does not have direct support for max length constraints. "
                f"The max length constraint of {max_length} is incorporated "
                "into the pattern of the slot."
            )

        if min_length is not None:
            self._attach_note(
                "LinkML does not have direct support for min length constraints. "
                f"The min length constraint of {min_length} is incorporated "
                "into the pattern of the slot."
            )

        # == Incorporate any length constraints into the pattern of the slot ==
        if max_length is not None or min_length is not None:
            length_constraint_regex = (
                f"^(?=."
                f"{{{min_length if min_length is not None else ''},"
                f"{max_length if max_length is not None else ''}}}$)"
            )

            orig_ptrn = self._slot.pattern
            if orig_ptrn is not None:
                # == There is an existing pattern carried over
                # from the Pydantic core schema ==

                # Update the pattern to include the length constraint
                self._slot.pattern = (
                    f"{length_constraint_regex}"
                    f"{orig_ptrn[1:] if orig_ptrn.startswith('^') else orig_ptrn}"
                )
            else:
                # == There is no existing pattern carried over
                # from the Pydantic core schema ==

                # Set the pattern to the length constraint
                self._slot.pattern = length_constraint_regex

        if schema.get("strip_whitespace"):
            self._attach_note(
                "Unable to express the option of "
                "stripping leading and trailing whitespace in LinkML."
            )
        if schema.get("to_lower"):
            self._attach_note(
                "Unable to express the option of converting the string to lowercase "
                "in LinkML."
            )
        if schema.get("to_upper"):
            self._attach_note(
                "Unable to express the option of converting the string to uppercase "
                "in LinkML."
            )
        if "regex_engine" in schema:
            # I believe nothing needs to be done here.
            # The regex engine mostly supports a subset of the standard regular
            # expressions. For more info,
            # see https://docs.pydantic.dev/latest/migration/#patterns-regex-on-strings.
            pass

    def _bytes_schema(self, schema: core_schema.BytesSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _date_schema(self, schema: core_schema.DateSchema) -> None:
        """
        Shape the contained slot definition to match a date value

        :param schema: The `core_schema.DateSchema` representing the date value
            restriction
        """
        self._slot.range = "date"

        if "le" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than or equal to "
                "a date. LinkML lacks direct support for this restriction."
            )
        if "ge" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than or equal to "
                "a date. LinkML lacks direct support for this restriction."
            )
        if "lt" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than a date. "
                "LinkML lacks direct support for this restriction."
            )
        if "gt" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than a date. "
                "LinkML lacks direct support for this restriction."
            )
        if "now_op" in schema:
            self._attach_note(
                "Unable to express the restriction of being before or after the "
                "current date. LinkML lacks direct support for this restriction."
            )
        if "now_utc_offset" in schema:
            self._attach_note(
                "Unable to express the utc offset of the current date "
                "in the restriction of being before or after the current date. "
                "LinkML lacks direct support for this restriction."
            )

    def _time_schema(self, schema: core_schema.TimeSchema) -> None:
        """
        Shape the contained slot definition to match a time value

        :param schema: The `core_schema.TimeSchema` representing the time value
            restriction
        """
        self._slot.range = "time"

        if "le" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than or equal to "
                "a time. LinkML lacks direct support for this restriction."
            )
        if "ge" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than or equal to "
                "a time. LinkML lacks direct support for this restriction."
            )
        if "lt" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than a time. "
                "LinkML lacks direct support for this restriction."
            )
        if "gt" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than a time. "
                "LinkML lacks direct support for this restriction."
            )
        if "tz_constraint" in schema:
            self._attach_note(
                f"Unable to express the timezone constraint of "
                f"{schema['tz_constraint']}. "
                f"LinkML lacks direct support for this restriction."
            )
        if "microseconds_precision" in schema:
            self._attach_note(
                f"Unable to express the microseconds precision constraint of "
                f"{schema['microseconds_precision']}. "
                "LinkML lacks direct support for this restriction."
            )

    def _datetime_schema(self, schema: core_schema.DatetimeSchema) -> None:
        """
        Shape the contained slot definition to match a datetime value

        :param schema: The `core_schema.DatetimeSchema` representing the datetime value
            restriction
        """
        self._slot.range = "datetime"

        if "le" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than or equal to "
                "a datetime. LinkML lacks direct support for this restriction."
            )
        if "ge" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than or equal to "
                "a datetime. LinkML lacks direct support for this restriction."
            )
        if "lt" in schema:
            self._attach_note(
                "Unable to express the restriction of being less than a datetime. "
                "LinkML lacks direct support for this restriction."
            )
        if "gt" in schema:
            self._attach_note(
                "Unable to express the restriction of being greater than a datetime. "
                "LinkML lacks direct support for this restriction."
            )
        if "now_op" in schema:
            self._attach_note(
                "Unable to express the restriction of being before or after the "
                "current datetime. LinkML lacks direct support for this restriction."
            )
        if "tz_constraint" in schema:
            self._attach_note(
                f"Unable to express the timezone constraint of "
                f"{schema['tz_constraint']}. "
                f"LinkML lacks direct support for this restriction."
            )
        if "now_utc_offset" in schema:
            self._attach_note(
                "Unable to express the utc offset of the current datetime in "
                "the restriction of being before or after the current datetime. "
                "LinkML lacks direct support for this restriction."
            )
        if "microseconds_precision" in schema:
            self._attach_note(
                f"Unable to express the microseconds precision constraint of "
                f"{schema['microseconds_precision']}. "
                "LinkML lacks direct support for this restriction."
            )

    def _timedelta_schema(self, schema: core_schema.TimedeltaSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _literal_schema(self, schema: core_schema.LiteralSchema) -> None:
        """
        Shape the contained slot definition to allow only a specific set of values

        :param schema: The `core_schema.LiteralSchema` representing a set of literal
            values that the slot can take
        """
        # Check if the types of the given literals are supportable
        expected: list[Any] = schema["expected"]
        literal_types = {type(literal) for literal in expected}
        if not literal_types.issubset({str, int}):
            self._attach_note(
                "Unable to express the restriction of being one of the elements in "
                f"`{expected}`. LinkML has direct support for only string "
                f"and integer elements in expressing such a restriction."
            )
        else:
            self._slot.range = "Any"
            self._slot.any_of = [
                (
                    AnonymousSlotExpression(equals_string=literal, range="string")
                    if type(literal) is str
                    else AnonymousSlotExpression(equals_number=literal, range="integer")
                )
                for literal in expected
            ]

    def _enum_schema(self, schema: core_schema.EnumSchema) -> None:
        """
        Shape the contained slot definition to match an enum value

        :param schema: The `core_schema.EnumSchema` representing the enum type the
            value belongs to
        """
        enum_name = schema["cls"].__name__

        self._slot.range = enum_name
        if "missing" in schema:
            self._attach_note(
                f"Unable to express calling {schema['missing'].__name__} in LinkML "
                f"when the provide value is not found in the enum type, {enum_name}."
            )

    def _is_instance_schema(self, schema: core_schema.IsInstanceSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _is_subclass_schema(self, schema: core_schema.IsSubclassSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _callable_schema(self, schema: core_schema.CallableSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _list_schema(self, schema: core_schema.ListSchema) -> None:
        """
        Shape the contained slot definition to match a list value

        :param schema: The `core_schema.ListSchema` representing
            the list value restriction
        """
        if self._slot.multivalued:
            # === This must be a nested list type ===
            self._attach_note(
                "Translation is incomplete." "Nested list types are not yet supported."
            )
            return

        self._slot.multivalued = True
        if "min_length" in schema:
            self._slot.minimum_cardinality = schema["min_length"]
        if "max_length" in schema:
            self._slot.maximum_cardinality = schema["max_length"]
        if "items_schema" in schema:
            self._shape_slot(schema["items_schema"])

    def _tuple_schema(self, schema: core_schema.TupleSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _set_schema(self, schema: core_schema.SetSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _frozenset_schema(self, schema: core_schema.FrozenSetSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _generator_schema(self, schema: core_schema.GeneratorSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _dict_schema(self, schema: core_schema.DictSchema) -> None:
        """
        Shape the contained slot definition to match restrictions of a dictionary value

        :param schema: The `core_schema.DictSchema` representing restrictions
        """
        # TODO: the current implementation is just an annotation
        #   A usable implementation is yet to be decided. Useful information
        #   can be found at, https://github.com/orgs/linkml/discussions/2239
        self._attach_note(
            "Warning: The translation is incomplete. `dict` types are yet to be "
            "supported."
        )

    def _function_schema(
        self,
        schema: Union[
            core_schema.AfterValidatorFunctionSchema,
            core_schema.BeforeValidatorFunctionSchema,
            core_schema.WrapValidatorFunctionSchema,
            core_schema.PlainValidatorFunctionSchema,
        ],
    ) -> None:
        """
        A helper method that shapes the contained slot definition to provide
            the restriction set by a validation function

        :param schema: The schema representing the validation function
        """
        mode = schema["type"].split("-")[1]

        self._attach_note(
            "Unable to translate the logic contained in "
            f"the {mode} validation function, {schema['function']['function']!r}."
        )
        if mode != "plain":
            self._shape_slot(schema["schema"])

    def _function_after_schema(
        self, schema: core_schema.AfterValidatorFunctionSchema
    ) -> None:
        """
        Shape the contained slot definition to provide the restriction set by an after
            validation function

        :param schema: The schema representing the after validation function
        """
        self._function_schema(schema)

    def _function_before_schema(
        self, schema: core_schema.BeforeValidatorFunctionSchema
    ) -> None:
        """
        Shape the contained slot definition to provide the restriction set by a before
            validation function

        :param schema: The schema representing the before validation function
        """
        self._function_schema(schema)

    def _function_wrap_schema(
        self, schema: core_schema.WrapValidatorFunctionSchema
    ) -> None:
        """
        Shape the contained slot definition to provide the restriction set by a wrap
            validation function

        :param schema: The schema representing the wrap validation function
        """
        self._function_schema(schema)

    def _function_plain_schema(
        self, schema: core_schema.PlainValidatorFunctionSchema
    ) -> None:
        """
        Shape the contained slot definition to provide the restriction set by a plain
            validation function

        :param schema: The schema representing the plain validation function
        """
        self._function_schema(schema)

    def _default_schema(self, schema: core_schema.WithDefaultSchema) -> None:
        """
        Shape the contained slot definition to have a default value

        :param schema: The `core_schema.WithDefaultSchema` representing the default
            value specification
        """
        inner_schema = schema["schema"]

        self._slot.required = False
        if "default" in schema and (default := schema["default"]) is not None:
            # === Set `ifabsent` meta slot ===
            default_type = type(default)
            if default_type is bool:
                self._slot.ifabsent = str(default)
            elif default_type is int:
                self._slot.ifabsent = f"int({default})"
            elif default_type is str:
                self._slot.ifabsent = f"string({default})"
            elif default_type is float:
                self._slot.ifabsent = f"float({default})"
            elif default_type is date:
                self._slot.ifabsent = f"date({default})"
            else:
                self._attach_note(
                    f"Unable to set a default value of {default!r} in LinkML. "
                    f"Default values of type {default_type} are not supported."
                )

            if inner_schema["type"] == "nullable":
                self._attach_note(
                    "Warning: LinkML doesn't have a null value. "
                    "The translation of `Optional` in Python may need further "
                    "adjustments."
                )
        if "default_factory" in schema:
            self._attach_note(
                "Unable to express the default factory, "
                f"{schema['default_factory']!r}, in LinkML."
            )
        if "on_error" in schema and schema["on_error"] != "raise":
            self._attach_note(
                "Unable to express the `on_error` option of "
                f"{schema['on_error']} in LinkML."
            )
        if "validate_default" in schema:
            # This is purposely left empty.
            # LinkML validates the default value of a slot, provided by the `ifabsent`
            # meta slot, no matter what. In the case of `schema['validate_default']`
            # being `False`, the default, LinkML's behavior is just stricter, and
            # attaching a note to the slot about not able to express
            # `schema['validate_default']` being `False` would generate too much
            # clutter.
            pass

        self._shape_slot(inner_schema)

    def _nullable_schema(self, schema: core_schema.NullableSchema) -> None:
        """
        Shape the contained slot definition to match a nullable value restriction

        :param schema: The schema representing the nullable value restriction

        Note: There is no null value in LinkML
              (https://github.com/orgs/linkml/discussions/1975).
        """
        if self._slot.required:
            # === The field being translated must have no default value ===

            self._attach_note(
                "Warning: LinkML doesn't have a null value. "
                "The translation of `Optional` for a required field may require "
                "further adjustments."
            )

        # Note: The case of `self._slot.required` being `False` is handled in
        #   `SlotGenerator._default_schema()

        self._shape_slot(schema["schema"])

    def _union_schema(self, schema: core_schema.UnionSchema) -> None:
        """
        Shape the contained slot definition to match a union restriction

        :param schema: The schema representing the union restriction
        """
        # TODO: the current implementation doesn't address all cases of `Union` partly
        #   due to limitation of LinkML. Useful information
        #   can be found at, https://github.com/orgs/linkml/discussions/2154

        def get_model_slot_expression(
            schema_: core_schema.CoreSchema,
        ) -> AnonymousSlotExpression:
            return AnonymousSlotExpression(
                range=schema_["cls"].__name__,
            )

        # A map of supported type choices to the functions for generating the
        # corresponding slot expression
        supported_type_choices: dict[
            str, Callable[[core_schema.CoreSchema], AnonymousSlotExpression]
        ] = {"model": get_model_slot_expression}

        choices = schema["choices"]

        choice_slot_expressions = []
        for c in choices:
            # Exits early if a choice is a tuple
            if isinstance(c, tuple):
                self._attach_note(
                    f"Warning: The translation is incomplete. The union core schema "
                    f"contains a tuple as a choice. Tuples as choices are yet to be "
                    f"supported. (core schema: {schema})."
                )
                return

            # Exits early if a choice is of unsupported type
            c_type = c["type"]
            if c_type not in supported_type_choices:
                self._attach_note(
                    f"Warning: The translation is incomplete. The union core schema "
                    f"contains a choice of type {c_type}. The choice type is yet to be "
                    f"supported. (core schema: {schema})."
                )
                return

            choice_slot_expressions.append(supported_type_choices[c_type](c))

        self._slot.any_of = choice_slot_expressions

        # This is needed because of the monotonicity nature of constraints
        #   in LinkML. For more information,
        #   see https://linkml.io/linkml/schemas/advanced.html#unions-as-ranges
        self._slot.range = any_class_def.name

    def _tagged_union_schema(self, schema: core_schema.TaggedUnionSchema) -> None:
        """
        Shape the contained slot definition to match a tagged union restriction

        :param schema: The schema representing the tagged union restriction
        """
        # TODO: the current implementation is just an annotation
        #   A usable implementation is yet to be decided. Useful information
        #   can be found at, https://github.com/orgs/linkml/discussions/2154
        #   and https://linkml.io/linkml/schemas/type-designators.html
        self._attach_note(
            "Warning: The translation is incomplete. Tagged union types are yet to be "
            "supported."
        )

    def _chain_schema(self, schema: core_schema.ChainSchema) -> None:
        """
        Shape the contained slot definition to match the restrictions specified by
            a `core_schema.ChainSchema`, which represents a chain of Pydantic core
            schemas
        :param schema: The `core_schema.ChainSchema`

        Note: Models can often be defined to avoid having a field with a chain schema.
            For example, in the following model, only field `y` has a chain schema.
            The type annotation of field `y`, through examination of the
            chain schema, can be argued incorrect; in the sense that no `int` can pass
            the validation for field `y`. The annotation of field `z` is well suited for
            field `y` for its intended purpose.

            ```python
            from typing import Union, Annotated

            from pydantic import BaseModel, Field, StringConstraints


            class Foo(BaseModel):
                x: str = Field(..., pattern=r"^[0-9a-z]+$")
                y: Union[int, str] = Field(..., pattern=r"^[0-9a-z]+$")
                z: Union[int, Annotated[str, StringConstraints(pattern=r"^[0-9a-z]+$")]]
            ```
        """
        self._attach_note(
            "Warning: Pydantic core schema of type `'chain'` is encountered. "
            "The translation may be less accurate. Often, the type annotation of the "
            "corresponding field in the corresponding Pydantic model can be improved."
        )

        for schema_in_chain in schema["steps"]:
            self._shape_slot(schema_in_chain)

    def _lax_or_strict_schema(self, schema: core_schema.LaxOrStrictSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _json_or_python_schema(self, schema: core_schema.JsonOrPythonSchema) -> None:
        """
        Shape the contained slot definition to match the restrictions specified by
            a `core_schema.JsonOrPythonSchema`
        :param schema: The `core_schema.JsonOrPythonSchema`

        Note: Since the restrictions specified by the inner `json_schema` is more
            readily translatable to LinkML in comparison to the restrictions specified
            by the inner `python_schema`, the `json_schema` is used to continue shaping
            the contained slot definition.
        """
        self._shape_slot(schema["json_schema"])

    def _typed_dict_schema(self, schema: core_schema.TypedDictSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _model_fields_schema(self, schema: core_schema.ModelFieldsSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _model_schema(self, schema: core_schema.ModelSchema) -> None:
        """
        Shape the contained slot definition to match an instance of a model, or class
            in LinkML

        :param schema: The schema representing the model
        """
        self._slot.range = schema["cls"].__name__

    def _dataclass_args_schema(self, schema: core_schema.DataclassArgsSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _dataclass_schema(self, schema: core_schema.DataclassSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _arguments_schema(self, schema: core_schema.ArgumentsSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _call_schema(self, schema: core_schema.CallSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _custom_error_schema(self, schema: core_schema.CustomErrorSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _json_schema(self, schema: core_schema.JsonSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _url_schema(self, schema: core_schema.UrlSchema) -> None:
        """
        Shape the contained slot definition to match a URL restriction

        :param schema: The schema representing the URL restriction
        """
        # This method may be further improved or implemented more fully upon the
        # resolution of https://github.com/linkml/linkml/issues/2215

        self._slot.range = "uri"

        # Incorporate `max_length` and `allowed_schemes` restrictions into the pattern
        # meta slot
        max_length: Optional[int] = schema.get("max_length")
        allowed_schemes: Optional[list[str]] = schema.get("allowed_schemes")
        max_length_re = rf"(?=.{{,{max_length}}}$)" if max_length is not None else ""
        allowed_schemes_re = (
            rf"(?i:{'|'.join(re.escape(scheme) for scheme in allowed_schemes)})"
            if allowed_schemes is not None
            else r"[^\s]+"
        )
        self._slot.pattern = rf"^{max_length_re}{allowed_schemes_re}://[^\s]+$"

        if "host_required" in schema:
            self._attach_note("Unable to express the `host_required` option in LinkML.")
        if "default_host" in schema:
            self._attach_note("Unable to express the `default_host` option in LinkML.")
        if "default_port" in schema:
            self._attach_note("Unable to express the `default_port` option in LinkML.")
        if "default_path" in schema:
            self._attach_note("Unable to express the `default_path` option in LinkML.")

    def _multi_host_url_schema(self, schema: core_schema.MultiHostUrlSchema) -> None:
        raise TranslationNotImplementedError(schema)

    def _definitions_schema(self, schema: core_schema.DefinitionsSchema) -> None:
        """
        Shape the contained slot definition to match a `core_schema.DefinitionsSchema`

        :param schema: The `core_schema.DefinitionsSchema`
        """
        self._shape_slot(resolve_ref_schema(schema, self._field_schema.context))

    def _definition_ref_schema(
        self, schema: core_schema.DefinitionReferenceSchema
    ) -> None:
        """
        Shape the contained slot definition to match
        a `core_schema.DefinitionReferenceSchema`

        :param schema: The `core_schema.DefinitionsSchema`
        """
        self._shape_slot(resolve_ref_schema(schema, self._field_schema.context))

    def _uuid_schema(self, schema: core_schema.UuidSchema) -> None:
        """
        Shape the contained slot definition to match a UUID restriction

        :param schema: The schema representing the UUID restriction
        """
        self._slot.range = "string"
        self._slot.pattern = get_uuid_regex(schema.get("version"))

    def _model_field_schema(self, schema: core_schema.ModelField) -> None:
        raise TranslationNotImplementedError(schema)

    def _dataclass_field_schema(self, schema: core_schema.DataclassField) -> None:
        raise TranslationNotImplementedError(schema)

    def _typed_dict_field_schema(self, schema: core_schema.TypedDictField) -> None:
        raise TranslationNotImplementedError(schema)

    def _computed_field_schema(self, schema: core_schema.ComputedField) -> None:
        raise TranslationNotImplementedError(schema)

    if pydantic_version >= version.parse("2.9"):
        # Methods define when Pydantic version is 2.9 or later
        def _complex_schema(self, schema: core_schema.ComplexSchema) -> None:
            raise TranslationNotImplementedError(schema)


def translate_defs(module_names: Iterable[str]) -> SchemaDefinition:
    """
    Translate Python objects, in the named modules and their submodules loaded to
    `sys.modules`, to LinkML

    :param module_names: The names to specify the modules and their submodules
    :return: A `SchemaDefinition` object representing the expressions of the
        Python objects in LinkML

    Note: The types of objects translated are those fetched by `tools.fetch_defs()`
    """
    # Eliminate duplicates in the module names
    if type(module_names) is not set:
        module_names = set(module_names)

    modules = get_all_modules(module_names)
    logger.info(
        "Considering %d modules for provided %d modules: %s",
        len(modules),
        len(module_names),
        module_names,
    )
    models, enums = fetch_defs(modules)
    logger.info("Fetched %d models and %d enums", len(models), len(enums))
    generator = LinkmlGenerator(models=models, enums=enums)
    logger.info("Generating schema")
    schema = generator.generate()
    return schema
