"""RevGuard API package — FastAPI backend for the demo (Phase 9 webhook, Phase 10 API).

Both factories are re-exported lazily so importing this package never requires FastAPI:

* :func:`create_app` — the standalone Razorpay webhook app (Phase 9).
* :func:`create_api_app` — the full demo API: events, cases, run, audit, metrics, webhook.
"""

from __future__ import annotations

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Lazy proxy to :func:`revguard.api.webhooks.create_app` (defers the FastAPI import)."""
    from revguard.api.webhooks import create_app as _create_app

    return _create_app(*args, **kwargs)


def create_api_app(*args: Any, **kwargs: Any) -> Any:
    """Lazy proxy to :func:`revguard.api.app.create_api_app` (defers the FastAPI import)."""
    from revguard.api.app import create_api_app as _create_api_app

    return _create_api_app(*args, **kwargs)


__all__ = ["create_app", "create_api_app"]
