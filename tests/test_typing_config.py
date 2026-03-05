from pathlib import Path


class TestTypingConfig:
    def test_mypy_uses_pydantic_plugin(self):
        pyproject = Path("pyproject.toml").read_text()
        assert "[tool.mypy]" in pyproject
        assert 'plugins = ["pydantic.mypy"]' in pyproject
