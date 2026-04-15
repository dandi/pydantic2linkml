from collections.abc import Iterable
from typing import Optional

# noinspection PyProtectedMember
from pydantic._internal._core_utils import CoreSchemaOrField


class NameCollisionError(Exception):
    """
    Raise when there is a name collision
    """


class UserError(Exception):
    """
    Raise when an entity is not used correctly and other more precise exceptions
    are not appropriate
    """


class GeneratorReuseError(UserError):
    """
    Raise when a generator object is reused
    """

    def __init__(self, generator):
        """
        :param generator: The generator object that is reused
        """
        super().__init__(
            f"{type(generator).__name__} generator object cannot be reused"
        )


class TranslationNotImplementedError(NotImplementedError):
    """
    Raise when the translation of a Pydantic core schema to LinkMK is not implemented

    Note: This is used to mark the translation methods of Pydantic core schemas that
      are deemed to be not necessary for use of this translation tool in general or
      against the targeted models expressed in Pydantic. File an issue if this error is
      encountered.
    """

    def __init__(self, schema: CoreSchemaOrField):
        """
        :param schema: The Pydantic core schema of which translation to LinkML is not
            implemented
        """
        super().__init__(
            f"Translation of Pydantic core schema, {schema['type']}, is not "
            "implemented. If you encounter this error in translating your models, "
            "consider filing an issue."
        )


class SlotUsageGenerationError(Exception):
    """
    Raise when a slot usage entry cannot be generated to make a given base slot
    definition function like a given target slot definition. A slot usage entry can
    only extend the base with new properties (meta slots) or override non-constraint
    properties of the base; it cannot remove properties from the base nor override
    its constraint properties (those defined in ``SlotExpression``).
    """

    def __init__(
        self,
        missing_meta_slots: Optional[Iterable[str]] = None,
        varied_constraint_meta_slots: Optional[Iterable[str]] = None,
    ):
        """
        :param missing_meta_slots: The meta slots that exist in the base
            slot definition but not in the target slot definition. The
            items are sorted case-insensitively. If None or not provided,
            an empty list is used.
        :param varied_constraint_meta_slots: The constraint meta slots
            (i.e., those defined in ``SlotExpression``) that exist in
            both the base and target slot definitions but have different
            values. The items are sorted case-insensitively. If None or
            not provided, an empty list is used.
        :raises ValueError: If both `missing_meta_slots` and
            `varied_constraint_meta_slots` are empty
        """
        sorted_missing: list[str] = (
            sorted(missing_meta_slots, key=str.casefold)
            if missing_meta_slots is not None
            else []
        )
        sorted_constraint: list[str] = (
            sorted(varied_constraint_meta_slots, key=str.casefold)
            if varied_constraint_meta_slots is not None
            else []
        )

        if len(sorted_missing) + len(sorted_constraint) == 0:
            error_msg = (
                "At least one of `missing_meta_slots` and "
                "`varied_constraint_meta_slots` must be non-empty."
            )
            raise ValueError(error_msg)

        super().__init__()

        self.missing_meta_slots: list[str] = sorted_missing
        self.varied_constraint_meta_slots: list[str] = sorted_constraint

    def __str__(self):
        return (
            f"Target slot definition has missing meta slots, "
            f"{self.missing_meta_slots}, and varied constraint meta slots, "
            f"{self.varied_constraint_meta_slots}"
        )

    def __repr__(self):
        return (
            f"{type(self).__name__}"
            f"(missing_meta_slots={self.missing_meta_slots!r}, "
            f"varied_constraint_meta_slots="
            f"{self.varied_constraint_meta_slots!r})"
        )


class YAMLContentError(ValueError):
    """
    Raise when the content of a YAML file is not what is expected
    """


class InvalidLinkMLSchemaError(ValueError):
    """
    Raised when a YAML string does not conform to the LinkML meta schema
    (e.g. unknown field names or wrong-type values)
    """
