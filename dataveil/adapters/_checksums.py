"""Luhn and IBAN checksums, shared by the adapters.

``classify.py``'s credit-card and IBAN classifiers call the SQL functions
``dataveil_luhn_valid`` and ``dataveil_iban_valid``. Each adapter registers
them in its engine, so the checksum runs per row inside the database and only
the aggregate COUNT reaches Python:

- DuckDB: the Python functions below, registered as UDFs.
- Postgres: the PL/pgSQL functions below (no ``plpython3u`` needed).
"""

from __future__ import annotations


def luhn_valid(value: str | None) -> bool:
    if value is None:
        return False
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_valid(value: str | None) -> bool:
    if value is None:
        return False
    s = value.replace(" ", "").upper()
    if not (5 <= len(s) <= 34):
        return False
    if not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    numeric_chars = []
    for ch in rearranged:
        if ch.isdigit():
            numeric_chars.append(ch)
        elif ch.isalpha():
            numeric_chars.append(str(ord(ch) - ord("A") + 10))
        else:
            return False
    try:
        return int("".join(numeric_chars)) % 97 == 1
    except ValueError:
        return False


LUHN_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION dataveil_luhn_valid(value TEXT) RETURNS BOOLEAN AS $$
DECLARE
    digits TEXT;
    total INT := 0;
    n INT;
    i INT;
    d CHAR;
BEGIN
    IF value IS NULL THEN
        RETURN FALSE;
    END IF;
    digits := regexp_replace(value, '[^0-9]', '', 'g');
    IF length(digits) < 2 THEN
        RETURN FALSE;
    END IF;
    FOR i IN 1..length(digits) LOOP
        d := substr(digits, length(digits) - i + 1, 1);
        n := d::INT;
        IF i % 2 = 0 THEN
            n := n * 2;
            IF n > 9 THEN
                n := n - 9;
            END IF;
        END IF;
        total := total + n;
    END LOOP;
    RETURN total % 10 = 0;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""

IBAN_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION dataveil_iban_valid(value TEXT) RETURNS BOOLEAN AS $$
DECLARE
    s TEXT;
    rearranged TEXT;
    numeric_str TEXT := '';
    ch CHAR;
    i INT;
BEGIN
    IF value IS NULL THEN
        RETURN FALSE;
    END IF;
    s := upper(replace(value, ' ', ''));
    IF length(s) < 5 OR length(s) > 34 THEN
        RETURN FALSE;
    END IF;
    IF NOT (substr(s, 1, 2) ~ '^[A-Z]{2}$') OR NOT (substr(s, 3, 2) ~ '^[0-9]{2}$') THEN
        RETURN FALSE;
    END IF;
    rearranged := substr(s, 5) || substr(s, 1, 4);
    FOR i IN 1..length(rearranged) LOOP
        ch := substr(rearranged, i, 1);
        IF ch ~ '[0-9]' THEN
            numeric_str := numeric_str || ch;
        ELSIF ch ~ '[A-Z]' THEN
            numeric_str := numeric_str || (ascii(ch) - ascii('A') + 10)::TEXT;
        ELSE
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN (numeric_str::NUMERIC % 97) = 1;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""
