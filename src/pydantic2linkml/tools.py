import importlib
import inspect
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import fields
from enum import Enum
from operator import attrgetter, itemgetter
from types import ModuleType
from typing import Any, NamedTuple, Optional, TypeVar, cast, overload

import yaml
from linkml_runtime.linkml_model import SchemaDefinition, SlotDefinition
from linkml_runtime.utils.formatutils import is_empty
from pydantic import BaseModel, FilePath, RootModel, validate_call

# noinspection PyProtectedMember
from pydantic._internal import _core_utils
from pydantic.fields import FieldInfo
from pydantic_core import core_schema

from pydantic2linkml.exceptions import (
    NameCollisionError,
    OverlayContentError,
    SlotExtensionError,
)

logger = logging.getLogger(__name__)


class StrEnum(str, Enum):
    pass


class FieldSchema(NamedTuple):
    # The resolved Pydantic core schema of the field
    schema: core_schema.CoreSchema

    # The context in which the Pydantic core schema of the field was defined
    # (i.e. the Pydantic core schema of the model that defined the field).
    # This context is needed to resolve any references in the field schema.
    context: core_schema.CoreSchema

    # The name of the field in the Pydantic model
    field_name: str

    # The `FieldInfo` object representing the field in the Pydantic model
    field_info: FieldInfo

    # The Pydantic model in which the field is defined
    model: type[BaseModel]


class LocallyDefinedFields(NamedTuple):
    new: dict[str, FieldSchema]
    overriding: dict[str, FieldSchema]


def get_parent_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    """
    Get the parent Pydantic models of a Pydantic model

    :param model: The Pydantic model. Note: This can't be `pydantic.BaseModel`.
        `pydantic.BaseModel` is really an empty model and shouldn't be translated to a
        LinkML class.
    :return: The list of parent Pydantic models of the input model.
        Note: `pydantic.BaseModel` is not considered to be a parent model for it is
            really an empty model.

    raises ValueError: If the input model is `pydantic.BaseModel`

    Note: The order of the parent models returned is the models' order in the definition
        of the input model.
    """
    if model is BaseModel:
        msg = "`model` cannot be `pydantic.BaseModel`"
        raise ValueError(msg)
    return [
        b for b in model.__bases__ if issubclass(b, BaseModel) and b is not BaseModel
    ]


def resolve_ref_schema(
    maybe_ref_schema: core_schema.CoreSchema,
    context: core_schema.CoreSchema,
) -> core_schema.CoreSchema:
    """
    Resolves reference in the core schema.

    :param maybe_ref_schema: A `CoreSchema` object that's possibly a reference,
        i.e. a `DefinitionsSchema` or a `DefinitionReferenceSchema`.
    :param context: A `CoreSchema` in which the `maybe_ref_schema` is defined.
        This can be the same object as `maybe_ref_schema`.
    :return: The resolved `CoreSchema` object.

    :raises ValueError: If `context` is not a `DefinitionsSchema` object when
        `maybe_ref_schema` is a `DefinitionsSchema` or `DefinitionReferenceSchema`.
    :raises RuntimeError: If the referenced schema is not found in the provided context.

    Note:
        This function mimics `resolve_ref_schema` in
        `pydantic._internal._schema_generation_shared.CallbackGetCoreSchemaHandler`
    """
    schema_type = maybe_ref_schema["type"]

    if schema_type == "definitions" or schema_type == "definition-ref":
        if context["type"] != "definitions":
            raise ValueError(
                "`context` must be a `DefinitionsSchema` object when "
                "`maybe_ref_schema` is a `DefinitionsSchema` "
                "or `DefinitionReferenceSchema`."
            )

    if schema_type == "definition-ref":
        context = cast(core_schema.DefinitionsSchema, context)
        ref = maybe_ref_schema["schema_ref"]
        for schema in context["definitions"]:
            if schema["ref"] == ref:
                return schema
        raise RuntimeError(f"Referenced schema by {ref} not found in provided context")
    elif schema_type == "definitions":
        return resolve_ref_schema(maybe_ref_schema["schema"], context)
    return maybe_ref_schema


