# Security policy

dataveil promises that a profile contains no raw rows or samples, and that a
plan can only use the closed operation vocabulary. A bug that breaks either
promise is a security issue, even if it looks like an ordinary bug.

Examples:

- a profile, classification or audit log entry that contains a raw value
- a statistic that reveals a single row in a case not listed in the README's
  [Known limits](README.md#known-limits)
- a plan that runs an operation or SQL outside the vocabulary
- SQL injection through a table name, column name or plan parameter

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/dataveil/) gets
fixes.

## How to report

Report privately with GitHub's
[private vulnerability reporting](https://github.com/Zain-ul-Abdin45/dataveil/security/advisories/new).
Do not open a public issue or pull request, and do not include real personal
data. A synthetic table that shows the leak is enough.

You will get a reply within 7 days. After a fix is released, the advisory is
published and you are credited, unless you prefer not to be.
