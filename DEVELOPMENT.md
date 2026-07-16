# Development

Local QA commands and environment setup. Addresses issue #7 (stabilize local
QA tooling for pytest temp dirs and ruff availability).

## Install dev dependencies

The lint/format/test tools live in the `dev` extra. Install the project
editable with dev (and whichever runtime extras you need):

```bash
# from repo root, in your venv
pip install -e ".[dev,data-sources,ml,backtest]"
```

This makes `pytest`, `ruff`, `black`, `mypy` available. If `ruff` is missing
it means the `dev` extra was not installed — rerun the command above.

## Run tests

Default (POSIX / unrestricted temp access):

```bash
python -m pytest -q
```

Sandboxed or Windows environments where the default temp root is not writable
(use a repo-local basetemp):

```bash
python -m pytest -q --basetemp .pytest_tmp
```

`.pytest_tmp/` is gitignored. Both commands should yield the same result;
the `--basetemp` form only changes where pytest writes its tmp dirs.

## Lint and format

```bash
python -m ruff check .
python -m black --check .
python -m mypy packages apps
```

## Pre-commit (optional)

`pre-commit` is in the `dev` extra. Install hooks once:

```bash
pre-commit install
```

## CI / quick check script

A minimal local check that pins a repo-local temp dir and runs pytest + ruff:

```bash
python -m pytest -q --basetemp .pytest_tmp && python -m ruff check .
```