def strip_function_schema(
    schema: _core_utils.AnyFunctionSchema,
) -> core_schema.CoreSchema:
    """
    Strip the outermost schema of a function schema

    :param schema: The function schema
    :return: The inner schema of the function schema
    :raises ValueError: If the given function schema is not a function with an inner
        schema
    """

    if _core_utils.is_function_with_inner_schema(schema):
        return schema["schema"]
    else:
        raise ValueError(
            "The given function schema is not a function with an inner schema. "
            "No outer schema to strip."
        )


# A mapping from unneeded wrapping schema types around `ModelSchema` to functions that
# strip the outermost unneeded wrapping schema. The set of schema types deemed as
# unneeded may change in the future if we are able to harvest the information in any of
# the wrapping schema types.
_strip_core_schema = cast(
    Callable[[core_schema.CoreSchema], core_schema.CoreSchema],
    strip_function_schema,
)
UNNEEDED_WRAPPING_SCHEMA_TYPE_TO_STRIP_FUNC: dict[
    str, Callable[[core_schema.CoreSchema], core_schema.CoreSchema]
] = {
    "function-before": _strip_core_schema,
    "function-after": _strip_core_schema,
    "function-wrap": _strip_core_schema,
    "function-plain": _strip_core_schema,
}


def strip_unneeded_wrapping_schema(
    schema: core_schema.CoreSchema,
) -> core_schema.CoreSchema:
    """
    Strip the outermost unneeded wrapping schema

    :param schema: The schema to be stripped
    :return: The inner schema of the given schema if the outermost schema of the given
        schema is an unneeded wrapping schema. Otherwise, the given schema itself is
        returned.
    """
    schema_type = schema["type"]

    if schema_type in UNNEEDED_WRAPPING_SCHEMA_TYPE_TO_STRIP_FUNC:
        return UNNEEDED_WRAPPING_SCHEMA_TYPE_TO_STRIP_FUNC[schema_type](schema)
    else:
        return schema


def get_model_schema(model: type[BaseModel]) -> core_schema.ModelSchema:
    """
    Get the corresponding `core_schema.ModelSchema` of a Pydantic model

    :param model: The Pydantic model
    """
    raw_model_schema = model.__pydantic_core_schema__
    model_schema = raw_model_schema

    while True:
        model_schema = resolve_ref_schema(model_schema, context=raw_model_schema)

        # Strip an unneeded wrapping schema
        inner_schema = strip_unneeded_wrapping_schema(model_schema)

        if inner_schema is model_schema:
            # Exit while-loop if no stripping is done, i.e. `model_schema` is already
            # devoid of any unneeded wrapping schema
            break
        else:
            model_schema = inner_schema

    assert model_schema["type"] == "model", (
        "Assumption about how model schema is stored is wrong."
    )

    return cast(core_schema.ModelSchema, model_schema)


def get_field_schema(model: type[BaseModel], fn: str) -> FieldSchema:
    """
    Get the `FieldSchema` wrapping the resolved Pydantic core schema of a field
    in a Pydantic model

    :param model: The Pydantic model
    :param fn: The name of the field
    :return: The Pydantic core schema of the field

    Note: The returned schema is guaranteed to be resolved, i.e. it is not a reference
        schema.
    """

    # The `FieldInfo` object representing the field in the Pydantic model
    field_info: FieldInfo = model.model_fields[fn]

    # The `core_schema.ModelSchema` of the Pydantic model
    model_schema = get_model_schema(model)

    if model_schema["schema"]["type"] == "model-fields":
        model_field = cast(core_schema.ModelFieldsSchema, model_schema["schema"])[
            "fields"
        ][fn]

        assert model_field["type"] == "model-field"

        model_pydantic_core_schema = model.__pydantic_core_schema__
        return FieldSchema(
            schema=resolve_ref_schema(
                model_field["schema"],
                context=model_pydantic_core_schema,
            ),
            context=model_pydantic_core_schema,
            field_name=fn,
            field_info=field_info,
            model=model,
        )
    else:
        raise NotImplementedError(
            f"This function currently doesn't support the inner schema of "
            f'a `ModelSchema` being the type of "{model_schema["schema"]["type"]}"'
        )


