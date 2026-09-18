import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.postgres import get_db
from app.models.network import Edge, Network, Node
from app.schemas.network import CentralityScore, EdgeBase, NetworkBase, NodeBase
from app.security import enforce_rate_limit, require_viewer
from app.services.analytics import calculate_centrality

router = APIRouter(
    prefix="/networks",
    tags=["Networks"],
    # Rate limit runs before auth: FastAPI resolves dependencies in order and
    # stops at the first exception, so an auth check listed first would let a
    # bad or missing API key 401 before the limiter ever saw the request --
    # unlimited-rate key brute forcing. The limiter has to see every request
    # regardless of whether it turns out to be authenticated.
    dependencies=[Depends(enforce_rate_limit), Depends(require_viewer)],
)
logger = logging.getLogger(__name__)


def _set_pagination_headers(response: Response, *, total: int, offset: int, returned: int) -> None:
    """Report the true size of the collection alongside a possibly-truncated page.

    The response body stays a bare JSON array -- every existing consumer
    (the frontend, e2e_test.py, data/scripts/demo_scenario.py) keeps working
    unchanged. These headers are what let a caller that cares distinguish "the
    network has exactly this many nodes" from "the network has more than this
    many nodes and got cut off at the limit", which the response body alone
    cannot say.
    """
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Has-More"] = "true" if offset + returned < total else "false"


@router.get("", response_model=list[NetworkBase])
def list_networks(
    response: Response,
    limit: int = Query(default=100, ge=1, le=1_000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List available infrastructure networks."""
    total = db.query(func.count(Network.id)).scalar() or 0
    items = db.query(Network).order_by(Network.created_at.desc()).offset(offset).limit(limit).all()
    _set_pagination_headers(response, total=total, offset=offset, returned=len(items))
    return items


@router.get("/{network_id}/nodes", response_model=list[NodeBase])
def get_nodes(
    network_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=5_000, ge=1, le=10_000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Get nodes for a specific network, paginated by offset/limit."""
    total = db.query(func.count(Node.id)).filter(Node.network_id == network_id).scalar() or 0
    nodes = (
        db.query(Node)
        .filter(Node.network_id == network_id)
        .order_by(Node.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    if total == 0:
        net = db.query(Network.id).filter(Network.id == network_id).first()
        if not net:
            raise HTTPException(status_code=404, detail="Network not found")
    _set_pagination_headers(response, total=total, offset=offset, returned=len(nodes))
    return nodes


@router.get("/{network_id}/edges", response_model=list[EdgeBase])
def get_edges(
    network_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=10_000, ge=1, le=20_000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Get edges for a specific network, paginated by offset/limit."""
    network = db.query(Network.id).filter(Network.id == network_id).first()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    total = db.query(func.count(Edge.id)).filter(Edge.network_id == network_id).scalar() or 0
    edges = (
        db.query(Edge)
        .filter(Edge.network_id == network_id)
        .order_by(Edge.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    _set_pagination_headers(response, total=total, offset=offset, returned=len(edges))
    return edges


@router.get("/{network_id}/centrality", response_model=list[CentralityScore])
def get_centrality(
    network_id: uuid.UUID,
    metric: Literal["betweenness", "pagerank"] = Query(default="betweenness"),
    db: Session = Depends(get_db),
):
    """
    Calculate and return centrality scores for all nodes in the network.
    Default metric is Betweenness Centrality (primary), with PageRank as secondary.
    """
    # Verify network exists
    net = db.query(Network).filter(Network.id == network_id).first()
    if not net:
        raise HTTPException(status_code=404, detail="Network not found")
        
    try:
        results = calculate_centrality(str(network_id), metric=metric, db=db)
    except Exception:
        logger.exception("centrality calculation failed for network %s with metric %s", network_id, metric)
        raise HTTPException(status_code=503, detail="Centrality service unavailable")

    # Defensive enrichment: If results lack name/node_type, populate from PostgreSQL
    missing_meta = any("name" not in r or "node_type" not in r for r in results)
    if missing_meta and results:
        node_records = db.query(Node).filter(Node.network_id == network_id).all()
        node_lookup = {str(n.id): n for n in node_records}
        for r in results:
            node = node_lookup.get(str(r.get("node_id")))
            if node:
                r.setdefault("name", node.name)
                r.setdefault("display_name", getattr(node, "display_name", None) or node.name)
                r.setdefault("node_type", node.node_type)
                r.setdefault("is_synthetic", getattr(node, "is_synthetic", True))
                r.setdefault("data_source", getattr(node, "data_source", "synthetic"))
                r.setdefault("name_source", getattr(node, "name_source", "synthetic"))
                r.setdefault("data_quality", getattr(node, "data_quality", "estimated"))
            else:
                r.setdefault("name", f"Node {str(r.get('node_id'))[:8]}")
                r.setdefault("display_name", f"Node {str(r.get('node_id'))[:8]}")
                r.setdefault("node_type", "unknown")
                r.setdefault("is_synthetic", True)
                r.setdefault("data_source", "synthetic")
                r.setdefault("name_source", "synthetic")
                r.setdefault("data_quality", "estimated")

    return results
