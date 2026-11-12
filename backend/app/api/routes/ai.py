from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.ai.analyst import AnalystUnavailable, analyst_status, run_analyst_turn
from app.api.deps import DB, CurrentUser, PlantDep
from app.db.models import Conversation

router = APIRouter(prefix="/plants/{plant_id}/ai", tags=["ai"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


def _conv_out(c: Conversation, with_messages: bool = False) -> dict:
    out = {
        "id": c.id,
        "plant_id": c.plant_id,
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "message_count": len(c.messages),
    }
    if with_messages:
        out["messages"] = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "usage": m.usage,
                "created_at": m.created_at.isoformat(),
            }
            for m in c.messages
        ]
    return out


@router.get("/status")
def get_status() -> dict:
    return analyst_status()


@router.post("/chat")
def chat(payload: ChatRequest, plant: PlantDep, db: DB, user: CurrentUser) -> dict:
    conversation = None
    if payload.conversation_id is not None:
        conversation = db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.plant_id != plant.id or conversation.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    try:
        return run_analyst_turn(db, plant, user, payload.message, conversation)
    except AnalystUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/conversations")
def list_conversations(plant: PlantDep, db: DB, user: CurrentUser, limit: int = Query(30, le=200)) -> dict:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.plant_id == plant.id, Conversation.user_id == user.id)
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
    ).all()
    return {"items": [_conv_out(c) for c in rows]}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, plant: PlantDep, db: DB, user: CurrentUser) -> dict:
    c = db.get(Conversation, conversation_id)
    if c is None or c.plant_id != plant.id or c.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return _conv_out(c, with_messages=True)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, plant: PlantDep, db: DB, user: CurrentUser):
    c = db.get(Conversation, conversation_id)
    if c is None or c.plant_id != plant.id or c.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    db.delete(c)
    db.commit()
