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


class SlotExtensionError(Exception):
    """
    Raise when a given base slot definition cannot be extended to achieve the behavior
    of a given target slot definition through a slot usage entry in a class definition
    """

    def __init__(
        self,
        missing_meta_slots: Optional[list[str]] = None,
        varied_meta_slots: Optional[list[str]] = None,
    ):
        """
        :param missing_meta_slots: The meta slots that exist in the base slot definition
            but not in the target slot definition. If None or not provided, an empty
            list is used.
        :param varied_meta_slots: The meta slots that exist in both the base and target
            slot definitions but have different values. If None or not provided, an
            empty list is used.
        :raises ValueError: If both `missing_meta_slots` and `varied_meta_slots` are
            empty
        """
        if missing_meta_slots is None:
            missing_meta_slots = []
        if varied_meta_slots is None:
            varied_meta_slots = []

        if len(missing_meta_slots) + len(varied_meta_slots) == 0:
            error_msg = (
                "At least one of `missing_meta_slots` and `varied_meta_slots` "
                "must be non-empty."
            )
            raise ValueError(error_msg)

        super().__init__()

        self.missing_meta_slots: list[str] = missing_meta_slots
        self.varied_meta_slots: list[str] = varied_meta_slots

    def __str__(self):
        return (
            f"Target slot definition has missing meta slots, "
            f"{self.missing_meta_slots}, and varied meta slots, "
            f"{self.varied_meta_slots}"
        )

    def __repr__(self):
        return (
            f"{type(self).__name__}"
            f"(missing_meta_slots={self.missing_meta_slots!r}, "
            f"varied_meta_slots={self.varied_meta_slots!r})"
        )


class YAMLContentError(ValueError):
    """
    Raise when the content of a YAML file is not what is expected
    """


class InvalidLinkMLSchemaError(ValueError):
    """
    Raised when a YAML string contains field names unknown to LinkML
    """
