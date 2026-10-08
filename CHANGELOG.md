# Changelog

All notable changes to dataveil are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) (while on 0.x, a minor release can
change behavior).

## [Unreleased]

### Security

- A format signature is only returned when at least 5 non-null values share it
  (`MIN_SIGNATURE_COUNT`). Rarer signatures, and any beyond the first 20, are
  reported together as `<other signatures>` with their total count. Before,
  a signature with count 1 described a single row.

### Added

- `py.typed` marker, so type checkers use dataveil's type hints.
- Contributor guide (`CONTRIBUTING.md`), security policy (`SECURITY.md`),
  pull request template and issue forms.
- `make check` (lint, format check, mypy, tests), pre-commit hooks, and CI on
  Python 3.13.

### Changed

- The README and the audit log docs state the privacy guarantee precisely:
  no raw rows or samples are returned, and some aggregates (for example
  `min`/`max` on a large table) can still reveal a value. Before, the README
  opened with "never raw values", which the Known limits section contradicted.
- The package passes `mypy --strict`. Public functions return precise types
  (for example `dict[str, Any]` instead of a bare `dict`).

## [0.2.0] - 2026-10-03

### Fixed

- Format signatures folded digits to `a`, so an SSN read `aaa-aa-aaaa` instead
  of `ddd-dd-dddd`. Letters now fold to `a` and digits to `d`.

### Security

- Numeric stats (`min`, `max`, `mean`, `stddev`, percentiles) are withheld when
  the table has fewer than 10 rows or the column fewer than 3 distinct values.
  In 0.1.0 they could reveal the value of a single row.
- Characters outside printable ASCII fold to `a` in format signatures, and a
  value made only of punctuation or whitespace is reported as
  `<punctuation or whitespace only>`.

### Changed

- The README has a "Known limits" section that states what aggregate-only
  does and does not guarantee.

## [0.1.0] - 2026-10-03

### Added

- Core engine: aggregate-only profiling, pattern- and checksum-based PII
  classification, and plan validation and execution against a closed,
  versioned operation vocabulary.
- Adapters for DuckDB (reference), SQLMesh and Postgres.
- Audit logging without values, and an optional plan-approval gate.
- Standalone MCP server (`dataveil-mcp`).

[Unreleased]: https://github.com/Zain-ul-Abdin45/dataveil/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Zain-ul-Abdin45/dataveil/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Zain-ul-Abdin45/dataveil/releases/tag/v0.1.0
