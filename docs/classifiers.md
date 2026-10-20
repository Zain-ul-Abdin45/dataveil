# Classifiers

`classify_table` tags each column with the best-matching classifier, or
`none`. A column is tagged when at least 80% of its non-null values match
(`match_threshold`), or 50% when the column name also hints at the tag
(`name_hint_threshold`). Both are [configurable](configuration.md).

Each result is a column, a tag and a match rate. Nothing else comes back:
every classifier runs inside the database as an aggregate `COUNT`.

## Built-in classifiers

Local, deterministic pattern + checksum matching (Presidio-style), run as
aggregate `COUNT`s so matched values never leave the adapter:
`PII:EMAIL`, `PII:SSN`, `PII:PHONE`, `PII:CREDIT_CARD` (Luhn-checked),
`PII:IBAN` (mod-97 checked).

`PII:PERSON_NAME` needs the optional `ner` extra and spaCy's small English
model (`en_core_web_sm`, MIT, runs offline). The model labels each distinct
value inside the database engine, as a DuckDB function, so only a count
reaches dataveil's core. It works on the DuckDB adapter and on SQLMesh or dbt
projects that run on DuckDB; Postgres cannot run Python functions, so it is
skipped there. Addresses are not classified yet.

| Tag | Matches | Name hints |
|---|---|---|
| `PII:EMAIL` | `local@domain.tld` | email, e_mail, mail |
| `PII:SSN` | `ddd-dd-dddd` | ssn, social_security |
| `PII:PHONE` | North American numbers, with optional +1, separators and extension | phone, mobile, tel |
| `PII:CREDIT_CARD` | 13 to 24 digits, spaces or dashes, starting and ending with a digit, Luhn-valid | card, credit_card, cc_number, pan |
| `PII:IBAN` | IBAN shape, mod-97 valid | iban |
| `PII:PERSON_NAME` | person names, found by a local spaCy model | name, customer, contact, person |

Non-string columns are never classified: casting a date to text would
make `2020-01-01` look like a phone number.

## Your own classifiers

Two ways, both evaluated exactly like the built-in regexes:

- **In the configuration file.** Add `[[classify.custom]]` entries with a
  tag, a pattern and optional name hints. See
  [Configuration](configuration.md#custom-classifiers).
- **As a plugin package.** Register classifiers under the
  `dataveil.classifiers` entry point group. See [Plugins](adapters/plugins.md).

Turn a built-in or plugin classifier off with `[classify] disabled`.
