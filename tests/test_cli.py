import pytest
from typer.testing import CliRunner
import typer

from generate_linkml_from_pydantic import main

runner = CliRunner()


@pytest.mark.xfail(
    reason="Awaiting the translation of Pydantic filed types to complete"
)
def test_smoke_cli():
    # Mimic the app creation in generate_linkml_from_pydantic
    app = typer.Typer()
    app.command()(main)

    result = runner.invoke(app)
    assert result.exit_code == 0
