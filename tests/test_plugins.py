"""Adapter and classifier plugins, discovered through entry points.

Each test writes a separate package, with its own ``.dist-info`` metadata,
to a temporary directory on ``sys.path``. ``importlib.metadata`` finds it
there exactly as it finds a package installed with pip, so these tests use
the real discovery path with no change to dataveil.
"""

import sys
import textwrap

import pytest

duckdb = pytest.importorskip("duckdb")

from dataveil.adapters.duckdb import DuckDBAdapter  # noqa: E402
from dataveil.config import ConfigError, parse_config  # noqa: E402
from dataveil.core.adapter import adapter_names, load_adapter_factory  # noqa: E402
from dataveil.core.classify import CLASSIFIERS, classify_table, registered_classifiers  # noqa: E402
from dataveil.core.config import ClassifyConfig, PluginError  # noqa: E402

PLUGIN_MODULE = "dataveil_example_plugin"

EXAMPLE_PLUGIN = textwrap.dedent(
    """
    import duckdb

    from dataveil.adapters.duckdb import DuckDBAdapter
    from dataveil.core.classify import Classifier
    from dataveil.core.config import CustomClassifier

    EMPLOYEE_ID = CustomClassifier(tag="HR:EMPLOYEE_ID", pattern="^E[0-9]{6}$", name_hints=("Employee",))
    BADGES = [Classifier(tag="HR:BADGE", pattern="^B-[A-Z]{3}$", name_hints=("badge",))]

    class ExampleAdapter(DuckDBAdapter):
        def __init__(self, options):
            self.options = dict(options)
            super().__init__(duckdb.connect(options.get("database", ":memory:")))

    def make_adapter(options):
        return ExampleAdapter(options)

    def not_a_classifier(value):
        return value  # code, not a definition: must be rejected

    UNANCHORED = CustomClassifier.__new__(CustomClassifier)
    object.__setattr__(UNANCHORED, "tag", "HR:LOOSE")
    object.__setattr__(UNANCHORED, "pattern", "E[0-9]+")
    object.__setattr__(UNANCHORED, "name_hints", ())

    EMAIL_AGAIN = Classifier(tag="PII:EMAIL", pattern="^.+@.+$", name_hints=())

    def returns_a_string(options):
        return "not an adapter"
    """
)


@pytest.fixture
def install_plugin(tmp_path, monkeypatch):
    """Write the example package with the given entry points and put it on sys.path."""

    def install(entry_points: str) -> None:
        (tmp_path / f"{PLUGIN_MODULE}.py").write_text(EXAMPLE_PLUGIN)
        dist_info = tmp_path / f"{PLUGIN_MODULE}-0.1.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: dataveil-example-plugin\nVersion: 0.1\n")
        (dist_info / "entry_points.txt").write_text(textwrap.dedent(entry_points))
        monkeypatch.syspath_prepend(str(tmp_path))

    yield install
    sys.modules.pop(PLUGIN_MODULE, None)


GOOD_PLUGIN = """
    [dataveil.classifiers]
    hr = dataveil_example_plugin:EMPLOYEE_ID
    badges = dataveil_example_plugin:BADGES

    [dataveil.adapters]
    example = dataveil_example_plugin:make_adapter
"""


def _staff(con):
    con.execute("CREATE TABLE staff (employee VARCHAR, badge VARCHAR, email VARCHAR)")
    con.executemany(
        "INSERT INTO staff VALUES (?, ?, ?)",
        [(f"E{i:06d}", "B-ABC", f"p{i}@example.com") for i in range(10)],
    )


# --- built-ins --------------------------------------------------------------


def test_builtins_are_registered_through_entry_points():
    from importlib.metadata import entry_points

    adapters = {ep.name: ep.value for ep in entry_points(group="dataveil.adapters")}
    assert adapters["duckdb"] == "dataveil.adapters.factories:duckdb_adapter"
    assert {"postgres", "sqlmesh"} <= set(adapters)
    builtin = {ep.name: ep for ep in entry_points(group="dataveil.classifiers")}["builtin"]
    assert builtin.load() is CLASSIFIERS
    assert registered_classifiers() == CLASSIFIERS
    assert adapter_names() == ["duckdb", "postgres", "sqlmesh"]


def test_builtin_factory_builds_an_adapter(tmp_path):
    adapter = load_adapter_factory("duckdb")({"path": str(tmp_path / "x.duckdb")})
    assert isinstance(adapter, DuckDBAdapter)


# --- a plugin package -------------------------------------------------------


def test_plugin_classifiers_are_used_without_code_changes(install_plugin):
    install_plugin(GOOD_PLUGIN)
    tags = [c.tag for c in registered_classifiers()]
    assert tags[: len(CLASSIFIERS)] == [c.tag for c in CLASSIFIERS]
    assert tags[len(CLASSIFIERS) :] == ["HR:BADGE", "HR:EMPLOYEE_ID"]

    con = duckdb.connect(":memory:")
    _staff(con)
    results = classify_table(DuckDBAdapter(con), "staff")
    assert {r.column: r.tag for r in results} == {
        "employee": "HR:EMPLOYEE_ID",
        "badge": "HR:BADGE",
        "email": "PII:EMAIL",
    }
    # a plugin classifier yields the same result shape as a built-in: a tag and a rate
    assert all(set(r.to_dict()) == {"column", "tag", "match_rate"} for r in results)


