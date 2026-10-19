"""dataveil.toml: validation, lookup, and its effect on profile/classify."""

import pytest

duckdb = pytest.importorskip("duckdb")

from dataveil.adapters.duckdb import DuckDBAdapter  # noqa: E402
from dataveil.config import Config, ConfigError, load_config, parse_config  # noqa: E402
from dataveil.core import classify, profile  # noqa: E402
from dataveil.core.classify import classify_table  # noqa: E402
from dataveil.core.config import ClassifyConfig, ProfileConfig  # noqa: E402
from dataveil.core.profile import OTHER_SIGNATURES, profile_table  # noqa: E402

# --- validation -------------------------------------------------------------


def test_defaults_are_the_module_constants():
    p, c = ProfileConfig(), ClassifyConfig()
    assert p.min_signature_count == profile.MIN_SIGNATURE_COUNT == 5
    assert p.min_extreme_count == profile.MIN_EXTREME_COUNT == 5
    assert p.min_rows_for_numeric_stats == profile.MIN_ROWS_FOR_NUMERIC_STATS
    assert p.min_distinct_for_numeric_stats == profile.MIN_DISTINCT_FOR_NUMERIC_STATS
    assert p.max_format_signatures == profile.MAX_FORMAT_SIGNATURES
    assert c.match_threshold == classify.MATCH_THRESHOLD
    assert c.name_hint_threshold == classify.NAME_HINT_THRESHOLD
    assert c.ner_max_distinct_values == classify.NER_MAX_DISTINCT_VALUES


@pytest.mark.parametrize(
    "key", ["min_signature_count", "min_extreme_count", "min_rows_for_numeric_stats", "min_distinct_for_numeric_stats"]
)
def test_privacy_minimums_can_be_raised_but_not_lowered(key):
    default = getattr(ProfileConfig(), key)
    assert getattr(ProfileConfig(**{key: default + 10}), key) == default + 10
    with pytest.raises(ConfigError, match=f"{key} = {default - 1} is below the safe default"):
        ProfileConfig(**{key: default - 1})


def test_max_format_signatures_can_be_lowered_but_not_raised():
    assert ProfileConfig(max_format_signatures=3).max_format_signatures == 3
    with pytest.raises(ConfigError, match="can only be lowered"):
        ProfileConfig(max_format_signatures=21)
    with pytest.raises(ConfigError, match="must not be negative"):
        ProfileConfig(max_format_signatures=-1)


@pytest.mark.parametrize("value", [True, 7.5, "10"])
def test_thresholds_must_be_integers(value):
    with pytest.raises(ConfigError, match="must be an integer"):
        ProfileConfig(min_signature_count=value)


@pytest.mark.parametrize("value", [0, 1.5, -0.1, "0.9", True])
def test_rates_must_be_in_range(value):
    with pytest.raises(ConfigError, match="match_threshold"):
        ClassifyConfig(match_threshold=value)


def test_name_hint_threshold_must_not_exceed_match_threshold():
    with pytest.raises(ConfigError, match="must not be above"):
        ClassifyConfig(match_threshold=0.6, name_hint_threshold=0.7)


def test_unknown_sections_and_keys_are_rejected():
    with pytest.raises(ConfigError, match="unknown section"):
        parse_config({"profiles": {}})
    with pytest.raises(ConfigError, match=r"\[profile\] unknown key\(s\): min_signature_cont"):
        parse_config({"profile": {"min_signature_cont": 10}})


def test_disabled_must_name_known_tags():
    assert parse_config({"classify": {"disabled": ["PII:PHONE"]}}).classify.disabled == ("PII:PHONE",)
    with pytest.raises(ConfigError, match="unknown tag"):
        parse_config({"classify": {"disabled": ["PII:PHONES"]}})
    with pytest.raises(ConfigError, match="list of tags"):
        parse_config({"classify": {"disabled": "PII:PHONE"}})


def test_adapter_kind_is_checked():
    assert parse_config({"adapter": {"kind": "postgres"}}).adapter.kind == "postgres"
    with pytest.raises(ConfigError, match="kind must be one of"):
        parse_config({"adapter": {"kind": "snowflake"}})


# --- lookup -----------------------------------------------------------------


@pytest.fixture
def no_config_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DATAVEIL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_missing_default_file_means_defaults(no_config_env):
    assert load_config() == Config()


def test_default_file_in_working_directory_is_read(no_config_env):
    (no_config_env / "dataveil.toml").write_text("[profile]\nmin_signature_count = 8\n")
    assert load_config().profile.min_signature_count == 8


def test_env_var_path_wins_and_must_exist(no_config_env, monkeypatch):
    (no_config_env / "dataveil.toml").write_text("[profile]\nmin_signature_count = 8\n")
    other = no_config_env / "other.toml"
    other.write_text("[profile]\nmin_signature_count = 9\n")
    monkeypatch.setenv("DATAVEIL_CONFIG", str(other))
    assert load_config().profile.min_signature_count == 9
    monkeypatch.setenv("DATAVEIL_CONFIG", str(no_config_env / "missing.toml"))
    with pytest.raises(ConfigError, match="config file not found"):
        load_config()


