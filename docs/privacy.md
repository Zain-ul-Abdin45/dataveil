# Privacy and known limits

## What dataveil guarantees

- No query that profiling or classification sends returns a raw row or a
  sample. Every query is an aggregate: counts, rates, statistics, or a
  `GROUP BY` over a generalized format signature.
- Every threshold that withholds a value is applied inside the database,
  so a withheld value never reaches dataveil's process.
- Classifiers, including [custom](configuration.md#custom-classifiers) and
  [plugin](adapters/plugins.md) classifiers, only produce match counts.
- The privacy thresholds can be made stricter in
  [`dataveil.toml`](configuration.md), never looser.

## Known limits

The profile is aggregate-only, but aggregate does not always mean it cannot
reveal a value. What the code does, and where it stops short:

- **Numeric stats are withheld for small or constant columns.** `min`, `max`,
  `mean`, `stddev` and the percentiles are only returned when the table has at
  least 10 rows and the column at least 3 distinct values (by default). Below that, a single
  row could be recovered from them, so they are omitted entirely. The
  thresholds are conservative heuristics, not a privacy guarantee.
- **`min` and `max` are only returned when they are shared.** They are literal
  cell values, so each one is returned only when at least `min_extreme_count`
  (5 by default) rows have that exact value. A unique outlier (the largest salary, for
  example) is withheld; a shared bound (age 18 for many rows) is kept.
- **Percentiles and the mean can still equal a cell value.** `p25`, `p50` and
  `p75` are exact cell values when the row count lines up, and `mean` and
  `stddev` describe the whole column. They are not tied to an outlier, but
  they are not anonymized either.
- **Format signatures are generalized, but some structure is kept.** Letters
  become `a`, digits become `d`, and case is folded. Spaces and punctuation
  stay, so `ddd-dd-dddd` is an SSN shape and `aaa@aaaaaaa.aaa` is an email
  shape. Accented and other non-ASCII characters fold to `a`. A value made
  only of punctuation or whitespace is replaced with
  `<punctuation or whitespace only>`.
- **Rare format signatures are grouped.** A signature is only returned when at
  least `min_signature_count` (5 by default) non-null values share it. Rarer ones, and
  any beyond the first 20, are counted together as `<other signatures>`. The
  bucket's count can still be small, so it can tell you that a few values have
  an unusual shape, but not what that shape is.
- **Small tables give unreliable statistics**: match rates on a handful of
  rows are noise (a single-row column is either 0.0 or 1.0).
- **Classification is pattern-based**: regex and checksum matching will miss
  personal identifiers written in an unusual format.
- **Name recognition is a statistical model**: `en_core_web_sm` is English
  only and misses some names (in a test it found 9 of 10 short names, missing
  "María García"). It labels a column's 10,000 most frequent distinct values
  (`ner_max_distinct_values`), so on a larger column the match rate is an
  estimate from those values. Like the checksum functions, the model reads
  each value inside the adapter's process; the values are not stored or
  returned.

So the guarantee is "no raw row is returned and no sample is taken, small or
constant numeric columns are withheld, and `min`, `max` and format signatures
are only returned when several rows share them", not "no value can ever be
inferred".
