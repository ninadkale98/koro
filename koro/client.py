"""Thin Supabase client: every koro operation is one Postgres function called over
PostgREST (POST /rest/v1/rpc/<function>). Standard library only."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
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
# (method, path under /storage/v1/, body, headers) -> response bytes
StorageTransport = Callable[[str, str, Optional[bytes], dict], bytes]


class Client:
    def __init__(self, url: str, key: str, transport: Optional[Transport] = None, timeout: float = 60.0,
                 storage_transport: Optional[StorageTransport] = None):
        if not url or not key:
            raise KoroError("koro is not configured: run `koro init` (or set KORO_SUPABASE_URL and KORO_SUPABASE_KEY)")
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self._transport = transport or self._http
        self._storage = storage_transport or self._storage_http

    def rpc(self, function: str, **params: Any) -> Any:
        body = {f"p_{k}": v for k, v in params.items() if v is not None}
        return self._transport(function, body)

    # -- Storage (private bucket; only the secret key can read or write) --------------

    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> Any:
        raw = self._storage("POST", f"object/{bucket}/{_quote(path)}", data,
                            {"Content-Type": content_type, "x-upsert": "true", "Cache-Control": "max-age=3600"})
        return json.loads(raw) if raw else None

    def download(self, bucket: str, path: str) -> bytes:
        return self._storage("GET", f"object/{bucket}/{_quote(path)}", None, {})

    def sign(self, bucket: str, path: str, expires_in: int = 3600) -> str:
        """A temporary URL anyone holding it can open (default one hour)."""
        raw = self._storage("POST", f"object/sign/{bucket}/{_quote(path)}",
                            json.dumps({"expiresIn": expires_in}).encode(), {"Content-Type": "application/json"})
        signed = json.loads(raw).get("signedURL") or json.loads(raw).get("signedUrl")
        if not signed:
            raise KoroError(f"storage: no signed URL for {bucket}/{path}")
        return signed if signed.startswith("http") else f"{self.url}/storage/v1{signed}"

    def _storage_http(self, method: str, path: str, body: Optional[bytes], headers: dict) -> bytes:
        req = urllib.request.Request(f"{self.url}/storage/v1/{path}", data=body, method=method,
                                     headers={**auth_headers(self.key), **headers})
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 300)) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(detail)
                message = err.get("message") or err.get("error") or detail
            except json.JSONDecodeError:
                message = detail or exc.reason
            if exc.code in (401, 403):
                message = f"{message} — check that KORO_SUPABASE_KEY is the project's secret key"
            raise KoroError(f"storage {method} {path.split('?')[0]}: {message} (HTTP {exc.code})") from None
        except urllib.error.URLError as exc:
            raise KoroError(f"cannot reach {self.url}: {exc.reason}") from None

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


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe="/._-")
