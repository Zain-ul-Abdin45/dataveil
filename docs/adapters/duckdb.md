# DuckDB

```bash
pip install "dataveil[duckdb]"
```

```python
import duckdb
from dataveil.adapters.duckdb import DuckDBAdapter

adapter = DuckDBAdapter(duckdb.connect("warehouse.duckdb"))
```

The reference adapter, and the one dataveil's own test suite runs against.
It supports all eight operations. It registers the Luhn and IBAN checksum
functions as DuckDB functions, so every classifier works, and the person-name
function when the `ner` extra and `en_core_web_sm` are installed. Pass
`register_ner_function=False` to skip the name model.

With the MCP server: `DATAVEIL_ADAPTER=duckdb` and `DATAVEIL_DUCKDB_PATH`,
or `[adapter] kind = "duckdb"` and `duckdb_path`.
