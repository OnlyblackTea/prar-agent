from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from app.llm.providers.base import PROVIDER_REGISTRY

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def list_providers() -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    for spec in PROVIDER_REGISTRY.values():
        providers.append({
            "key": spec.key,
            "label": spec.label,
            "credentials_fields": {
                k: asdict(v) for k, v in spec.credentials_fields.items()
            },
            "params_fields": {
                k: asdict(v) for k, v in spec.params_fields.items()
            },
        })
    return {"providers": providers}
