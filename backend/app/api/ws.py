import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.postgres import SessionLocal
from app.db.redis import get_async_redis_client
from app.models.network import SimulationResult
from app.security import websocket_principal

router = APIRouter(tags=["WebSockets"])


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

    with SessionLocal() as db:
        exists = db.query(SimulationResult.id).filter(SimulationResult.id == simulation_uuid).first()
    if not exists:
        await websocket.close(code=1008, reason="Simulation not found")
        return

    await websocket.accept()

    pubsub = get_async_redis_client().pubsub()
    channel = f"sim_{sim_id}"
    await pubsub.subscribe(channel)

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                data = message["data"]
                await websocket.send_text(data)

                # Check for completion or failure
                parsed = json.loads(data)
                if parsed.get("status") in {"completed", "failed"}:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