def test_plugin_classifier_can_be_disabled_from_config(install_plugin):
    install_plugin(GOOD_PLUGIN)
    config = parse_config({"classify": {"disabled": ["HR:BADGE"]}}).classify
    con = duckdb.connect(":memory:")
    _staff(con)
    assert {r.column: r.tag for r in classify_table(DuckDBAdapter(con), "staff", config)}["badge"] == "none"


def test_custom_tag_may_not_reuse_a_plugin_tag(install_plugin):
    install_plugin(GOOD_PLUGIN)
    with pytest.raises(ConfigError, match="already used by a built-in or plugin"):
        parse_config({"classify": {"custom": [{"tag": "HR:BADGE", "pattern": "^x$"}]}})


def test_plugin_adapter_is_selected_by_name_with_its_options(install_plugin):
    install_plugin(GOOD_PLUGIN)
    assert adapter_names() == ["duckdb", "postgres", "sqlmesh", "example"]
    assert parse_config({"adapter": {"kind": "example"}}).adapter.kind == "example"
    adapter = load_adapter_factory("example")({"database": ":memory:", "region": "eu"})
    assert type(adapter).__name__ == "ExampleAdapter"
    assert adapter.options == {"database": ":memory:", "region": "eu"}


def test_server_uses_a_plugin_adapter_from_config(install_plugin, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    install_plugin(GOOD_PLUGIN)
    for var in ("DATAVEIL_ADAPTER", "DATAVEIL_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "plugin.duckdb"
    (tmp_path / "dataveil.toml").write_text(
        f'[adapter]\nkind = "example"\n[adapter.options]\ndatabase = "{db}"\n'
        f'[audit]\nlog_path = "{tmp_path / "a.jsonl"}"\n'
    )
    from dataveil.server import get_adapter, get_config, propose_cleansing_plan

    get_adapter.cache_clear()
    get_config.cache_clear()
    try:
        _staff(get_adapter()._con)  # noqa: SLF001
        tags = {c["column"]: c["tag"] for c in propose_cleansing_plan("staff")["classifications"]}
        assert tags["employee"] == "HR:EMPLOYEE_ID"
        assert db.exists()
    finally:
        get_adapter.cache_clear()
        get_config.cache_clear()


def test_unknown_adapter_kind_lists_the_registered_ones(install_plugin):
    install_plugin(GOOD_PLUGIN)
    with pytest.raises(ConfigError, match="duckdb, postgres, sqlmesh, example, got 'snowflake'"):
        parse_config({"adapter": {"kind": "snowflake"}})


# --- broken plugins fail loudly ---------------------------------------------


@pytest.mark.parametrize(
    "entry_point, message",
    [
        ("bad = dataveil_example_plugin:not_a_classifier", "must provide Classifier or CustomClassifier objects"),
        ("bad = dataveil_example_plugin:UNANCHORED", "must start with \\^ and end with \\$"),
        ("bad = dataveil_example_plugin:EMAIL_AGAIN", "PII:EMAIL is already defined by built-in"),
        ("bad = dataveil_example_plugin:MISSING", "failed to load"),
        ("bad = no_such_module:X", "failed to load"),
    ],
)
def test_broken_classifier_plugins_are_rejected(install_plugin, entry_point, message):
    install_plugin(f"[dataveil.classifiers]\n{entry_point}\n")
    with pytest.raises(PluginError, match=message):
        registered_classifiers()
    # classification refuses to run rather than silently skipping the plugin
    with pytest.raises(PluginError):
        classify_table(DuckDBAdapter(duckdb.connect(":memory:")), "t", ClassifyConfig())


def test_two_plugins_with_the_same_tag_are_rejected(install_plugin):
    install_plugin(
        "[dataveil.classifiers]\na = dataveil_example_plugin:EMPLOYEE_ID\nb = dataveil_example_plugin:EMPLOYEE_ID\n"
    )
    with pytest.raises(PluginError, match="HR:EMPLOYEE_ID is already defined by plugin 'a'"):
        registered_classifiers()


def test_adapter_plugin_may_not_take_a_builtin_name(install_plugin):
    install_plugin("[dataveil.adapters]\nduckdb = dataveil_example_plugin:make_adapter\n")
    with pytest.raises(PluginError, match="uses the built-in name 'duckdb'"):
        adapter_names()


def test_adapter_plugin_must_return_an_adapter(install_plugin):
    install_plugin("[dataveil.adapters]\nweird = dataveil_example_plugin:returns_a_string\n")
    with pytest.raises(PluginError, match="returned str, not an Adapter"):
        load_adapter_factory("weird")({})
