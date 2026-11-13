"""Guards on route declaration order.

FastAPI matches routes in the order they are declared. A parameterised segment
declared ahead of a literal one swallows it: `/api/queue/{job_id}` sat above
`/api/queue/stuck` and `/api/queue/all`, so both answered 422 `int_parsing`
from the day they were added until 2026-08-07, and nothing failed because
neither had a caller or a test.

This checks the shape rather than the two paths, so the next literal route
added below a parameterised one fails here instead of in production.

The routes are read from `app.openapi()["paths"]`. `app.routes` is not usable
for this: since FastAPI 0.162 an included router is one opaque entry there, not
its routes. The schema lists a path once, at the position of the method that
was declared first for it, so its order decides a pair only in two cases:

- a single-method literal path listed above the parameterised one is reachable;
- a literal path listed below a single-method parameterised one is shadowed.

Every other pair is asked: a GET goes to the literal path and the route that
matched is read from the ASGI scope. A pair that shares only another method
cannot be asked, because sending it would run the endpoint: it is reported
unless SOURCE_ORDER names it, and those pairs are checked against the order of
the decorators in the source file. Routes declared with
`include_in_schema=False` are not in the schema; SCHEMA_EXCLUDED names them and
they are requested as well.
"""

import importlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SEGMENT = re.compile(r"\{[^}]+\}")
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}

# Pairs the schema cannot order and a request must not probe:
# (method, literal path, parameterised path) -> (source file, literal
# decorator, parameterised decorator). The literal decorator has to come first.
SOURCE_ORDER = {
    ("DELETE", "/api/nodes/stale", "/api/nodes/{node_id}"): (
        "api/nodes.py",
        '@router.delete("/stale"',
        '@router.delete("/{node_id}"',
    ),
}

# Every route declared with include_in_schema=False. All are literal paths.
SCHEMA_EXCLUDED = ["/healthz", "/manifest.webmanifest"]


@pytest.fixture(scope="module")
def app():
    return importlib.import_module("cast2md.main").app


def declared(app: FastAPI) -> list[tuple[str, set[str]]]:
    """(path, methods) of every route in the schema, in declaration order."""
    return [
        (path, {m.upper() for m in operations if m in HTTP_METHODS})
        for path, operations in app.openapi()["paths"].items()
    ]


def _shadows(pattern: str, literal: str) -> bool:
    """Whether a request for `literal` would be matched by `pattern` first.

    True when the two have the same number of segments and every segment of
    `pattern` either equals the literal's segment or is a path parameter.
    """
    p, lit = pattern.strip("/").split("/"), literal.strip("/").split("/")
    if len(p) != len(lit):
        return False
    return all(a == b or SEGMENT.fullmatch(a) for a, b in zip(p, lit))


def matched_path(app: FastAPI, path: str) -> str | None:
    """The path template of the route a GET for `path` is routed to.

    Starlette's router writes the matched route into the ASGI scope before it
    calls the endpoint, so the answer holds also when the endpoint fails for
    want of a database.
    """
    seen: dict = {}

    async def recording(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            if scope["type"] == "http":
                seen.update(scope)

    TestClient(recording, raise_server_exceptions=False).get(path)
    route = seen.get("route")
    return getattr(route, "path", None)


def shadowed(app: FastAPI) -> list[str]:
    """Literal paths that a parameterised path declared above them catches."""
    routes = declared(app)
    found = []
    for i, (path, methods) in enumerate(routes):
        if SEGMENT.search(path):
            continue  # only literal paths can be shadowed
        for j, (other, other_methods) in enumerate(routes):
            shared = other_methods & methods
            if not SEGMENT.search(other) or not shared or not _shadows(other, path):
                continue
            pair = f"{sorted(shared)} {path} and {sorted(other_methods)} {other}"
            if i < j and len(methods) == 1:
                continue  # declared before every method of the other path
            if i > j and len(other_methods) == 1:
                found.append(f"{pair}: the parameterised path is declared first")
            elif "GET" not in shared:
                if all((m, path, other) in SOURCE_ORDER for m in shared):
                    continue  # test_source_order_pairs_are_declared_literal_first
                found.append(f"{pair}: the schema cannot order them; add the pair to SOURCE_ORDER")
            elif (got := matched_path(app, path)) != path:
                found.append(f"{pair}: GET {path} is routed to {got}")
    return found


def test_no_parameterised_route_shadows_a_literal_one(app):
    """A literal path is never declared below a parameter that would catch it."""
    found = shadowed(app)
    assert not found, "unreachable routes:\n  " + "\n  ".join(found)


def test_the_guard_reports_a_shadowed_route():
    """The guard fails on the shape of the 2026-08-07 defect, also through an
    included router, and passes once the literal route is declared first."""
    from fastapi import APIRouter

    def build(literal_first: bool, delete_first: bool) -> FastAPI:
        router = APIRouter(prefix="/api/queue")
        if delete_first:
            router.delete("/{job_id}")(lambda job_id: job_id)

        def by_id(job_id: int):
            return job_id

        def stuck():
            return []

        if literal_first:
            router.get("/stuck")(stuck)
            router.get("/{job_id}")(by_id)
        else:
            router.get("/{job_id}")(by_id)
            router.get("/stuck")(stuck)
        application = FastAPI()
        application.include_router(router)
        return application

    assert shadowed(build(literal_first=False, delete_first=False)) == [
        "['GET'] /api/queue/stuck and ['GET'] /api/queue/{job_id}: "
        "the parameterised path is declared first"
    ]
    # With an earlier DELETE on the parameterised path the schema lists that
    # path first in both orders; the request decides.
    assert shadowed(build(literal_first=False, delete_first=True)) == [
        "['GET'] /api/queue/stuck and ['DELETE', 'GET'] /api/queue/{job_id}: "
        "GET /api/queue/stuck is routed to /api/queue/{job_id}"
    ]
    assert shadowed(build(literal_first=True, delete_first=False)) == []
    assert shadowed(build(literal_first=True, delete_first=True)) == []


@pytest.mark.parametrize("path", ["/api/queue/all", "/api/queue/stuck"])
def test_the_two_queue_routes_resolve_to_their_own_endpoint(app, path):
    """Named explicitly because these are the two the guard above was written for."""
    assert "get" in app.openapi()["paths"].get(path, {}), f"{path} is not registered at all"
    assert matched_path(app, path) == path


@pytest.mark.parametrize("pair", sorted(SOURCE_ORDER))
def test_source_order_pairs_are_declared_literal_first(pair):
    import pathlib

    import cast2md

    file, literal, parameterised = SOURCE_ORDER[pair]
    source = (pathlib.Path(cast2md.__file__).parent / file).read_text()
    assert source.count(literal) == 1 and source.count(parameterised) == 1
    assert source.index(literal) < source.index(parameterised), f"{pair}: declared below"


@pytest.mark.parametrize("path", SCHEMA_EXCLUDED)
def test_routes_outside_the_schema_answer_themselves(app, path):
    """A route excluded from the schema is invisible to the guard above, so it
    is requested: 200 from its own endpoint, not a parameter route's 422 or
    the 404 of a path nothing serves."""
    assert path not in app.openapi()["paths"]
    assert TestClient(app).get(path).status_code == 200


def test_schema_excluded_list_is_complete():
    """SCHEMA_EXCLUDED names every `include_in_schema=False` in the source."""
    import pathlib

    import cast2md

    source = pathlib.Path(cast2md.__file__).parent
    count = sum(p.read_text().count("include_in_schema=False") for p in source.rglob("*.py"))
    assert count == len(SCHEMA_EXCLUDED)
