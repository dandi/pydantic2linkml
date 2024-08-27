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
