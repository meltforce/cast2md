"""Guards on route declaration order.

FastAPI matches routes in the order they are declared. A parameterised segment
declared ahead of a literal one swallows it: `/api/queue/{job_id}` sat above
`/api/queue/stuck` and `/api/queue/all`, so both answered 422 `int_parsing`
from the day they were added until 2026-08-07, and nothing failed because
neither had a caller or a test.

This checks the shape rather than the two paths, so the next literal route
added below a parameterised one fails here instead of in production.
"""

import importlib
import re

import pytest
from fastapi.routing import APIRoute

SEGMENT = re.compile(r"\{[^}]+\}")


@pytest.fixture(scope="module")
def routes():
    """Every APIRoute of the assembled application, in declaration order."""
    main = importlib.import_module("cast2md.main")
    return [r for r in main.app.routes if isinstance(r, APIRoute)]


def _shadows(pattern: str, literal: str) -> bool:
    """Whether a request for `literal` would be matched by `pattern` first.

    True when the two have the same number of segments and every segment of
    `pattern` either equals the literal's segment or is a path parameter.
    """
    p, lit = pattern.strip("/").split("/"), literal.strip("/").split("/")
    if len(p) != len(lit):
        return False
    return all(a == b or SEGMENT.fullmatch(a) for a, b in zip(p, lit))


def test_no_parameterised_route_shadows_a_literal_one(routes):
    """A literal path is never declared below a parameter that would catch it."""
    shadowed = []

    for i, candidate in enumerate(routes):
        if SEGMENT.search(candidate.path):
            continue  # only literal paths can be shadowed
        for earlier in routes[:i]:
            if not SEGMENT.search(earlier.path):
                continue
            if not (earlier.methods & candidate.methods):
                continue
            if _shadows(earlier.path, candidate.path):
                shadowed.append(
                    f"{sorted(candidate.methods)} {candidate.path} is declared below "
                    f"{sorted(earlier.methods)} {earlier.path}, which matches it first"
                )

    assert not shadowed, "unreachable routes:\n  " + "\n  ".join(shadowed)


@pytest.mark.parametrize("path", ["/api/queue/all", "/api/queue/stuck"])
def test_the_two_queue_routes_resolve_to_their_own_endpoint(routes, path):
    """Named explicitly because these are the two the guard above was written for."""
    matching = [r for r in routes if r.path == path and "GET" in r.methods]
    assert matching, f"{path} is not registered at all"

    index = routes.index(matching[0])
    catch_all = [
        r
        for r in routes[:index]
        if "GET" in r.methods and _shadows(r.path, path) and SEGMENT.search(r.path)
    ]
    assert not catch_all, f"{path} is caught by {[r.path for r in catch_all]}"