def get_locally_defined_fields(model: type[BaseModel]) -> LocallyDefinedFields:
    """
    Get the fields defined in a Pydantic model that are not inherited

    :param model: The Pydantic model
    :return:
        A tuple of two dictionaries:
            The first contains the fields that are newly defined in this model as keys.
            The second contains the fields that are redefined (overriding) in this model
                as keys.
            The values in both dictionaries are `FieldSchema` objects representing the
                Pydantic core schemas of respective fields in context.
    """
    # Names of locally defined fields
    locally_defined_fns = set(model.model_fields).intersection(model.__annotations__)

    # Names of newly defined fields
    new_fns = locally_defined_fns.difference(
        *(pm.model_fields for pm in get_parent_models(model))
    )

    # Names of overriding fields
    overriding_fns = locally_defined_fns - new_fns

    return LocallyDefinedFields(
        new={fn: get_field_schema(model, fn) for fn in new_fns},
        overriding={fn: get_field_schema(model, fn) for fn in overriding_fns},
    )


T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


@overload
def bucketize(
    items: Iterable[T],
    key_func: Callable[[T], K],
) -> defaultdict[K, list[T]]: ...


@overload
def bucketize(
    items: Iterable[T],
    key_func: Callable[[T], K],
    value_func: Callable[[T], V],
) -> defaultdict[K, list[V]]: ...


def bucketize(
    items: Iterable[T],
    key_func: Callable[[T], K],
    value_func: Optional[Callable[[T], V]] = None,
) -> defaultdict[K, list[Any]]:
    """
    Bucketize items based on a key function

    :param items: The items to bucketize
    :param key_func: The key function
    :param value_func: An optional function to transform the items before storing to
        the corresponding buckets identified by the corresponding keys
    :return: A dictionary with keys as the results of the key function and values as
        the list of (transformed) items that have the corresponding key
    """
    buckets: defaultdict[K, list[Any]] = defaultdict(list)
    for item in items:
        key = key_func(item)
        buckets[key].append(item if value_func is None else value_func(item))
    return buckets


def ensure_unique_names(*clses: type) -> None:
    """
    In the context of the collection of all classes given as an argument,
    ensure all of them have a unique name.

    :param clses: The classes given as an argument packed in a tuple

    :raises NameCollisionError: If there are classes with the same name
    """
    # Sort classes into buckets by name
    buckets: dict[str, list[type]] = bucketize(clses, attrgetter("__name__"))

    # Build error message for any name collisions
    err_msg: Optional[str] = None
    for name, lst in buckets.items():
        if len(lst) > 1:
            new_err_msg = f"Name collision @ {name}: {lst!r}"
            err_msg = new_err_msg if err_msg is None else f"{err_msg}; {new_err_msg}"

    if err_msg is not None:
        raise NameCollisionError(err_msg)


def normalize_whitespace(text: str) -> str:
    """
    Return a version of the input text with leading and trailing whitespaces removed
    and sequences of consecutive whitespaces replaced with a single space.
    """
    return re.sub(r"\s+", " ", text.strip())


# TODO: write tests for this function
def get_all_modules(module_names: Iterable[str]) -> set[ModuleType]:
    """
    Get the named modules and their submodules loaded to `sys.modules`

    :param module_names: The names of the modules
    :return: The named modules and their submodules loaded to `sys.modules`
    """
    # Eliminate duplicates in the module names
    if type(module_names) is not set:
        module_names = set(module_names)

    modules: list[ModuleType] = []

    # Pre-import all the modules of given names first, so we have no order effects
    # etc. Note: This will load some of the submodules of these modules to
    # `sys.modules` as well.
    for module_name in module_names:
        importlib.import_module(module_name)

    # Collect all the modules of given names and their submodules loaded to
    # `sys.modules`
    for module_name in module_names:
        modules.extend(
            m
            for name, m in sys.modules.items()
            if name == module_name or name.startswith(module_name + ".")
        )

    return set(modules)


