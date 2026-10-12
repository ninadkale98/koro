"""Thin Supabase client: every koro operation is one Postgres function called over
PostgREST (POST /rest/v1/rpc/<function>). Standard library only."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


class KoroError(Exception):
    """A readable error from the database or the network."""


def auth_headers(key: str) -> dict:
    headers = {"apikey": key}
    # Legacy service_role keys are JWTs and also go in Authorization. New
    # sb_secret_ keys are not JWTs: the API gateway reads them from apikey only.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


Transport = Callable[[str, dict], Any]


class Client:
    def __init__(self, url: str, key: str, transport: Optional[Transport] = None, timeout: float = 60.0):
        if not url or not key:
            raise KoroError("koro is not configured: run `koro init` (or set KORO_SUPABASE_URL and KORO_SUPABASE_KEY)")
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self._transport = transport or self._http

    def rpc(self, function: str, **params: Any) -> Any:
        body = {f"p_{k}": v for k, v in params.items() if v is not None}
        return self._transport(function, body)

    def _http(self, function: str, body: dict) -> Any:
        req = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/{function}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={**auth_headers(self.key), "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(detail)
                message = err.get("message") or detail
                if err.get("hint"):
                    message += f" (hint: {err['hint']})"
            except json.JSONDecodeError:
                message = detail or exc.reason
            if exc.code in (401, 403):
                message = f"{message} — check that KORO_SUPABASE_KEY is the project's secret key"
            if exc.code == 404:
                message = f"{message} — is the koro schema applied to this project? (see README)"
            raise KoroError(f"{function}: {message}") from None
        except urllib.error.URLError as exc:
            raise KoroError(f"cannot reach {self.url}: {exc.reason}") from None
        if not raw:
            return None
        return json.loads(raw)
