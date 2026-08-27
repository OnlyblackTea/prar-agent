# 17. 内置工具（shell / fs.read / fs.write）

## 目标

- **一句话**：在 Task 15 的 `Tool[ArgsT]` 契约与 Task 16 的 `Sandbox` 之上，实现三个内置工具，产出 `builtin_tools()` 工厂供 Task 18 dispatcher 注册；本任务不接状态机、不碰 LLM router、不改前端。

- **验收标准**（缺一不可）：

  1. `cd backend && uv run pytest -q` 全绿（现有 212 + 本任务新增 ~20 用例）
  2. `uv run ruff check src tests` / `uv run mypy src tests` 零警告零错误（mypy strict）
  3. Linux VM（192.168.1.147）上 `test_builtin_tools.py` 的 POSIX 分支（`/bin/sh -c`）真实跑通，不 skip
  4. 三工具 `name` 与 `plan_engine._DEFAULT_TOOLS` 完全一致：`shell` / `fs.read` / `fs.write`

## 输入 / 输出

- **输入**：强类型 args（pydantic，`extra="forbid"`）+ `ExecContext`（workdir / run_shell / emit_stdout）
- **输出**：`ToolResult`（ok / output / artifacts / git_commit 恒 None）

失败语义红线（沿用 base.py 双轨）：

- 业务性失败（exit≠0、文件不存在、路径逃逸）→ `ToolResult(ok=False)`，LLM 观察 output 后换参数重试
- 环境故障（沙箱起不来、超时机制失效）→ `run_shell` 已 raise `ToolExecutionError`，工具层不捕获、直接向上抛

## 接口设计

### 目录结构

```
backend/src/app/tools/builtin/
  __init__.py   # builtin_tools() 工厂
  shell.py      # ShellTool + ShellArgs
  fs.py         # FsReadTool / FsWriteTool + args + 路径助手
```

### 1. ShellTool（name="shell"）

```python
class ShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(description="要在隔离工作目录中执行的 shell 命令")
    timeout: float | None = Field(
        default=None, description="超时秒数；None 用沙箱默认 300s"
    )
```

- `command` 是**字符串**，由系统 shell 解析（LLM 生成字符串命令最自然，与 planner prompt 的 `_DEFAULT_TOOLS` 语义一致）；注入风险由沙箱隔离兜底
- argv 组装：Windows `["cmd", "/d", "/s", "/c", command]`；POSIX `["/bin/sh", "-c", command]`
- 执行：`ctx.run_shell.run(argv, timeout=args.timeout, cwd=ctx.workdir)`
- 结果映射：exit_code==0 → `ok=True`；≠0（含 124 超时）→ `ok=False`
- output 固定三段格式（stderr 空时标注 `(empty)`，保证 LLM 可稳定解析）：

```
exit_code=0
stdout:
<...>
stderr:
<...>
```

- `rerunnable = False`（shell 命令副作用不可知，保守默认；Task 18 消费此属性）
- **不流式**：Sandbox.run 是全量返回接口，流式管道是 Task 19 的交付（15 的 R 表已预留）

### 2. FsReadTool（name="fs.read"）

```python
class FsReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="相对工作目录的文件路径")
    max_bytes: int = Field(
        default=262144, ge=1024, le=1048576, description="最大读取字节数（1KB..1MB）"
    )
```

- 路径解析（与 fs.write 共用私有助手 `_resolve_in_workdir`）：
  - **沙箱根来源**：fs 工具直接依赖 `Sandbox`（`isinstance(ctx.run_shell, Sandbox)` 取 `.root`；非 Sandbox → `ToolExecutionError`）。builtin → sandbox 单向依赖无循环；18 装配的 run_shell 恒为 Sandbox
  - `workdir` 自身逃出沙箱根 → `ToolExecutionError`（dispatcher 装配错误，属环境故障）；`path` 拒绝绝对路径 / 含 `..` / resolve 后逃出 `workdir` → `ok=False`（LLM 输入，业务失败）
- 不存在 → `ok=False`："file not found: <rel>"
- 是目录 → `ok=False`："is a directory: <rel>"
- UTF-8 解码失败 → `ok=False`：二进制文件不可读 + 文件大小（MVP 不支持 base64，走 §风险扩展）
- 超过 `max_bytes` → **截断**并在尾部注明 `[truncated: read N of M bytes]`（给 LLM 可行动信息，优于纯拒绝）
- output = 文件内容原样（无路径前缀，省 token）
- `rerunnable = True`

