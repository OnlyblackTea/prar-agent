from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.adapter_service import AdapterNotFoundError, AdapterService

router = APIRouter(prefix="/api/adapters", tags=["adapters"])


# ===== Pydantic schemas =====


class ModelAdapterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=128)
    credentials_env: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class ModelAdapterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    model: str | None = Field(None, min_length=1, max_length=128)
    credentials_env: dict[str, str] | None = None
    params: dict[str, Any] | None = None
    is_active: bool | None = None


class ModelAdapterResponse(BaseModel):
    id: UUID
    name: str
    provider: str
    model: str
    credentials_env: dict[str, str]
    params: dict[str, Any]
    is_default: bool
    is_active: bool

    model_config = {"from_attributes": True}


# ===== Dependency（占位，DB session middleware 建好后替换） =====


async def get_adapter_service(
    db: AsyncSession = Depends(get_db),
) -> AdapterService:
    return AdapterService(db)


# ===== Routes =====


@router.get("", response_model=list[ModelAdapterResponse])
async def list_adapters(
    only_active: bool = True,
    service: AdapterService = Depends(get_adapter_service),
) -> list[ModelAdapterResponse]:
    adapters = await service.list(only_active=only_active)
    return [ModelAdapterResponse.model_validate(a) for a in adapters]


@router.get("/{adapter_id}", response_model=ModelAdapterResponse)
async def get_adapter(
    adapter_id: UUID,
    service: AdapterService = Depends(get_adapter_service),
) -> ModelAdapterResponse:
    try:
        adapter = await service.get(adapter_id)
    except AdapterNotFoundError as e:
        raise HTTPException(status_code=404, detail="adapter_not_found") from e
    return ModelAdapterResponse.model_validate(adapter)


@router.post("", response_model=ModelAdapterResponse, status_code=201)
async def create_adapter(
    payload: ModelAdapterCreate,
    service: AdapterService = Depends(get_adapter_service),
) -> ModelAdapterResponse:
    try:
        adapter = await service.create(payload.model_dump())
    except ValueError as e:
        detail = "provider_unknown" if "Unknown provider" in str(e) else str(e)
        raise HTTPException(status_code=400, detail=detail) from e
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="adapter_name_conflict") from e
    return ModelAdapterResponse.model_validate(adapter)


@router.put("/{adapter_id}", response_model=ModelAdapterResponse)
async def update_adapter(
    adapter_id: UUID,
    payload: ModelAdapterUpdate,
    service: AdapterService = Depends(get_adapter_service),
) -> ModelAdapterResponse:
    try:
        adapter = await service.update(
            adapter_id, payload.model_dump(exclude_unset=True)
        )
    except AdapterNotFoundError as e:
        raise HTTPException(status_code=404, detail="adapter_not_found") from e
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="adapter_name_conflict") from e
    return ModelAdapterResponse.model_validate(adapter)


@router.delete("/{adapter_id}", status_code=204)
async def delete_adapter(
    adapter_id: UUID,
    hard: bool = False,
    service: AdapterService = Depends(get_adapter_service),
) -> None:
    try:
        if hard:
            adapter = await service.get(adapter_id)
            db = service._db
            await db.delete(adapter)
            await db.flush()
        else:
            await service.soft_delete(adapter_id)
    except AdapterNotFoundError as e:
        raise HTTPException(status_code=404, detail="adapter_not_found") from e
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="adapter_in_use") from e


@router.post("/{adapter_id}/set-default", response_model=ModelAdapterResponse)
async def set_default_adapter(
    adapter_id: UUID,
    service: AdapterService = Depends(get_adapter_service),
) -> ModelAdapterResponse:
    try:
        adapter = await service.set_default(adapter_id)
    except AdapterNotFoundError as e:
        raise HTTPException(status_code=404, detail="adapter_not_found") from e
    return ModelAdapterResponse.model_validate(adapter)
