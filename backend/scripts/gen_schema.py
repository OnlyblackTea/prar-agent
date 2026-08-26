"""从 SHARED_SCHEMAS 生成 shared/schema.json."""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def generate(shared_schemas: list[type[BaseModel]]) -> dict[str, Any]:
    """从 SHARED_SCHEMAS 生成 JSON Schema Draft 2020-12."""
    definitions: dict[str, Any] = {}
    for model in shared_schemas:
        schema = model.model_json_schema(mode="serialization")
        name = model.__name__
        # 提取 $defs 合并到顶层 definitions
        if "$defs" in schema:
            definitions.update(schema.pop("$defs"))
        definitions[name] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
    }


def main() -> None:
    # 将 src 加入路径以便导入
    src = Path(__file__).resolve().parent.parent / "src"
    sys.path.insert(0, str(src))

    from app.shared.schemas import SHARED_SCHEMAS

    out = Path(__file__).resolve().parent.parent.parent / "shared" / "schema.json"
    out.parent.mkdir(exist_ok=True)
    schema = generate(SHARED_SCHEMAS)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    print(f"Generated {out} ({len(schema['$defs'])} schemas)")


if __name__ == "__main__":
    main()
