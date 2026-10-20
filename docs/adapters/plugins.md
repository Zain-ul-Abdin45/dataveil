# Plugins

A separate package can add adapters and classifiers without changing
dataveil. It registers them as entry points, and the built-in ones are
registered the same way:

```toml
# the plugin package's pyproject.toml
[project.entry-points."dataveil.adapters"]
acme = "acme_dataveil:make_adapter"

[project.entry-points."dataveil.classifiers"]
acme = "acme_dataveil:CLASSIFIERS"
```

```python
# acme_dataveil.py
from dataveil.core.config import CustomClassifier

CLASSIFIERS = [CustomClassifier(tag="ACME:ACCOUNT_ID", pattern="^AC[0-9]{8}$", name_hints=("account",))]

def make_adapter(options):  # options: the [adapter.options] table, as strings
    return AcmeAdapter(options["account"])
```

After `pip install acme-dataveil`, every classification includes
`ACME:ACCOUNT_ID`, and `DATAVEIL_ADAPTER=acme` or `[adapter] kind = "acme"`
selects the adapter.

A classifier plugin provides definitions, not code: a `CustomClassifier`
or `Classifier`, or a list of them. dataveil runs each one as the same
aggregate `COUNT` as a built-in classifier, so a plugin can only produce
counts. Its patterns follow the same rules as [custom classifiers](../configuration.md#custom-classifiers). A broken
plugin, a pattern that fails those rules, or a tag that is already taken
stops classification with an error instead of being skipped. An adapter
plugin's factory must return an `Adapter`, and it must not reuse a
built-in adapter name.

## Testing a plugin

dataveil's own plugin tests write a package with its own `.dist-info`
metadata to a temporary directory on `sys.path`. `importlib.metadata`
finds it exactly as it finds a package installed with pip. See
`tests/test_plugins.py`.