def test_invalid_toml_and_invalid_values_name_the_file(no_config_env):
    path = no_config_env / "bad.toml"
    path.write_text("[profile\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)
    path.write_text("[profile]\nmin_signature_count = 1\n")
    with pytest.raises(ConfigError, match=r"bad\.toml: \[profile\] min_signature_count = 1 is below"):
        load_config(path)


# --- effect on profile and classify -----------------------------------------


@pytest.fixture
def con():
    return duckdb.connect(":memory:")


def _signatures(con, values, config=None):
    con.execute("CREATE TABLE t (v VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[v] for v in values])
    col = profile_table(DuckDBAdapter(con), "t", config).columns[0]
    return {s.signature: s.count for s in col.format_signatures}


def test_raised_min_signature_count_hides_signatures_inside_the_database(con):
    values = ["123-45-6789"] * 6 + ["a@b.co"] * 12
    assert _signatures(con, values) == {"a@a.aa": 12, "ddd-dd-dddd": 6}
    con.execute("DROP TABLE t")
    strict = ProfileConfig(min_signature_count=10)
    assert _signatures(con, values, strict) == {"a@a.aa": 12, OTHER_SIGNATURES: 6}


def test_lowered_max_format_signatures(con):
    values = ["1"] * 5 + ["22"] * 6 + ["333"] * 7
    assert _signatures(con, values, ProfileConfig(max_format_signatures=1)) == {"ddd": 7, OTHER_SIGNATURES: 11}


def test_raised_min_extreme_count_withholds_min_and_max(con):
    con.execute("CREATE TABLE t (x INTEGER)")
    values = [1] * 6 + list(range(2, 20)) + [20] * 9
    con.executemany("INSERT INTO t VALUES (?)", [[v] for v in values])
    adapter = DuckDBAdapter(con)
    default = profile_table(adapter, "t").columns[0]
    assert (default.min, default.max) == (1, 20)
    strict = profile_table(adapter, "t", ProfileConfig(min_extreme_count=8)).columns[0]
    assert (strict.min, strict.max) == (None, 20)
    assert strict.mean is not None


def test_raised_min_rows_withholds_all_numeric_stats(con):
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [[v] for v in range(30)])
    col = profile_table(DuckDBAdapter(con), "t", ProfileConfig(min_rows_for_numeric_stats=31)).columns[0]
    assert col.mean is None and col.p50 is None


def _tags(con, config=None):
    return {r.column: r.tag for r in classify_table(DuckDBAdapter(con), "people", config)}


@pytest.fixture
def people(con):
    con.execute("CREATE TABLE people (email VARCHAR, note VARCHAR)")
    emails = [f"user{i}@example.com" for i in range(10)]
    # 6 of 10 notes are emails: tagged only with a lower match_threshold
    notes = emails[:6] + ["hello"] * 4
    con.executemany("INSERT INTO people VALUES (?, ?)", list(zip(emails, notes)))
    return con


def test_disabled_classifier_is_skipped(people):
    assert _tags(people)["email"] == "PII:EMAIL"
    assert _tags(people, ClassifyConfig(disabled=("PII:EMAIL",)))["email"] == "none"


def test_match_threshold_changes_tagging(people):
    assert _tags(people)["note"] == "none"
    assert _tags(people, ClassifyConfig(match_threshold=0.6))["note"] == "PII:EMAIL"


# --- server -----------------------------------------------------------------


@pytest.fixture
def server_env(no_config_env, monkeypatch):
    pytest.importorskip("mcp")
    for var in ("DATAVEIL_ADAPTER", "DATAVEIL_DUCKDB_PATH", "DATAVEIL_AUDIT_LOG_PATH"):
        monkeypatch.delenv(var, raising=False)
    from dataveil.server import get_adapter, get_config

    get_adapter.cache_clear()
    get_config.cache_clear()
    yield no_config_env
    get_adapter.cache_clear()
    get_config.cache_clear()


def test_server_reads_adapter_thresholds_and_audit_path_from_file(server_env):
    from dataveil.server import get_adapter, profile

    db = server_env / "w.duckdb"
    (server_env / "dataveil.toml").write_text(
        f'[adapter]\nkind = "duckdb"\nduckdb_path = "{db}"\n'
        f'[profile]\nmin_signature_count = 10\n[audit]\nlog_path = "{server_env / "log.jsonl"}"\n'
    )
    con = get_adapter()._con  # noqa: SLF001
    con.execute("CREATE TABLE t (v VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [["123-45-6789"]] * 6)
    sigs = profile("t")["columns"][0]["format_signatures"]
    assert sigs == [{"signature": OTHER_SIGNATURES, "count": 6}]
    assert db.exists()
    assert (server_env / "log.jsonl").exists()


def test_server_env_vars_override_the_file(server_env, monkeypatch):
    from dataveil.server import get_adapter

    (server_env / "dataveil.toml").write_text('[adapter]\nkind = "postgres"\n')
    monkeypatch.setenv("DATAVEIL_ADAPTER", "duckdb")
    assert isinstance(get_adapter(), DuckDBAdapter)


def test_server_reports_a_bad_config_as_a_tool_error(server_env):
    from mcp.server.mcpserver.exceptions import ToolError

    from dataveil.server import list_tables

    (server_env / "dataveil.toml").write_text("[profile]\nmin_extreme_count = 2\n")
    with pytest.raises(ToolError, match="min_extreme_count = 2 is below"):
        list_tables()


# --- custom regex classifiers -----------------------------------------------

CUSTOMER_ID = {"tag": "PII:CUSTOMER_ID", "pattern": "^CUS-[0-9]{8}$", "name_hints": ["Customer_Ref"]}


def test_custom_classifier_from_toml(no_config_env):
    path = no_config_env / "dataveil.toml"
    path.write_text(
        '[[classify.custom]]\ntag = "PII:CUSTOMER_ID"\npattern = "^CUS-[0-9]{8}$"\nname_hints = ["Customer_Ref"]\n'
        "[[classify.custom]]\ntag = \"INTERNAL:ACCOUNT\"\npattern = '^ACC\\d{6}$'\n"
    )
    custom = load_config().classify.custom
    assert [c.tag for c in custom] == ["PII:CUSTOMER_ID", "INTERNAL:ACCOUNT"]
    assert custom[0].name_hints == ("customer_ref",)
    assert custom[1].pattern == "^ACC\\d{6}$"


@pytest.mark.parametrize(
    "item, message",
    [
        ({**CUSTOMER_ID, "pattern": "^CUS-[0-9{8}$"}, "invalid pattern"),
        ({**CUSTOMER_ID, "pattern": "CUS-[0-9]{8}"}, "must start with \\^ and end with \\$"),
        ({**CUSTOMER_ID, "pattern": "^CUS-[0-9]{8}\\$"}, "must start with \\^ and end with \\$"),
        ({**CUSTOMER_ID, "pattern": "^(?i:cus)-[0-9]{8}$"}, "only \\(\\?:\\.\\.\\.\\) groups"),
        ({**CUSTOMER_ID, "pattern": "^(?!x)CUS-[0-9]{8}$"}, "only \\(\\?:\\.\\.\\.\\) groups"),
        ({**CUSTOMER_ID, "pattern": "^\\bCUS-[0-9]{8}$"}, "not supported the same way"),
        ({**CUSTOMER_ID, "pattern": "^(a)\\1$"}, "not supported the same way"),
        ({**CUSTOMER_ID, "tag": "customer id"}, "tag must look like"),
        ({**CUSTOMER_ID, "tag": "none"}, "must not be 'none'"),
        ({**CUSTOMER_ID, "tag": "PII:EMAIL"}, "already used by a built-in"),
        ({**CUSTOMER_ID, "name_hints": "customer"}, "name_hints must be a list"),
        ({**CUSTOMER_ID, "regex": "^x$"}, "unknown key"),
        ({"tag": "PII:CUSTOMER_ID"}, "missing key\\(s\\): pattern"),
    ],
)
def test_invalid_custom_classifiers_are_rejected_at_load(item, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({"classify": {"custom": [item]}})


def test_duplicate_custom_tags_are_rejected():
    with pytest.raises(ConfigError, match="duplicate tag"):
        parse_config({"classify": {"custom": [CUSTOMER_ID, CUSTOMER_ID]}})


def test_non_capturing_groups_and_quotes_are_allowed(con):
    # the single quote must be escaped in the SQL literal, not break the query
    config = parse_config(
        {"classify": {"custom": [{"tag": "X:QUOTED", "pattern": "^(?:it's|it''s) [a-z]+$"}]}}
    ).classify
    con.execute("CREATE TABLE people (note VARCHAR)")
    con.executemany("INSERT INTO people VALUES (?)", [["it's fine"]] * 9 + [["nope"]])
    assert _tags(con, config) == {"note": "X:QUOTED"}


@pytest.fixture
def customers(con):
    con.execute("CREATE TABLE people (customer_ref VARCHAR, ref VARCHAR, email VARCHAR)")
    ids = [f"CUS-{i:08d}" for i in range(10)]
    # 6 of 10 match: only the name-hinted column reaches its lower threshold
    half = ids[:6] + ["walk-in"] * 4
    con.executemany(
        "INSERT INTO people VALUES (?, ?, ?)", [(a, b, f"u{i}@example.com") for i, (a, b) in enumerate(zip(half, half))]
    )
    return con


def test_custom_classifier_tags_columns_on_duckdb(customers):
    config = parse_config({"classify": {"custom": [CUSTOMER_ID]}}).classify
    assert _tags(customers) == {"customer_ref": "none", "ref": "none", "email": "PII:EMAIL"}
    assert _tags(customers, config) == {"customer_ref": "PII:CUSTOMER_ID", "ref": "none", "email": "PII:EMAIL"}
    rates = {r.column: r.match_rate for r in classify_table(DuckDBAdapter(customers), "people", config)}
    assert rates["customer_ref"] == 0.6
