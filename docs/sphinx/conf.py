"""Sphinx configuration for the Bobba Buddy documentation.

Build from the project root::

    uv run --group docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html

API pages are generated from the Google-style docstrings by ``sphinx.ext.napoleon``.
The guides in ``docs/*.md`` are pulled in through MyST ``include`` stubs in
``guides/``, so they're written once and shown both on GitHub and here.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bobba_buddy import __version__  # noqa: E402

# -- Project -------------------------------------------------------------------------

project = "Bobba Buddy"
author = "aimanamri"
copyright = "2026, aimanamri"  # noqa: A001 - Sphinx's name for it
version = release = __version__

# -- Extensions ----------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build", "README.md"]

# Google-style docstrings only.
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_ivar = False  # "Attributes:" → attribute entries (also valid in modules)
napoleon_attr_annotations = True

autodoc_default_options = {
    "members": True,
    "private-members": True,  # the packet handlers (_on_*) are the interesting part
    "show-inheritance": True,
    "member-order": "bysource",
}
autoclass_content = "both"  # class docstring + __init__ Args
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_preserve_defaults = True

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# Types nitpicky mode (-n) can't link:
# * g_python and anthropic publish no Sphinx inventory.
# * In a class's __init__ parameters, autodoc can't evaluate postponed annotations
#   (``from __future__ import annotations``), so imported names stay unqualified. They
#   still render as text, and link normally everywhere else.
nitpick_ignore_regex = [
    ("py:class", r"g_python\..*"),
    ("py:class", r"anthropic\..*"),
    ("py:class", r"(HPacket|HMessage|Extension|Direction|Path|date|Executor)"),
]

# MyST: GitHub-style heading anchors, so the guides' "#section" links work here too.
myst_heading_anchors = 4

# -- HTML ----------------------------------------------------------------------------

html_theme = "furo"
html_title = f"Bobba Buddy {release}"
