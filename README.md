# pydantic2linkml
A tool for translating models expressed in Pydantic to LinkML

[![Hatch project](https://img.shields.io/badge/%F0%9F%A5%9A-Hatch-4051b5.svg)](https://github.com/pypa/hatch)

-----

### Sample Run

```console
pydantic2linkml -o o.yml -l INFO dandischema.models
```

### Options

| Flag | Description |
|------|-------------|
| `-o` / `--output-file` | Write output to a file (default: stdout) |
| `-M` / `--merge-file` | Deep-merge a YAML file into the generated schema. Values from the file win on conflict; the result is validated against the LinkML meta schema. |
| `-O` / `--overlay-file` | Shallow-merge a YAML file into the generated schema. The result is validated against the LinkML meta schema. |
| `-l` / `--log-level` | Log level (default: `WARNING`) |