### 3. FsWriteTool（name="fs.write"）

```python
class FsWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="相对工作目录的文件路径（父目录自动创建）")
    content: str = Field(description="要写入的文本内容")
```

- 路径解析同 fs.read（逃逸 → `ok=False`）
- 父目录自动 `mkdir(parents=True, exist_ok=True)`
- **原子写**：同目录临时文件（`.<name>.tmp<pid>`）+ `os.replace`（防半写状态，Task 20 checkpoint 只会 git add 到完整文件）
- output：`wrote N bytes to <rel>`（N 为 UTF-8 编码后字节数）
- `artifacts = [Path(rel)]`（相对 workdir，遵守 15 约定）
- `rerunnable = True`（幂等）

### 4. 工厂

```python
# tools/builtin/__init__.py
def builtin_tools() -> list[Tool[Any]]:
    """返回三个内置工具实例（固定顺序：shell / fs.read / fs.write）。"""
    return [ShellTool(), FsReadTool(), FsWriteTool()]
```

- Task 18 dispatcher 循环 `registry.register(t)`；`tools/__init__.py` **不新增导出**（避免 tools 层所有模块都碰 builtin，保持按需 import）

## 文件清单

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `backend/src/app/tools/builtin/__init__.py` | 新建 | `builtin_tools()` 工厂 |
| `backend/src/app/tools/builtin/shell.py` | 新建 | `ShellTool` + `ShellArgs` |
| `backend/src/app/tools/builtin/fs.py` | 新建 | `FsReadTool` / `FsWriteTool` + args + `_resolve_in_workdir` |
| `backend/tests/test_builtin_tools.py` | 新建 | ~20 用例（见测试清单） |
| `backend/src/app/tools/__init__.py` | **不改** | 维持现状 |

零现有文件改动；零新增依赖（全部基于 15/16 已交付的标准库 + pydantic）。

## 实施步骤

1. **TDD 红**：写 `test_builtin_tools.py`（先失败——`app.tools.builtin` 不存在）
2. **绿**：实现 `builtin/` 三文件
3. **Windows 质量门**：`uv run pytest -q` + `ruff check` + `mypy` 三绿
4. **Linux VM 验证**：同步 backend 源码到 192.168.1.147，`uv run pytest tests/test_builtin_tools.py -v` 真实跑通（`/bin/sh` 分支）
5. **文档收尾 + commit**：本设计补「实施记录」章节（如发现平台约束），commit 交付主人 GPG 签名

## 测试清单（test_builtin_tools.py）

| ID | 用例 | 断言要点 |
| --- | --- | --- |
| T1 | 三工具元数据 | name / description / args_schema / rerunnable（shell=False，fs=True） |
| T2 | json_schema 无 additionalProperties | 三工具 schema 均不含 `additionalProperties` |
| T3 | `builtin_tools()` 工厂 | 三实例、顺序固定、注册进 `ToolRegistry` 无冲突、`to_specs()` 产出 3 spec |
| T4 | shell 成功 | 沙箱实跑 `echo`，exit 0，ok=True，output 三段格式含 stdout |
| T5 | shell 失败 | `exit 3` → ok=False，output 含 exit_code=3 与 stderr |
| T6 | shell 超时 | `timeout=1` + sleep → exit_code=124，ok=False，output 标注超时 |
| T7 | shell cwd 生效 | 命令输出当前目录 == workdir（沙箱视角） |
| T8 | fs.read 文本 | 内容原样返回、无路径前缀 |
| T9 | fs.read 不存在 | ok=False，output 含 file not found |
| T10 | fs.read 目录 | ok=False，output 含 is a directory |
| T11 | fs.read 二进制 | 写非 UTF-8 字节 → ok=False，output 含文件大小 |
| T12 | fs.read 截断 | max_bytes=1024 读大文件 → 尾部含 `[truncated: ...]` |
| T13 | fs.read 逃逸 | `../` 与绝对路径 → ok=False |
| T14 | fs.write 新文件 | ok=True，磁盘落盘，output 含字节数，artifacts=[rel] |
| T15 | fs.write 覆盖 | 二次写覆盖成功，内容为新值 |
| T16 | fs.write 嵌套目录 | 父目录自动创建 |
| T17 | fs.write 逃逸 | `../` 与绝对路径 → ok=False，磁盘无文件 |
| T18 | 闭环 | fs.write 写文件 → shell `cat` 读回内容一致 |