def fetch_defs(
    modules: Iterable[ModuleType],
) -> tuple[set[type[BaseModel]], set[type[Enum]]]:
    """
    Fetch Python objects that provide schema definitions from given modules

    :param modules: The given modules
    :return: A tuple of two sets:
        The first set contains strict subclasses of `pydantic.BaseModel` that is not
            a subclass of `pydantic.RootModel` in the given modules
        The second set contains strict subclasses of `enum.Enum` in the given modules
    """

    models: set[type[BaseModel]] = set()
    enums: set[type[Enum]] = set()

    for module in modules:
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, BaseModel)
                and cls is not BaseModel
                and not issubclass(cls, RootModel)
            ):
                models.add(cls)
            elif issubclass(cls, Enum) and cls is not Enum:
                enums.add(cls)

    return models, enums


def get_uuid_regex(version: Optional[int] = None) -> str:
    """
    Get the regular expression for UUIDs. If a version is provided, the regular
    expression will be specific to that version.

    :param version: The optional version number that is 1, 3, 4, or 5 (version supported
        by the Python `uuid` module)
    """
    if version is None:
        return (
            r"^(?:urn:uuid:)?"  # Optional "urn:uuid:" prefix
            r"[0-9a-fA-F]{8}-?"  # 8 hex digits with optional hyphen
            r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
            r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
            r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
            r"[0-9a-fA-F]{12}$"  # 12 hex digits
        )
    elif version in {1, 3, 4, 5}:
        return (
            r"^(?:urn:uuid:)?"  # Optional "urn:uuid:" prefix
            r"[0-9a-fA-F]{8}-?"  # 8 hex digits with optional hyphen
            r"[0-9a-fA-F]{4}-?"  # 4 hex digits with optional hyphen
            # Version and 3 hex digits with optional hyphen
            rf"{version}[0-9a-fA-F]{{3}}-?"
            r"[89abAB][0-9a-fA-F]{3}-?"  # Variant and 3 hex digits with optional hyphen
            r"[0-9a-fA-F]{12}$"  # 12 hex digits
        )
    else:
        raise ValueError("Invalid UUID version number")


def force_to_set(iterable: Optional[Iterable[T]]) -> set[T]:
    """
    Force an iterable of elements to a set if it is not already a set

    Note: If the input is `None`, an empty set is returned.

    :param iterable: The iterable of elements or `None`

    :return: The set of elements in the iterable or an empty set if the input is `None`
            If the input is already a set, it is returned as is (for performance).

    Usage: This function is useful to remove duplicates in an iterable.
    """
    if iterable is None:
        result = set()
    elif not isinstance(iterable, set):
        result = set(iterable)
    else:
        result = iterable
    return result


def sort_dict(
    d: dict[K, V], key_func: Callable[[tuple[K, V]], Any] = itemgetter(0)
) -> dict[K, V]:
    """
    Sort a dictionary by the key defined by a given key function

    :param d: The dictionary to be sorted
    :param key_func: The key function defining the key used to sort on the key-value
        pairs of the dictionary. If not provided, this defaults to the function that
        returns the key of each key-value pair. I.e., the dictionary is sorted by the
        key of each key-value pair.
    :return: A new dictionary that is the sorted version of the provided dictionary
    """
    return dict(sorted(d.items(), key=key_func))


def get_non_empty_meta_slots(slot: SlotDefinition) -> set[str]:
    """
    Get the names of the non-empty meta slots of a slot definition

    :param slot: The slot definition
    :return: The names of the non-empty meta slots of the slot definition
    """
    non_empty_meta_slots = set()
    for f in fields(SlotDefinition):
        meta_slot_name = f.name
        if not is_empty(getattr(slot, meta_slot_name)):
            non_empty_meta_slots.add(meta_slot_name)
    return non_empty_meta_slots


def get_slot_usage_entry(
    base: SlotDefinition, target: SlotDefinition
) -> Optional[SlotDefinition]:
    """
    Obtain a slot usage entry that extends (refines) the base slot definition,
    in a class definition, to achieve the behavior of the target slot definition

    :param base: The base slot definition
    :param target: The target slot definition

    :return: The slot usage entry that extends the base slot definition to achieve the
        behavior of the target slot definition. If the base slot definition doesn't
        need an extension to achieve the behavior of the target slot definition, i.e.,
        the base slot definition and the target slot definition are identical, `None` is
        returned.

    :raises SlotExtensionError: If the given base slot definition cannot be extended to
        achieve the behavior of the given target slot definition through a slot usage
        entry in a class definition
    """
    base_properties = get_non_empty_meta_slots(base)
    target_properties = get_non_empty_meta_slots(target)

    missing_properties = base_properties - target_properties
    common_properties = base_properties & target_properties

    varied_properties = set()
    for p in common_properties:
        if getattr(base, p) != getattr(target, p):
            varied_properties.add(p)

    if missing_properties or varied_properties:
        raise SlotExtensionError(
            missing_meta_slots=sorted(missing_properties, key=str.casefold),
            varied_meta_slots=sorted(varied_properties, key=str.casefold),
        )

    extended_properties = target_properties - base_properties

    if not extended_properties:
        return None

    # Note: A `name` argument is provided because the `SlotDefinition` class requires it
    return SlotDefinition(
        name=base.name, **{p: getattr(target, p) for p in extended_properties}
    )


