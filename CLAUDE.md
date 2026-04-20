# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Keeping Documentation Up to Date

- Whenever you notice that any documentation — `CLAUDE.md`, `README.md`, or any other
  docs for human or machine consumption — is outdated or incorrect (e.g., Python
  versions, dependencies, commands, architecture descriptions), update it immediately.
- Before submitting a PR, review **all project documentation** and ensure everything
  is accurate and up to date.
- Wrap all prose in documentation files at ~79 characters so they read well as
  plain text. Code blocks and long URLs are exempt.

## Project Overview

`pydantic2linkml` is a CLI tool and library that translates [Pydantic](https://docs.pydantic.dev/) v2 models to [LinkML](https://linkml.io/) schemas. It works by introspecting Pydantic's internal `core_schema` objects rather than the higher-level model API.

## Build & Environment

This project uses [Hatch](https://hatch.pypa.io/) for environment and build management.

```bash
# Check if Hatch is already installed (it may be installed via Homebrew, pipx, pip, etc.)
hatch --version

# If not installed, see https://hatch.pypa.io/latest/install/ for options, e.g.:
#   brew install hatch        # macOS/Linux via Homebrew
#   pipx install hatch        # isolated pip install (recommended)
#   pip install hatch         # plain pip

# Run tests in a specific Python environment
hatch run test.py3.10:pytest tests/

# Run a single test file
hatch run test.py3.10:pytest tests/test_gen_linkml.py

# Run a single test by name
hatch run test.py3.10:pytest tests/test_gen_linkml.py::test_name

# Run tests with coverage
hatch run test.py3.10:pytest --cov tests/

# Run tests across all Python matrix environments
hatch run test:python -m pytest --numprocesses=logical -s -v tests

# Type checking
hatch run types:check

# Lint/format (ruff is configured in pyproject.toml)
ruff check .
ruff format .

# Spell check
codespell
```

The default hatch environment uses Python 3.10. The `test` environment matrix covers Python 3.10–3.13 and adds `aind-data-schema`, `dandischema`, `pytest`, `pytest-cov`, `pytest-mock`, and `pytest-xdist`.

## CLI Usage

```bash
pydantic2linkml [OPTIONS] MODULE_NAMES...
# Example:
pydantic2linkml -o output.yml -l INFO dandischema.models
```

Options:

- `--output-file`/`-o` (path) — write output to a file instead of stdout
- `--merge-file`/`-M` (path) — deep-merge a YAML file into the generated
  schema; values from the file win on conflict; the result is validated
  against the LinkML meta schema
- `--overlay-file`/`-O` (path) — shallow-merge a YAML file into the
  generated schema; the result is validated against the LinkML meta
  schema
- `--log-level`/`-l` (default: WARNING)

## Architecture

### Core Translation Pipeline

1. **`tools.py`** — Low-level utilities for introspecting Pydantic internals
   and post-processing the generated schema YAML:
   - `get_all_modules()` — imports modules and collects them with submodules
   - `fetch_defs()` — extracts `BaseModel` subclasses and `Enum` subclasses
     from modules
   - `get_field_schema()` / `get_locally_defined_fields()` — extracts
     resolved `pydantic_core.CoreSchema` objects for fields, distinguishing
     newly defined vs. overriding fields
   - `FieldSchema` (NamedTuple) — bundles a field's core schema, its
     resolution context, field name, `FieldInfo`, owning model, and an
     `is_subschema` flag (default `False`) indicating whether this
     represents a sub-schema in the schema of a field (e.g., a union
     choice) rather than the schema of the field itself
   - `resolve_ref_schema()` — resolves `definition-ref` and `definitions`
     schema types to concrete schemas
   - `canonicalize_schema_yml(yml)` — round-trips a YAML string through
     `SchemaDefinition` for canonical key ordering, then validates the
     result against the LinkML meta schema via `linkml.validator`
     (raises `InvalidLinkMLSchemaError` on unknown fields or wrong-type
     values); the meta-schema validator is lazily initialized and cached
     via `_get_meta_schema_validator()`
   - `apply_schema_overlay(schema_yml, overlay_file)` — shallow-merges a
     YAML file into a schema YAML string; no field filtering; calls
     `canonicalize_schema_yml` to reorder keys and validate the result
   - `apply_yaml_deep_merge(schema_yml, merge_file)` — deep-merges a YAML
     file into a schema YAML string using `deepmerge`; calls
     `canonicalize_schema_yml` to reorder keys and validate the result
   - `remove_schema_key_duplication(yml)` — strips redundant `name`/`text`/
     `prefix_prefix` fields from serialized LinkML YAML
   - `add_section_breaks(yml)` — inserts blank lines before top-level
     sections

2. **`gen_linkml.py`** — Main translation logic:
   - `translate_defs(module_names)` — top-level entry point; loads modules, fetches defs, runs `LinkmlGenerator`
   - `LinkmlGenerator` — single-use class; converts a collection of Pydantic models and enums into a `SchemaDefinition`. Call `generate()` once per instance.
   - `SlotGenerator` — single-use class; translates a single Pydantic `CoreSchema` into a `SlotDefinition`. Dispatches on schema `type` strings via handler methods. Handles nesting, optionality, lists, unions, literals, UUIDs, dates, etc.
   - `any_class_def` — module-level `ClassDefinition` constant for the LinkML `Any` type

3. **`cli/`** — Typer-based CLI wrapping `translate_defs`; `cli/__init__.py`
   defines the `app` and `main` command. After translation the pipeline is:
   dump YAML → optional `-M` deep merge → optional `-O` overlay →
   `remove_schema_key_duplication` → `add_section_breaks` → output.

4. **`exceptions.py`** — Custom exceptions:
   - `NameCollisionError` — duplicate class/enum names across modules
   - `GeneratorReuseError` — attempting to reuse a single-use generator
   - `TranslationNotImplementedError` — schema type not yet handled
   - `SlotUsageGenerationError` — cannot generate a slot_usage entry to
     make a base slot function like a target slot (a slot_usage entry can
     only extend the base slot with new properties or override the base
     slot's non-constraint properties); accepts any `Iterable[str]` for
     its meta-slot lists and sorts them case-insensitively on
     construction
   - `YAMLContentError` — YAML file content is not what is expected (e.g.,
     not a mapping)
   - `InvalidLinkMLSchemaError` — schema does not conform to the LinkML
     meta schema (unknown fields, wrong-type values, etc.); raised by
     `canonicalize_schema_yml`

### Key Design Patterns

- **Single-use generators**: Both `LinkmlGenerator` and `SlotGenerator` enforce one-time use via `GeneratorReuseError`. Instantiate a new object for each translation.
- **Pydantic internals**: The code directly accesses `pydantic._internal` APIs (marked with `# noinspection PyProtectedMember`). These may break on Pydantic upgrades — Pydantic is currently pinned to `~=2.7,<2.11` for this reason.
- **Field distinction**: `get_locally_defined_fields()` separates fields annotated directly on a model from those inherited, enabling correct LinkML slot vs. slot_usage generation.
- **Schema resolution**: Pydantic wraps many schemas in `definitions`/`definition-ref` indirection and function validators (`function-before`, `function-after`, etc.). `resolve_ref_schema()` and `strip_unneeded_wrapping_schema()` unwrap these before dispatch.

### Test Assets

`tests/assets/mock_module0.py` and `mock_module1.py` define Pydantic models used across test files to exercise the translator with realistic model hierarchies.

## Test Writing Conventions

- Group related tests into a class.
- Use parametrization to reduce code duplication.

## Python Language Usage

- Write Python code using the latest Python features supported by the
  project (see the minimum version and matrix in `pyproject.toml`) when
  they make the code easier to read and maintain. For example, the
  `match` statement (available since Python 3.10) is especially helpful
  in this project, where `SlotGenerator` dispatches on Pydantic
  `core_schema` `type` strings.

## Workflow Preferences

- Hatch environments use **uv** as the installer. Use
  `hatch run uv pip ...` instead of `hatch run pip ...` when querying
  or managing packages (e.g., `hatch run uv pip show <pkg>`).
