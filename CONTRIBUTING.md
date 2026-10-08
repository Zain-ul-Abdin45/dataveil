# Contributing to dataveil

Thanks for your interest. This guide covers setup, checks, and the rules
that keep dataveil safe to point at sensitive data.

## Setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/Zain-ul-Abdin45/dataveil.git
cd dataveil
make setup        # creates .venv, installs ".[dev]", installs the pre-commit hook
```

## Checks

```bash
make check        # lint, format check, mypy, tests -- the same checks CI runs
make format       # fix formatting and auto-fixable lint findings
make help         # list all targets
```

Run `make check` before you open a pull request. CI runs the same checks on
Python 3.10, 3.12 and 3.13.

Two test modules need external systems and skip themselves when those are
missing:

- `tests/test_postgres_adapter.py` needs a reachable Postgres. Set
  `DATAVEIL_TEST_POSTGRES_URL` (default: a local `dataveil_test` database).
- `tests/test_sqlmesh_adapter.py` needs the `sqlmesh` extra (included in `dev`).

## Design rules

These rules are the point of the project. A change that breaks one of them
will not be merged, even if the tests pass.

1. **Aggregate-only.** Nothing that leaves `profile`/`classify` may contain a
   raw value, a row, or a sample. If a change adds a new statistic, explain in
   the pull request why it cannot reveal a single row, and update
   [Known limits](README.md#known-limits) if it can in some case.
2. **No LLM-authored code.** The LLM only picks operations from the closed
   vocabulary in `dataveil/core/plan.py`. Do not add an operation that runs
   free-form SQL or code.
3. **New operations need a real use case.** Open an issue first with the plan
   that needs the operation. We do not add operations speculatively.
4. **The core has no dependencies.** `dataveil.core` imports only the standard
   library. Backend code goes into an adapter behind an optional extra.

## Adding an adapter

Implement the five methods of `dataveil.core.adapter.Adapter` (the module
docstring describes the contract), add an optional extra in `pyproject.toml`,
and add an end-to-end test module that skips itself when the backend is not
available. Look at `dataveil/adapters/postgres.py` as an example.

## Pull requests

- Open an issue first for anything larger than a small fix.
- Keep one change per pull request, and add a test that fails without it.
- Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for any
  change a user would notice.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for the
  title: `fix: ...`, `feat: ...`, `docs: ...`, `chore: ...`, `test: ...`.
- Never use real personal data in tests, issues or pull requests. Use
  synthetic data (for example `Faker`, which is in the `dev` extra).

## Reporting a privacy leak

Do not open a public issue. See [SECURITY.md](SECURITY.md).
