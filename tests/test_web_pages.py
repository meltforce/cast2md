"""Every HTML page renders.

Starlette 1.x takes the request as the first argument of `TemplateResponse`;
the older `(name, context)` order raises `TypeError: unhashable type: 'dict'`
inside Jinja and the page answers 500. No API test reaches a template.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

PAGES = [
    "/",
    "/episodes",
    "/feeds",
    "/status",
    "/admin",
    "/settings",
    "/admin/settings",
    "/queue",
    "/admin/queue",
    "/admin/runpod",
    "/search",
]


@pytest.fixture
def client(db_conn):
    return TestClient(importlib.import_module("cast2md.main").app)


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:300]
    assert response.headers["content-type"].startswith("text/html")
    assert "</html>" in response.text


@pytest.mark.parametrize("path", ["/feeds/2147483000", "/episodes/2147483000"])
def test_missing_record_renders_the_error_page(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "not found" in response.text


def test_detail_pages_render(client, sample_episode):
    for path in (f"/feeds/{sample_episode.feed_id}", f"/episodes/{sample_episode.id}"):
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text[:300]}"


@pytest.mark.parametrize("path", ["/", "/queue", "/settings"])
def test_node_page_renders(path, tmp_path, monkeypatch):
    """The transcriber node's own UI, without a worker and without a config."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from cast2md.node.server import create_app

    response = TestClient(create_app()).get(path)
    assert response.status_code == 200, response.text[:300]
    assert "</html>" in response.text