（T4-T18 全部基于真实 `Sandbox`（tmp_path 沙箱根）+ 真实 `ExecContext`，不用 fake；T18 覆盖"工具间协作"路径。）

## 风险与未决

| ID | 风险 | 对策 |
| --- | --- | --- |
| R1 | `cmd /s /c` 引号语义与 POSIX `/bin/sh -c` 不一致 | command 原样透传不加工；双平台各用官方 shell 语义，T4-T7 在双平台实跑覆盖 |
| R2 | LLM 生成 `command` 可能注入（`rm -rf` 等） | 沙箱隔离兜底（目录隔离 + 资源限制 + 树杀）；MVP 不做命令级策略审查，走 Task 18 决策 |
| R3 | fs.write 原子写临时文件残留（进程被杀） | 临时文件前缀 `.tmp` 且 Task 20 checkpoint 只 add artifacts；残留不阻塞，cleanup 时随沙箱根删除 |
| R4 | 超大 content 写入耗内存 | LLM 侧 token 上限天然约束；MVP 不加硬顶，Task 18 若暴露给多步流程再评估 |
| R5 | `max_bytes` 截断可能截断多字节 UTF-8 字符边界 | 用 `errors="replace"` 解码（不抛异常），尾部注明截断 |

### 已决策（默认值，主人不反对就这么走）

| ID | 决策点 | 决策 | 备选 |
| --- | --- | --- | --- |
| Q1 | command 字符串 vs argv 列表 | **字符串 + 系统 shell 解析** | argv 列表（LLM 生成繁琐） |
| Q2 | rerunnable | **shell=False、fs.read/fs.write=True** | 全部默认 True |
| Q3 | fs.read 超限 | **截断 + 尾注** | 纯拒绝 |
| Q4 | 流式输出 | **17 不流式**（Sandbox 全量接口） | 17 就做流式（越界，19 的活） |
| Q5 | shell 暴露 env 参数 | **不暴露**（沙箱默认继承宿主 env + 安全注入不可覆盖） | 暴露（YAGNI） |
| Q6 | fs.read 二进制 | **ok=False + 文件大小**（不支持 base64） | base64 透传（MVP 不需要） |
| Q7 | fs.read max_bytes 上限 | **1MB 硬顶**（pydantic le 约束） | 无上限 |
| Q8 | tools/__init__ 导出 | **不导出** builtin（18 从 `app.tools.builtin` 导入） | 全量导出 |

如以上 8 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 实施记录（2026-08-27）

**验收结果**：

1. Windows 开发机：全套 234 passed / 4 skipped；`ruff check` / `mypy strict`（68 文件）零警告零错误
2. Linux VM（192.168.1.147）：`test_builtin_tools.py` 22 passed（`/bin/sh -c` 分支真实跑通）
3. 本任务新增 `test_builtin_tools.py` 22 用例（T4-T18 全部真实 Sandbox 实跑）

**实施中发现的平台约束与适配**：

- **pydantic 2.13.3 行为变化**：`extra="forbid"` 模型现在输出 `additionalProperties: False` 键（15 设计时版本不输出）。T2 断言适配为 `json_schema.get("additionalProperties", False) is not True`（验证 extra=forbid 生效，版本无关）。Task 18 转换层若需 OpenAI strict 兼容（不出现该键）再处理。
- **Windows cmd `timeout` 不支持输入重定向**：测试环境的 stdin 非控制台时 `timeout /t` 直接报错退出。超时用例改用 `ping -n 11 127.0.0.1`（loopback 不受代理黑洞影响）。
- **RLIMIT_NPROC 按用户全量进程数计算**：沙箱设 `max_processes=8` 时，VM 上 lqz 用户已有 >8 进程 → 沙箱内外部命令 fork 全部失败（`Cannot fork`）；shell 内置命令（echo/pwd/cd）不 fork 所以不受影响。builtin 测试 fixture 改 `max_processes=0`（NPROC 专测留在 test_sandbox.py）。**Task 18 装配 limits 时若设 max_processes，需考虑宿主用户已有进程数**。
- **fs 工具沙箱根来源**：设计细化——`run_shell` 必须是 `Sandbox`（`isinstance` 检查取 `.root`，否则 `ToolExecutionError`），已同步写入接口设计节。
