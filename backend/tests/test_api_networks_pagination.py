"""Tests for offset/limit pagination on the /networks list endpoints.

The response body stays a bare JSON array for backward compatibility with
every existing consumer; the true collection size and whether the page was
truncated are reported via X-Total-Count / X-Has-More response headers
instead of a body-shape change. These tests pin that contract at the
function level, the same pattern test_api_scenarios.py uses for
create_scenario -- a MagicMock db rather than a live Postgres/PostGIS
instance, since the ORM models bind a PostGIS Geometry column SQLite cannot
represent.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

# Lean-environment shims are installed once for the whole suite by
# tests/conftest.py, before this file is collected.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import Response

from app.api.networks import _set_pagination_headers, get_edges, get_nodes, list_networks


def _mock_db(*, total: int, page_items: list):
    """A db whose query chain answers a count query and an items query alike.

    Both `.query(func.count(...)).filter(...).scalar()` and
    `.query(Model).filter(...).order_by(...).offset(...).limit(...).all()`
    call `.filter()` on the object `.query()` returns, so setting both
    `.scalar` and the `.order_by...` chain on that one shared mock answers
    whichever path the endpoint actually takes.
    """
    db = MagicMock()

    def query_side_effect(*_args, **_kwargs):
        m = MagicMock()
        m.filter.return_value.scalar.return_value = total
        m.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = (
            page_items
        )
        # list_networks has no network_id filter, so it chains straight off
        # the query mock rather than through .filter() first.
        m.order_by.return_value.offset.return_value.limit.return_value.all.return_value = page_items
        m.scalar.return_value = total
        return m

    db.query.side_effect = query_side_effect
    return db


def test_set_pagination_headers_reports_truncation():
    response = Response()
    _set_pagination_headers(response, total=10, offset=0, returned=5)
    assert response.headers["X-Total-Count"] == "10"
    assert response.headers["X-Has-More"] == "true"


def test_set_pagination_headers_reports_exact_fit():
    response = Response()
    _set_pagination_headers(response, total=5, offset=0, returned=5)
    assert response.headers["X-Total-Count"] == "5"
    assert response.headers["X-Has-More"] == "false"


def test_set_pagination_headers_accounts_for_offset():
    # A second page that reaches the end must not be reported as truncated
    # just because a full-sized page was returned.
    response = Response()
    _set_pagination_headers(response, total=10, offset=5, returned=5)
    assert response.headers["X-Has-More"] == "false"


def test_get_nodes_reports_truncation_when_more_remain():
    network_id = uuid.uuid4()
    page = [MagicMock(id=uuid.uuid4()) for _ in range(2)]
    db = _mock_db(total=5, page_items=page)
    response = Response()

    result = get_nodes(network_id=network_id, response=response, limit=2, offset=0, db=db)

    assert result == page
    assert response.headers["X-Total-Count"] == "5"
    assert response.headers["X-Has-More"] == "true"


def test_get_edges_reports_no_truncation_when_page_covers_total():
    network_id = uuid.uuid4()
    page = [MagicMock(id=uuid.uuid4()) for _ in range(3)]
    db = _mock_db(total=3, page_items=page)
    response = Response()

    result = get_edges(network_id=network_id, response=response, limit=10_000, offset=0, db=db)

    assert result == page
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Has-More"] == "false"


def test_list_networks_sets_pagination_headers():
    page = [MagicMock(id=uuid.uuid4()) for _ in range(1)]
    db = _mock_db(total=1, page_items=page)
    response = Response()

    result = list_networks(response=response, limit=100, offset=0, db=db)

    assert result == page
    assert response.headers["X-Total-Count"] == "1"
    assert response.headers["X-Has-More"] == "false"
