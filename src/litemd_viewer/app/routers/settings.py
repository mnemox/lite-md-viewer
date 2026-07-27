"""Key/value application settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Setting
from ..schemas import SettingRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(session: Session = Depends(get_session)) -> dict[str, str | None]:
    return {s.key: s.value for s in session.scalars(select(Setting)).all()}


@router.put("/{key}")
def put_setting(
    key: str, req: SettingRequest, session: Session = Depends(get_session)
) -> dict:
    setting = session.get(Setting, key)
    if setting is None:
        session.add(Setting(key=key, value=req.value))
    else:
        setting.value = req.value
    session.commit()
    return {"key": key, "value": req.value}
