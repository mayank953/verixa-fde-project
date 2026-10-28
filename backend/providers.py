"""Outside services: live or sample.

EXTERNAL_MODE=sample (the default in .env.example) answers every outside service from recorded, fictional
examples in data/fixtures/, so nothing leaves the machine, nothing costs money and no real person is looked up.
EXTERNAL_MODE=live calls the real services and needs their keys. The code default is "live" on purpose: a deployment
must never fall back to sample data by accident.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
DISPOSABLE_DOMAINS = {"tempmail.example", "fakemail.example", "throwaway.example"}


def external_mode() -> str:
    return os.getenv("EXTERNAL_MODE", "live").strip().lower()


def sample() -> bool:
    return external_mode() == "sample"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load(name: str) -> dict:
    p = FIXTURES / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------- Apify look-alike (LinkedIn, YouTube, Google search)
class _Dataset:
    def __init__(self, items): self._items = items
    def iterate_items(self): return iter(self._items)


class _Actor:
    def __init__(self, owner): self._owner = owner
    def call(self, run_input=None):
        self._owner._items = self._owner._lookup(json.dumps(run_input or {}))
        return {"defaultDatasetId": "sample"}


class SampleApify:
    """Same calls as apify_client.ApifyClient (actor(...).call(...), dataset(...).iterate_items()), answered from a fixture file."""
    def __init__(self, kind: str):
        self._kind, self._items = kind, []
    def _lookup(self, text: str) -> list:
        text_n = _norm(text)
        for name, items in _load(self._kind).items():
            if _norm(name) in text_n:
                return items
        return []
    def actor(self, actor_id): return _Actor(self)
    def dataset(self, dataset_id): return _Dataset(self._items)


def apify_client(token: str, kind: str):
    if sample():
        return SampleApify(kind)
    from apify_client import ApifyClient
    return ApifyClient(token)


# ---------------------------------------------------------------- httpx look-alike (SerpAPI, XposedOrNot, Disify)
class SampleResponse:
    def __init__(self, status_code: int, data): self.status_code, self._data = status_code, data
    def json(self): return self._data
    @property
    def text(self): return json.dumps(self._data)
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("sample error", request=None, response=self)


class SampleAsyncClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    async def get(self, url, params=None, headers=None, **k):
        if "serpapi.com" in url:
            q = (params or {}).get("q", "")
            for name, entry in _load("serp").items():
                if _norm(name) in _norm(q):
                    for suffix, results in entry.get("queries", {}).items():
                        if q.lower().endswith(suffix):
                            return SampleResponse(200, {"organic_results": results})
            return SampleResponse(200, {"organic_results": []})
        if "xposedornot.com" in url:
            email = url.rsplit("/", 1)[-1].lower()
            hit = _load("breach").get(email)
            return SampleResponse(200, {"breaches": hit}) if hit else SampleResponse(404, {})
        if "disify.com" in url:
            email = url.rsplit("/", 1)[-1].lower()
            domain = email.split("@")[-1]
            return SampleResponse(200, {"format": True, "dns": True, "disposable": domain in DISPOSABLE_DOMAINS, "alias": False})
        return SampleResponse(404, {})


def async_client(**kwargs):
    return SampleAsyncClient() if sample() else httpx.AsyncClient(**kwargs)
