# 06. shared/schema.json 自动生成管道

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 01（骨架）、Task 04.1b（adapter schemas）
> **被依赖**：Task 09（前端骨架消费 TS 类型）、Task 10（Plan 渲染消费 Decision/Step schema）
> **commit 范围**：单个 commit

---

## 1. 目标

- **一句话**：`make gen-schema` 一键从 pydantic 模型导出 JSON Schema → 前端 TypeScript 类型，前后端契约零手写零漂移。
- **验收标准**：
  1. `make gen-schema` 输出 `shared/schema.json`（JSON Schema Draft 2020-12）
  2. `shared/schema.json` 包含所有标记为"共享"的 pydantic 模型
  3. `shared/types.ts` 由 `json-schema-to-typescript` 自动生成（Task 09 前端骨架建好后接入；本 task 仅验证 JSON Schema 可被该工具消费）
  4. CI 守护：`make check-schema` 检测 schema.json 是否与代码同步（`git diff --exit-code shared/schema.json`）
  5. `make test` 全绿，`make lint` 无警告

---

## 2. 现状问题

| # | 现状 | 问题 |
|---|------|------|
| P1 | 前后端无共享类型 | 前端手写 interface，改后端字段前端不知道 |
| P2 | pydantic schema 散落各模块 | 没有统一导出点 |

---

## 3. 方案设计

### 3.1 思路

```
pydantic models → registry 标记 → gen_schema.py 收集 →
model_json_schema() 导出 → shared/schema.json →
(Task 09 后) json-schema-to-typescript → shared/types.ts
```

### 3.2 共享 schema 标记机制

在 `app/shared/schemas.py` 集中 re-export 需要共享的模型，作为生成脚本的唯一入口：

```python
# app/shared/schemas.py — 所有需要导出到前端的 schema 在此注册
from app.api.adapters import (
    ModelAdapterCreate,
    ModelAdapterResponse,
    ModelAdapterUpdate,
)
from app.health import HealthResponse

SHARED_SCHEMAS: list[type] = [
    HealthResponse,
    ModelAdapterCreate,
    ModelAdapterUpdate,
    ModelAdapterResponse,
]
```

后续 Task 新增需要共享的 schema 时，只需在此文件 append。

### 3.3 生成脚本

`scripts/gen_schema.py`：

```python
"""从 SHARED_SCHEMAS 生成 shared/schema.json。"""
import json
import sys
from pathlib import Path
from pydantic import TypeAdapter

from app.shared.schemas import SHARED_SCHEMAS

def generate() -> dict:
    definitions: dict = {}
    for model in SHARED_SCHEMAS:
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
    out = Path(__file__).resolve().parent.parent / "shared" / "schema.json"
    out.parent.mkdir(exist_ok=True)
    schema = generate()
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    print(f"✓ {out} ({len(schema['$defs'])} schemas)")

if __name__ == "__main__":
    main()
```

### 3.4 目录结构

```
prar-agent/
├── shared/
│   ├── schema.json          # 生成产物，git tracked
│   └── types.ts             # Task 09 后由 json-schema-to-ts 生成
├── backend/
│   ├── src/app/shared/
│   │   ├── __init__.py
│   │   └── schemas.py       # SHARED_SCHEMAS 注册表
│   └── scripts/
│       └── gen_schema.py    # 生成脚本
```

### 3.5 Makefile 目标

```makefile
gen-schema:
	uv run python scripts/gen_schema.py

check-schema:
	uv run python scripts/gen_schema.py
	git diff --exit-code shared/schema.json || \
		(echo "ERROR: shared/schema.json out of sync — run 'make gen-schema'" && exit 1)
```

---

## 4. 文件清单

| 路径 | 类型 | 说明 |
|------|------|------|
| `backend/src/app/shared/__init__.py` | **新增** | 空 |
| `backend/src/app/shared/schemas.py` | **新增** | SHARED_SCHEMAS 注册表 |
| `backend/scripts/gen_schema.py` | **新增** | JSON Schema 生成脚本 |
| `shared/schema.json` | **新增（生成）** | 生成产物，git tracked |
| `shared/.gitkeep` | **新增** | 确保目录存在 |
| `backend/Makefile` | 改造 | 加 `gen-schema` / `check-schema` 目标 |
| `backend/tests/test_schema_gen.py` | **新增** | 生成脚本测试 |

---

## 5. 实施步骤

| # | 步骤 | 验证 |
|---|------|------|
| 1 | 创建 `shared/` 目录 + `.gitkeep` | 目录存在 |
| 2 | 写 `app/shared/schemas.py` — SHARED_SCHEMAS 列表 | import 不报错 |
| 3 | 写 `scripts/gen_schema.py` | `uv run python scripts/gen_schema.py` 生成 `shared/schema.json` |
| 4 | 验证 schema.json 内容正确（含 4 个 schema 定义） | 手动 check JSON 结构 |
| 5 | Makefile 加 `gen-schema` / `check-schema` | `make gen-schema` 成功 |
| 6 | 写 `tests/test_schema_gen.py` | make test 全绿 |
| 7 | `make lint && make test` | 0 error |

---

## 6. 测试清单

### `tests/test_schema_gen.py`（新增）

| # | 测试 | 断言 |
|---|------|------|
| T1 | `test_shared_schemas_not_empty` | `SHARED_SCHEMAS` 长度 ≥ 4 |
| T2 | `test_all_shared_schemas_are_pydantic_models` | 每个元素都是 `BaseModel` 子类 |
| T3 | `test_generate_produces_valid_json_schema` | `generate()` 返回 dict 含 `$schema` 和 `$defs` |
| T4 | `test_generate_contains_all_registered_schemas` | `$defs` 的 key 集合 ⊇ SHARED_SCHEMAS 的类名集合 |
| T5 | `test_each_schema_has_type_and_properties` | 每个 `$defs[name]` 含 `type` 和 `properties` 键 |

---

## 7. 设计决策

| 决策 | 理由 |
|------|------|
| 集中注册表而非 decorator 扫描 | 显式胜于隐式；改动清单一目了然；不依赖 import 顺序 |
| `schema.json` git tracked | CI 可 diff 检测漂移；前端不需要跑后端就能拿到类型 |
| `mode="serialization"` | 前端消费的是 API 响应（序列化形态），不是创建时的 input schema |
| `shared/` 在项目根而非 backend 内 | 前后端都能访问；Task 09 的 `json-schema-to-ts` 从此读入 |
| 暂不生成 `types.ts` | 前端骨架（Task 09）不存在，无 pnpm；本 task 只保证 JSON Schema 是有效输入 |

---

## 8. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `model_json_schema()` 对泛型 `StructuredResponse[T]` 生成不可控 | 中 | 低 | 不把 StructuredResponse 放入 SHARED_SCHEMAS（它是内部类型） |
| schema.json 忘记 regenerate | 中 | 低 | `check-schema` 在 CI / pre-commit 拦截 |

---

## 9. 决策题

无开放决策题。主人审阅后回 `APPROVED` 即开始编码。
