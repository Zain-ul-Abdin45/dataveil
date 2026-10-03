import duckdb
import pytest

from dataveil.adapters.duckdb import DuckDBAdapter


@pytest.fixture
def con():
    connection = duckdb.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def adapter(con):
    return DuckDBAdapter(con)
