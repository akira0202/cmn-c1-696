"""Unit tests for GraphApiClient — CMN-C1-696. Covers BL-10/11, OData & URL escaping."""

import httpx
import pytest

from src.services.graph_api_client import GraphApiClient, GraphApiError


class Resp:
    def __init__(self, code, js=None, text="") -> None:
        self.status_code, self._js, self.text = code, js, text

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


def _token_ok(url):
    return "oauth2" in url


def test_get_user_404_returns_none(monkeypatch):
    def h(method, url, **kw):
        return Resp(200, {"access_token": "t"}) if _token_ok(url) else Resp(404)

    monkeypatch.setattr(httpx, "request", h)
    assert GraphApiClient("t", "c", "s").get_user("a@b.co") is None


def test_get_user_url_encodes_guest_upn(monkeypatch):
    seen = {}

    def h(method, url, **kw):
        seen["url"] = url
        return Resp(200, {"access_token": "t"}) if _token_ok(url) else Resp(200, {"id": "U"})

    monkeypatch.setattr(httpx, "request", h)
    GraphApiClient("t", "c", "s").get_user("foo_ext#EXT#@contoso.com")
    path = seen["url"].split("/users/")[1]
    assert "%23" in path and "#" not in path


def test_get_groups_odata_escaping(monkeypatch):
    seen = {}

    def h(method, url, **kw):
        seen["params"] = kw.get("params")
        return Resp(200, {"access_token": "t"}) if _token_ok(url) else Resp(200, {"value": [{"id": "G"}]})

    monkeypatch.setattr(httpx, "request", h)
    GraphApiClient("t", "c", "s").get_groups_by_display_name("O'Brien Team")
    assert seen["params"]["$filter"] == "displayName eq 'O''Brien Team'"


def test_add_member_204_added(monkeypatch):
    def h(method, url, **kw):
        return Resp(200, {"access_token": "t"}) if _token_ok(url) else Resp(204)

    monkeypatch.setattr(httpx, "request", h)
    assert GraphApiClient("t", "c", "s").add_member("G", "U") == "added"


def test_add_member_already_exists_idempotent(monkeypatch):  # BL-10
    def h(method, url, **kw):
        if _token_ok(url):
            return Resp(200, {"access_token": "t"})
        return Resp(400, {"error": {"message": "One or more added object references already exist"}})

    monkeypatch.setattr(httpx, "request", h)
    assert GraphApiClient("t", "c", "s").add_member("G", "U") == "already_member"


def test_add_member_other_400_raises(monkeypatch):
    def h(method, url, **kw):
        if _token_ok(url):
            return Resp(200, {"access_token": "t"})
        return Resp(400, {"error": {"message": "Invalid object identifier"}})

    monkeypatch.setattr(httpx, "request", h)
    with pytest.raises(GraphApiError):
        GraphApiClient("t", "c", "s").add_member("G", "U")


def test_add_member_retries_on_429(monkeypatch):  # BL-11
    import shared.utils.retry_policy as rp

    monkeypatch.setattr(rp.time, "sleep", lambda _s: None)  # no real backoff delay
    n = {"calls": 0}

    def h(method, url, **kw):
        if _token_ok(url):
            return Resp(200, {"access_token": "t"})
        n["calls"] += 1
        return Resp(429) if n["calls"] == 1 else Resp(204)

    monkeypatch.setattr(httpx, "request", h)
    assert GraphApiClient("t", "c", "s").add_member("G", "U") == "added"
    assert n["calls"] >= 2  # retried past the 429
