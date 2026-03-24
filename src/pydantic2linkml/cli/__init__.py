import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml
from linkml_runtime.dumpers import yaml_dumper
from pydantic import ValidationError

from pydantic2linkml.cli.tools import LogLevel
from pydantic2linkml.exceptions import YAMLContentError
from pydantic2linkml.gen_linkml import translate_defs
from pydantic2linkml.tools import (
    add_section_breaks,
    apply_schema_overlay,
    apply_yaml_deep_merge,
    remove_schema_key_duplication,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.command()
def main(
    module_names: list[str],
    merge_file: Annotated[
        Optional[Path],
        typer.Option(
            "--merge-file",
            "-M",
            help="A YAML file whose contents are deep-merged into the generated "
            "schema. Values from this file win on conflict. The result is "
            "always a valid YAML file but may not be a valid LinkML schema — "
            "it is the user's responsibility to supply a merge file that "
            "produces a valid schema.",
        ),
    ] = None,
    overlay_file: Annotated[
        Optional[Path],
        typer.Option(
            "--overlay-file",
            "-O",
            help="An overlay file specifying a partial schema to be applied on top of "
            "the generated schema. The overlay is merged into the serialized YAML "
            "output, so the result is always a valid YAML file but may not be a "
            "valid LinkML schema — it is the user's responsibility to supply an "
            "overlay that produces a valid schema. Overlay keys that do not "
            "correspond to a field of SchemaDefinition are skipped.",
        ),
    ] = None,
    output_file: Annotated[Optional[Path], typer.Option("--output-file", "-o")] = None,
    log_level: Annotated[
        LogLevel, typer.Option("--log-level", "-l")
    ] = LogLevel.WARNING,
):
    # Set log level of the CLI
    logging.basicConfig(level=getattr(logging, log_level))

    schema = translate_defs(module_names)
    logger.info("Dumping schema")
    yml = remove_schema_key_duplication(yaml_dumper.dumps(schema))
    if merge_file is not None:
        logger.info("Applying deep merge from %s", merge_file)
        try:
            yml = apply_yaml_deep_merge(schema_yml=yml, merge_file=merge_file)
        except ValidationError as e:
            raise typer.BadParameter(
                f"The merge file path is invalid: {e}",
                param_hint="'--merge-file'",
            ) from e
        except yaml.YAMLError as e:
            raise typer.BadParameter(
                f"The merge file does not contain valid YAML: {e}",
                param_hint="'--merge-file'",
            ) from e
        except YAMLContentError as e:
            raise typer.BadParameter(
                f"The merge file does not contain a valid YAML mapping: {e}",
                param_hint="'--merge-file'",
            ) from e
    if overlay_file is not None:
        logger.info("Applying overlay from %s", overlay_file)
        try:
            yml = apply_schema_overlay(schema_yml=yml, overlay_file=overlay_file)
        except ValidationError as e:
            raise typer.BadParameter(
                f"The overlay file path is invalid: {e}",
                param_hint="'--overlay-file'",
            ) from e
        except YAMLContentError as e:
            raise typer.BadParameter(
                f"The overlay file does not contain a valid YAML mapping: {e}",
                param_hint="'--overlay-file'",
            ) from e
    yml = add_section_breaks(yml)
    if not output_file:
        print(yml, end="")  # noqa: T201
    else:
        with output_file.open("w") as f:
            f.write(yml)
    logger.info("Success!")
