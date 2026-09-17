"""The UI is a plain HTTP client, so what is worth testing is the request it sends."""

from typing import Any

import httpx
import pytest

from app.auth import decode_token
from app.settings import get_settings
from ui import streamlit_app


class _Response:
    status_code = 200
    content = b"{}"

    def json(self) -> dict[str, object]:
        return {"answer": "ok"}


def test_ask_sends_a_persona_token_and_never_the_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _Response:
        sent.update(kwargs, url=url)
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)

    code, body = streamlit_app.ask("What are the salary bands?", "sales")

    assert (code, body) == (200, {"answer": "ok"})
    assert sent["json"] == {"question": "What are the salary bands?"}, "groups are never sent"

    token = sent["headers"]["Authorization"].removeprefix("Bearer ")
    principal = decode_token(token, get_settings())
    assert principal.subject == "demo-sales"
    assert principal.groups == frozenset({"all-staff", "sales"})
