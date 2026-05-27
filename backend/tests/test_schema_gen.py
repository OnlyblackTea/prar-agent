"""Schema 自动生成管道测试."""

from pydantic import BaseModel
from scripts.gen_schema import generate

from app.shared.schemas import SHARED_SCHEMAS

# ===== T1: SHARED_SCHEMAS 非空 =====


def test_shared_schemas_not_empty() -> None:
    assert len(SHARED_SCHEMAS) >= 4


# ===== T2: 全是 BaseModel 子类 =====


def test_all_shared_schemas_are_pydantic_models() -> None:
    for model in SHARED_SCHEMAS:
        assert issubclass(model, BaseModel), f"{model} is not a BaseModel"


# ===== T3: generate() 返回有效 JSON Schema =====


def test_generate_produces_valid_json_schema() -> None:
    schema = generate(SHARED_SCHEMAS)
    assert "$schema" in schema
    assert "$defs" in schema
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


# ===== T4: $defs 包含所有注册模型 =====


def test_generate_contains_all_registered_schemas() -> None:
    schema = generate(SHARED_SCHEMAS)
    names = {m.__name__ for m in SHARED_SCHEMAS}
    assert names.issubset(set(schema["$defs"]))


# ===== T5: 每个 schema 有 type 和 properties =====


def test_each_schema_has_type_and_properties() -> None:
    schema = generate(SHARED_SCHEMAS)
    for name, definition in schema["$defs"].items():
        assert "type" in definition, f"{name} missing 'type'"
        assert "properties" in definition, f"{name} missing 'properties'"