@validate_call
def apply_schema_overlay(schema_yml: str, overlay_file: FilePath) -> str:
    """Apply an overlay YAML file onto a serialized schema YAML string.

    :param schema_yml: YAML string of a serialized SchemaDefinition
    :param overlay_file: Path to an existing overlay YAML file
    :return: YAML string with the overlay applied, keys ordered to match
        SchemaDefinition field order
    :raises ValueError: If ``schema_yml`` does not deserialize to a dict
    :raises OverlayContentError: If the overlay file does not contain a YAML
        mapping
    """
    schema_dict = yaml.safe_load(schema_yml)
    if not isinstance(schema_dict, dict):
        raise ValueError(
            f"schema_yml did not deserialize to a dict: {type(schema_dict)}"
        )

    with overlay_file.open() as f:
        overlay = yaml.safe_load(f)

    if not isinstance(overlay, dict):
        raise OverlayContentError(
            f"Overlay file {overlay_file} must contain a YAML mapping"
        )

    # Ordered list of valid SchemaDefinition field names
    sd_field_names = [f.name for f in fields(SchemaDefinition)]
    sd_field_set = set(sd_field_names)

    # Apply overlay, skipping keys that are not SchemaDefinition fields
    for k, v in overlay.items():
        if k not in sd_field_set:
            logger.warning(
                "Overlay key '%s' is not a field of SchemaDefinition. Skipping.",
                k,
            )
        else:
            schema_dict[k] = v

    # Rebuild dict in SchemaDefinition field order
    ordered = {k: schema_dict[k] for k in sd_field_names if k in schema_dict}

    return yaml.dump(ordered, allow_unicode=True, sort_keys=False)


def remove_schema_key_duplication(yml: str) -> str:
    """Remove redundant name/text fields from a valid serialized LinkML schema.

    In LinkML's serialized YAML, dictionary keys already serve as
    identifiers for classes, slots, enums, slot_usage entries, and
    permissible values. This function strips the redundant ``name`` and
    ``text`` fields that the linkml-runtime YAML dumper includes alongside
    those keys.

    :param yml: A YAML string representing a **valid** LinkML schema.
    """
    schema = yaml.safe_load(yml)

    for cls in schema.get("classes", {}).values():
        cls.pop("name", None)
        for su in cls.get("slot_usage", {}).values():
            su.pop("name", None)

    for slot in schema.get("slots", {}).values():
        slot.pop("name", None)

    for enum in schema.get("enums", {}).values():
        enum.pop("name", None)
        for pv in enum.get("permissible_values", {}).values():
            pv.pop("text", None)

    return yaml.dump(schema, allow_unicode=True, sort_keys=False)


def add_section_breaks(
    yml: str,
    keys: tuple[str, ...] = ("enums", "slots", "classes"),
    break_str: str = "\n",
) -> str:
    """Insert a break string before selected top-level keys in a YAML string.

    :param yml: A YAML string.
    :param keys: Top-level keys to precede with a break. Defaults to
        ``("enums", "slots", "classes")``.
    :param break_str: String prepended before each matched key line.
        Defaults to ``"\\n"``, producing a blank line.
    """
    if not keys:
        return yml

    pattern = r"^(" + "|".join(re.escape(k) for k in keys) + r"):"

    def replacement(m: re.Match) -> str:
        if m.start() == 0:
            return m.group(0)
        return break_str + m.group(0)

    return re.sub(pattern, replacement, yml, flags=re.MULTILINE)
