import os
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelAdapter
from app.llm.providers.base import PROVIDER_REGISTRY
from app.llm.router import LLMTransportError
from app.llm.types import ResolvedAdapter


class AdapterNotFoundError(Exception):
    def __init__(self, adapter_id: UUID) -> None:
        super().__init__(f"Adapter {adapter_id} not found")
        self.adapter_id = adapter_id


class AdapterService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(self, *, only_active: bool = True) -> list[ModelAdapter]:
        stmt = select(ModelAdapter).order_by(ModelAdapter.created_at)
        if only_active:
            stmt = stmt.where(ModelAdapter.is_active.is_(True))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, adapter_id: UUID) -> ModelAdapter:
        adapter = await self._db.get(ModelAdapter, adapter_id)
        if adapter is None:
            raise AdapterNotFoundError(adapter_id)
        return adapter

    async def create(self, payload: dict[str, Any]) -> ModelAdapter:
        provider_key = payload["provider"]
        spec = PROVIDER_REGISTRY.get(provider_key)
        if spec is None:
            raise ValueError(f"Unknown provider: {provider_key!r}")

        for field_name in spec.credentials_fields:
            if field_name not in payload.get("credentials_env", {}):
                raise ValueError(f"Missing credential field: {field_name!r}")

        spec.validate_config(payload)

        adapter = ModelAdapter(
            name=payload["name"],
            provider=provider_key,
            model=payload["model"],
            credentials_env=payload.get("credentials_env", {}),
            params=payload.get("params", {}),
        )
        self._db.add(adapter)
        await self._db.flush()
        return adapter

    async def update(self, adapter_id: UUID, payload: dict[str, Any]) -> ModelAdapter:
        adapter = await self.get(adapter_id)
        for key in ("name", "model", "credentials_env", "params", "is_active"):
            if key in payload:
                setattr(adapter, key, payload[key])
        await self._db.flush()
        return adapter

    async def soft_delete(self, adapter_id: UUID) -> None:
        adapter = await self.get(adapter_id)
        adapter.is_active = False
        await self._db.flush()

    async def set_default(self, adapter_id: UUID) -> ModelAdapter:
        adapter = await self.get(adapter_id)
        await self._db.execute(
            update(ModelAdapter)
            .where(ModelAdapter.is_default.is_(True))
            .values(is_default=False)
        )
        adapter.is_default = True
        await self._db.flush()
        return adapter

    async def get_default(self) -> ModelAdapter | None:
        result = await self._db.execute(
            select(ModelAdapter).where(ModelAdapter.is_default.is_(True))
        )
        return result.scalar_one_or_none()

    def resolve(self, adapter: ModelAdapter) -> ResolvedAdapter:
        spec = PROVIDER_REGISTRY.get(adapter.provider)
        if spec is None:
            raise LLMTransportError(
                f"Unknown provider: {adapter.provider!r}",
                model_id=adapter.model,
            )
        credentials: dict[str, str] = {}
        for field_name in spec.credentials_fields:
            env_name = adapter.credentials_env.get(field_name)
            if not env_name:
                raise LLMTransportError(
                    f"Missing credential field {field_name!r} for adapter {adapter.name!r}",
                    model_id=adapter.model,
                )
            value = os.environ.get(env_name)
            if not value:
                raise LLMTransportError(
                    f"Env var {env_name!r} not set",
                    model_id=adapter.model,
                )
            credentials[field_name] = value
        return ResolvedAdapter(
            id=adapter.id,
            name=adapter.name,
            provider=adapter.provider,
            model=adapter.model,
            credentials=credentials,
            params=dict(adapter.params),
        )
