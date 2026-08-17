"""HTTP contracts for serving the built administrative web shell."""

from __future__ import annotations

import asyncio

from app.main import create_app


async def asgi_get(application, path: str) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    messages: list[dict[str, object]] = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 443),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], body, start["headers"]


def test_built_web_shell_is_served_without_capturing_health_endpoint(tmp_path):
    (tmp_path / "index.html").write_text("<main>AIsales administrative shell</main>", encoding="utf-8")
    application = create_app(web_directory=tmp_path)

    root_status, root_body, root_headers = asyncio.run(asgi_get(application, "/"))
    setup_status, setup_body, setup_headers = asyncio.run(asgi_get(application, "/setup"))
    health_status, _health_body, _health_headers = asyncio.run(asgi_get(application, "/healthz"))

    assert root_status == 200
    assert root_body == b"<main>AIsales administrative shell</main>"
    assert setup_status == 200
    assert setup_body == root_body
    assert (b"referrer-policy", b"no-referrer") in setup_headers
    assert (b"cache-control", b"no-store") in setup_headers
    assert b"text/html" in next(value for name, value in root_headers if name == b"content-type")
    assert health_status == 503
