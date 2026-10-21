"""The HTTP connection the raw-`httpx` provider adapters share.

`anthropic`, `openrouter` and `openai_compatible` each speak their own wire --
their own payloads, headers, readers and errors, deliberately unshared -- but
they hold the client underneath it identically: an optional caller-owned
`httpx.AsyncClient`, else one built lazily on first use with a CA bundle that
survives a stale `$SSL_CERT_FILE`, and closed (and forgotten) only when it is
the adapter's own. That plumbing lives here once.

A gateway module like the adapters it serves: it imports nothing from
`grimoire.store` (#239).
"""

from __future__ import annotations

import os
import ssl

import certifi
import httpx


class ProviderHTTP:
    """A provider adapter's lazily built, optionally caller-owned HTTP client."""

    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _verify(self) -> ssl.SSLContext:
        # httpx trusts $SSL_CERT_FILE, but a stale value (e.g. left by a removed
        # conda install) points at a missing file and crashes TLS setup. Honor a
        # valid override; otherwise fall back to certifi's bundle.
        cert = os.environ.get("SSL_CERT_FILE")
        cafile = cert if cert and os.path.exists(cert) else certifi.where()
        return ssl.create_default_context(cafile=cafile)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=30.0), verify=self._verify()
            )
        return self._http

    async def aclose(self) -> None:
        # Reset, not just close: `_client()` is lazy, so leaving the closed
        # client in place would hand it back to the next caller (and every
        # request through it raises). `EmbeddingsClient.close` does the same.
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None
