# Sphinx documentation

This folder builds an HTML site from two sources:

- **The guides** in `docs/*.md`. Each file in `guides/` is a one-line MyST `include` of the real file, so the guides are written once and show up both on GitHub and here.
- **The API reference**, generated from the code's Google-style docstrings by `sphinx.ext.autodoc` and `sphinx.ext.napoleon`. There is one page per module in `api/`, plus the three scripts.

## Build

From the project root:

```bash
uv sync --group docs
uv run --group docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html
```

Open `docs/sphinx/_build/html/index.html` in a browser. `_build/` is git-ignored.

For a strict build (warnings fail it, and every cross-reference is checked):

```bash
uv run --group docs sphinx-build -b html -W --keep-going -n docs/sphinx docs/sphinx/_build/html
```

The strict build currently passes with no warnings.

## Layout

| Path | Contents |
|---|---|
| `conf.py` | Settings: extensions, Google-style napoleon, autodoc defaults, MyST heading anchors, Shibuya theme (yellow accent) |
| `_static/` | `logo.png` (logo and favicon) and `custom.css` (light-mode link contrast, crisp pixel-art logo) |
| `_templates/` | `components/foot-copyright.html`: the footer with the fan-project disclaimer (text in `conf.py` `html_context`) |
| `index.rst` | Landing page and the table of contents |
| `guides/*.md` | Include stubs for `docs/*.md` (same file names, so links between guides keep working) |
| `api/*.rst` | One `automodule` page per module, plus `scripts.*.rst` for `scripts/` |

## Adding things

- **A new module:** add `api/bobba_buddy.<name>.rst` (copy an existing one) and list it in `api/index.rst`.
- **A new guide:** add `docs/<name>.md`, then `guides/<name>.md` containing `` ```{include} ../../<name>.md `` and a closing fence, and list it in `index.rst`.
- **Docstrings:** follow the Google style used everywhere (`Args:`, `Returns:`, `Raises:`, `Attributes:`). `uv run ruff check .` enforces it (the `D` rules with `convention = "google"`).

Private members such as the `_on_*` packet handlers are included, because they're where most of the behaviour lives.
