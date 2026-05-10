"""Shared HTTP-status range bounds used by adapter classification logic.

Notifiers and checkers all classify responses with the same ``2xx`` /
``4xx`` / ``5xx`` shape. Naming the boundary literals once, here, keeps
the call sites readable, silences ``PLR2004`` (magic-value-comparison)
without scattering ``# noqa`` markers, and gives the adapters a single
canonical place to look when the classification rules change.

Use :class:`http.HTTPStatus` for individual status codes (401, 403, 404,
429, …); this module is for range *boundaries* only — ``HTTPStatus`` has
no symbolic name for the open ends ``300`` / ``600``.
"""

from __future__ import annotations

from typing import Final

# Range boundaries for ``HTTP_2XX_MIN <= status < HTTP_2XX_MAX`` checks.
HTTP_2XX_MIN: Final = 200
HTTP_2XX_MAX: Final = 300  # exclusive
HTTP_4XX_MIN: Final = 400
HTTP_5XX_MIN: Final = 500
HTTP_5XX_MAX: Final = 600  # exclusive

__all__ = [
    "HTTP_2XX_MAX",
    "HTTP_2XX_MIN",
    "HTTP_4XX_MIN",
    "HTTP_5XX_MAX",
    "HTTP_5XX_MIN",
]
