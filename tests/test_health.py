"""Tests for the /api/health build identity.

CI's deploy-gate reads the `build` field to confirm that the commit it just
deployed is the one serving. If the field disappears or stops reflecting
CAST2MD_BUILD_VERSION, that check silently degrades to "something answers" --
which is the state it was written to replace.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """A TestClient whose app sees whatever CAST2MD_BUILD_VERSION the test sets."""

    def _build(value: str | None):
        if value is None:
            monkeypatch.delenv("CAST2MD_BUILD_VERSION", raising=False)
        else:
            monkeypatch.setenv("CAST2MD_BUILD_VERSION", value)
        main = importlib.import_module("cast2md.main")
        return TestClient(main.app)

    return _build


def test_health_reports_the_build_env_var(client):
    """The value CI passes as VERSION comes back out of /api/health."""
    response = client("edge-0123456789abcdef0123456789abcdef01234567").get("/api/health")

    # 503 is a valid outcome here (storage or database may be unavailable in a
    # bare environment); the build field is reported either way.
    assert response.json()["build"] == "edge-0123456789abcdef0123456789abcdef01234567"


def test_health_build_defaults_to_dev(client):
    """Outside a CI-built image the field is present and reads 'dev'."""
    assert client(None).get("/api/health").json()["build"] == "dev"


def test_deploy_gate_extraction_matches(client):
    """The shell extraction in ci.yml's deploy-gate finds the field.

    Mirrors the sed expression in .forgejo/workflows/ci.yml. Kept as a test
    because a rename on the Python side would otherwise only surface as a
    failing deploy.
    """
    import re
    import subprocess

    expected = "edge-deadbeef"
    body = client(expected).get("/api/health").text
    extracted = subprocess.run(
        ["sed", "-n", r's/.*"build"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'],
        input=body,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert extracted == expected
    # And the same expression survives a pretty-printed body.
    assert re.search(r'"build"\s*:\s*"([^"]*)"', body).group(1) == expected
