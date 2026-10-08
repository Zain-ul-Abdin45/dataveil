"""Named-entity labels for free-text PII (person names), shared by the
adapters that can run Python functions inside their engine (DuckDB, and
SQLMesh or dbt projects on DuckDB).

``classify.py``'s ``PII:PERSON_NAME`` classifier calls the SQL function
``dataveil_ner_label(value)``. It returns the spaCy entity label that covers
most of the value (``PERSON``, ``GPE``, ``ORG``, ...), or ``''`` when there is
none. Like the checksum functions, it runs per value inside the database
engine; only the aggregate COUNT reaches ``core/classify.py``.

Optional: needs the ``ner`` extra and the English model::

    pip install "dataveil[ner]"
    python -m spacy download en_core_web_sm

Without them, ``ner_available()`` is ``False`` and the classifier is skipped.
The model is loaded on the first call, not when an adapter is created.
"""

from __future__ import annotations

import functools
import importlib.util
from typing import Any

MODEL = "en_core_web_sm"

# An entity must cover at least this share of the value to label it, so
# "Order for Alice" is not a name but "Alice Smith" is.
MIN_ENTITY_COVERAGE = 0.6


def ner_available() -> bool:
    """Whether spaCy and the model are installed (without loading them)."""
    if importlib.util.find_spec("spacy") is None:
        return False
    import spacy.util

    return bool(spacy.util.is_package(MODEL))


@functools.lru_cache(maxsize=1)
def _nlp() -> Any:
    import spacy

    # Only the entity recognizer is needed; the other pipes cost time.
    return spacy.load(MODEL, disable=["parser", "lemmatizer", "tagger", "attribute_ruler"])


@functools.lru_cache(maxsize=100_000)
def ner_label(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    for entity in _nlp()(text).ents:
        if len(entity.text) >= MIN_ENTITY_COVERAGE * len(text):
            return str(entity.label_)
    return ""
