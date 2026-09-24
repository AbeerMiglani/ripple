import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.db.postgres import SessionLocal
from app.db.redis import get_async_redis_client
from app.models.network import SimulationResult
from app.security import websocket_principal

router = APIRouter(tags=["WebSockets"])

#: Bounds how long a connection waits for a simulation to settle. Matches the
#: Celery task's own hard cap (task_time_limit, celery_app.py) plus margin
#: for queueing/network latency -- the same bound the frontend's REST polling
#: fallback (pollSimulationUntilSettled) uses, so neither channel gives up
#: before a run that is still legitimately in progress could complete.
_MAX_WAIT_SECONDS = 150
_POLL_TIMEOUT_SECONDS = 1.0


def _current_status(simulation_uuid: uuid.UUID) -> str | None:
    """The run's durable status, or None when it does not exist.

    A blocking SQLAlchemy round trip: callers in this async module must go
    through ``_status`` so the query runs on a worker thread. Called inline it
    stalled the event loop -- and with it every other request and socket this
    process serves -- once per second for every open connection.
    """
    with SessionLocal() as db:
        row = db.query(SimulationResult.status).filter(SimulationResult.id == simulation_uuid).first()
    return row[0] if row else None


async def _status(simulation_uuid: uuid.UUID) -> str | None:
    return await run_in_threadpool(_current_status, simulation_uuid)


@router.websocket("/ws/simulations/{sim_id}")
async def simulation_websocket(websocket: WebSocket, sim_id: str):
    try:
        websocket_principal(websocket)
        simulation_uuid = uuid.UUID(sim_id)
    except (ValueError, TypeError):
        await websocket.close(code=1008, reason="Invalid simulation identifier")
        return
    except Exception:
        # Authentication failures intentionally do not disclose key details.
        await websocket.close(code=1008, reason="Unauthorized")
        return

    status = await _status(simulation_uuid)
    if status is None:
        await websocket.close(code=1008, reason="Simulation not found")
        return

    await websocket.accept()

    # Already settled by the time the client subscribed. Redis pub/sub does
    # not replay missed messages, so a fast simulation -- or just a client
    # slow to connect -- previously left this connection subscribed to a
    # channel that would never publish again, with nothing to ever receive.
    if status in {"completed", "failed"}:
        await websocket.send_text(json.dumps({"status": status}))
        return

    pubsub = get_async_redis_client().pubsub()
    channel = f"sim_{sim_id}"
    await pubsub.subscribe(channel)

    try:
        elapsed = 0.0
        while elapsed < _MAX_WAIT_SECONDS:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SECONDS)
            if message:
                data = message["data"]
                await websocket.send_text(data)

                parsed = json.loads(data)
                if parsed.get("status") in {"completed", "failed"}:
                    return
                continue

            # No pub/sub message this tick -- fall back to the durable row
            # directly, so a publish this connection raced past (subscribed
            # a moment after the task published) or Redis simply dropped is
            # still caught within about a second, rather than left hanging
            # for the rest of the wait budget.
            elapsed += _POLL_TIMEOUT_SECONDS
            current = await _status(simulation_uuid)
            if current in {"completed", "failed"}:
                await websocket.send_text(json.dumps({"status": current}))
                return

        await websocket.close(code=1000, reason="Timed out waiting for the simulation to settle")
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
