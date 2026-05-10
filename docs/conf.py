"""Sphinx configuration for domain-watcher documentation.

Built and published by Read the Docs from ``.readthedocs.yaml`` at the
repository root. Locally::

    uv pip install -e '.[docs]'
    sphinx-build -W -b html docs docs/_build/html
"""

from __future__ import annotations

import importlib.metadata as _md
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# -- Path setup --------------------------------------------------------------
# Make the package importable for autodoc without requiring an editable install
# step inside the RTD build (the build still installs the package, but this
# keeps `sphinx-build` runnable from a bare checkout for previews).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# -- Project information -----------------------------------------------------
project = "domain-watcher"
author = "Aryn Y."
copyright = f"{datetime.now(tz=UTC):%Y}, {author}"

try:
    release = _md.version("domain-watcher")
except _md.PackageNotFoundError:  # not installed: fall back to pyproject
    import tomllib

    release = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
version = ".".join(release.split(".")[:2])

# -- General configuration ---------------------------------------------------
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
]
templates_path = ["_templates"]

# MyST: enable the markdown features we actually use in the guides.
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "linkify",
    "smartquotes",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3
myst_url_schemes = ("http", "https", "mailto", "ftp")

# Source suffixes — we standardise on Markdown.
source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

master_doc = "index"
language = "en"

# Surface every doc/example glitch as a build error in CI.
nitpicky = False  # autodoc cross-refs to stdlib types create noise; opt-in later
# Suppressed:
#   - myst.header: structural ordering in included CHANGELOG.md
#   - autosectionlabel.*: chunked headings in autosummary stubs duplicate
#     across the re-export pages (adapters/* and testing/* point at the
#     same class). The contract is intentional — same symbol, two import
#     paths — so duplicate object descriptions are noise, not bugs.
suppress_warnings = ["myst.header", "autosectionlabel.*"]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- HTML output -------------------------------------------------------------
html_theme = "furo"
html_title = f"{project} {version}"
html_static_path = ["_static"]
html_show_sourcelink = False
html_theme_options = {
    "source_repository": "https://github.com/arynyklas/domain-watcher/",
    "source_branch": "main",
    "source_directory": "docs/",
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/arynyklas/domain-watcher",
            "html": "",
            "class": "fa-brands fa-github",
        },
    ],
}

# -- Autodoc -----------------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autodoc_class_signature = "mixed"
autodoc_preserve_defaults = True
autosummary_generate = True
autosummary_ignore_module_all = False

# -- Intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/20/", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}
intersphinx_disabled_reftypes = ["std:doc"]

# -- Read the Docs integration ----------------------------------------------
# RTD sets READTHEDOCS=True in the build env. Pin a small canonical URL so
# generated sitemap.xml + linkcheck behave identically locally and on RTD.
on_rtd = os.environ.get("READTHEDOCS") == "True"
html_baseurl = os.environ.get(
    "READTHEDOCS_CANONICAL_URL", "https://domain-watcher.readthedocs.io/en/latest/"
)

# -- Linkcheck ---------------------------------------------------------------
linkcheck_ignore = [
    # Telegram bot examples are placeholders.
    r"https?://t\.me/.*",
    # GHCR / PyPI / GitHub release URLs return 404 until the first tagged
    # release exists in the public repo.
    r"https?://github\.com/arynyklas/domain-watcher/releases/.*",
    r"https?://github\.com/arynyklas/domain-watcher/compare/.*",
    r"https?://pypi\.org/p/domain-watcher.*",
]
linkcheck_timeout = 10
linkcheck_workers = 4
