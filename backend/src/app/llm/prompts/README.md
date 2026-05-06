# Prompt 模板

本目录存放所有 LLM prompt 模板（`.md` 文件），便于不改代码迭代 prompt。

## 命名约定

- `planner.md` — Task 07 plan_engine 的 Planner Manager 角色
- `critic.md` — Task 11 critic 自审
- `merger.md` — Task 12 review_merger 合 comments → plan v{N+1}
- `executor.md` — Task 18 action_dispatcher 的 Programmer 角色

## 模板语法

用 Python `str.format(**ctx)` 渲染。占位符用 `{var_name}`。例：

```
你是项目经理。基于以下需求生成结构化计划：
INIT_REQUEST: {init_request}
LTM_RECALL: {ltm_recall}
AVAILABLE_TOOLS: {tool_registry}
```

caller 负责传入 ctx，router **不**渲染（router 收到的就是渲染后的最终 string）。
