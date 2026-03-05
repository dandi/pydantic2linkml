from pathlib import Path


class TestMypyPydanticPluginConfig:
    def test_mypy_uses_pydantic_plugin(self):
        pyproject_content = (
            Path(__file__).resolve().parents[1] / "pyproject.toml"
        ).read_text()
        assert "[tool.mypy]" in pyproject_content
        assert 'plugins = ["pydantic.mypy"]' in pyproject_content
        assert "[tool.pydantic-mypy]" in pyproject_content
        assert "init_forbid_extra = true" in pyproject_content
        assert "init_typed = true" in pyproject_content
        assert "warn_required_dynamic_aliases = true" in pyproject_content
