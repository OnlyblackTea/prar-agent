# 20. Git Checkpoint（每 step 一 commit）

> 对应 ROADMAP M3 #20：`core/checkpoint.py` + 每 step 一 commit，message 固定格式。
> 注意：ROADMAP 原文写 `pygit2`，本设计改为 **git CLI subprocess**（Q1 已决策，偏离 ROADMAP，主人可推翻）。

## 目标

- 一句话目标：实现 `GitCheckpoint`（`core/checkpoint.py`），在 ACTING 执行期间把每个**成功 step** 的沙箱工作目录变更提交为沙箱本地 git 仓库中的一个 commit，message 固定格式，为 M4-26 局部 rerun（`git revert <step_commit>`）提供 commit 定位与回滚基础。
- 验收标准：
  1. 每成功 step 恰一个 commit；失败 step 不产生 commit（Q4），`git log --grep <step_id>` 可唯一定位（26 依赖契约）；
  2. commit message 固定格式，含 `plan_version` 与 `step_id`（Q5）；
  3. 零新增 Python 依赖（git CLI subprocess + 标准库），双平台（Windows + Linux）同等支持；
  4. Windows 开发机：全套测试全绿，`ruff check` / `mypy strict` 零警告零错误；
  5. Linux（VM 192.168.1.147，SSH）：`test_checkpoint.py` + `test_action_dispatcher.py` 真实跑通（真实 git CLI，不 skip）；
  6. checkpoint 环境故障（git 缺失、init 失败、超时）→ raise `ToolExecutionError`，dispatcher 停机（base.py 失败语义红线）。

## 输入 / 输出

- 上游产物：
  - Task 15 `tools/base.py`：`ToolResult.git_commit` 预留字段（注释"Task 20 填"）；
  - Task 16 `tools/sandbox.py`：`Sandbox(root=sandbox/runs/<session_id>)`，`steps/<step_id>` 工作目录；
  - Task 18 `core/action_dispatcher.py`：`execute_plan` 每 step 的 `record = await self._execute_step(step, ctx)` 挂载点；
  - Task 19 `api/ws_act.py`：`step.done` 事件 = `record.model_dump(mode="json")`，StepExecution 加字段自动透传前端。
- 本任务交付物：
  - `backend/src/app/core/checkpoint.py`（新增，~120 行）
  - `backend/src/app/core/action_dispatcher.py`（修改：`StepExecution.git_commit` 字段 + `execute_plan` 挂 init/commit_step）
  - `backend/tests/test_checkpoint.py`（新增，~10 用例）
  - `backend/tests/test_action_dispatcher.py`（修改：新增 D1-D4 + 既有断言随字段更新）
  - `backend/tests/test_ws_act.py`（修改：`step.done` 事件断言随字段更新，若存在严格相等断言）
  - `docs/design/15-tool-abc-registry.md`（修改：追加设计变更——`ToolResult.git_commit` 语义澄清）
  - `docs/design/18-action-dispatcher.md`（修改：追加设计变更——`StepExecution.git_commit` 字段说明）

## 接口设计

### `GitCheckpoint` 类

```python
class GitCheckpoint:
    """沙箱本地 git 仓库的 checkpoint 提交器。git CLI subprocess，零新增依赖。

    与失败语义红线对齐：
      - git 缺失 / init 失败 / 命令超时 → raise ToolExecutionError（环境故障，dispatcher 停机）
      - 不存在"业务性失败"分支：checkpoint 不面向 LLM 重试
    """

    def __init__(
        self,
        repo_root: Path,                      # = Sandbox.root（沙箱根即 git 根）
        *,
        author_name: str = "prar-agent",      # 每命令 -c user.name 注入，不依赖宿主 gitconfig
        author_email: str = "agent@prar.local",
        timeout: float = 30.0,                # 单条 git 命令兜底超时（本地操作应秒级完成）
    ) -> None: ...

    async def init(self, *, plan_version: int) -> None:
        """git init（repo 已存在时幂等跳过）+ 基线空 commit。

        基线 commit message：`[prar:v{plan_version}] init`（--allow-empty）。
        作用：26 号任务 revert 第一个 step commit 时需要父提交——
        git 对根 commit 的 revert 语义上不可行，基线使每个 step commit 都有父。
        """

    async def commit_step(
        self, *, plan_version: int, step_id: str, title: str,
    ) -> str:
        """git add -A + git commit --allow-empty -m <msg>；返回完整 40 位 hash（`git rev-parse HEAD`）。

        message 格式（26 定位契约，改动须走 WORKFLOW §5 设计变更）：
            `[prar:v{plan_version}:{step_id}] {title or step_id}`
        --allow-empty：成功但无文件变更的 step 也产生 commit，
        保证「每成功 step 恰一 commit」不变量（26 按 step 定位唯一）。
        """
```

内部私有 helper `_run_git(*args) -> str`：`asyncio.create_subprocess_exec("git", *args, cwd=self._repo_root)`，每条命令前缀 `-c user.name=... -c user.email=...`；`asyncio.wait_for(proc.communicate(), timeout)`；非零退出 / 超时 → `ToolExecutionError`（附 stderr 摘录）。

### 为什么 git CLI 而非 pygit2（Q1，偏离 ROADMAP）

1. **零新增依赖**：pygit2 是 libgit2 的 C 扩展，Windows 轮子体积大、双平台（本机 Windows + VM Linux）安装与 mypy stubs 都有成本；项目 15-19 连续 5 个任务坚持"零新增依赖"（ctypes/resource 标准库实现）。
2. **26 号任务 revert 流程本身就是 CLI 操作**（`git revert <step_commit>`），CLI 方案下 20 与 26 的操作面一致，便于验证 message 契约。
3. **checkpoint 是低频本地操作**（每 step 一条 commit，秒级），subprocess 开销可忽略；pygit2 的在进程内优势（高频操作）在 MVP 不成立。
4. git CLI 是双平台开发环境已存在的事实依赖（Windows Git Bash / VM 均可用，实施步骤 6 前置验证）。

### dispatcher 集成点

```python
# execute_plan 开头（sandbox.ensure_root() 之后）
checkpoint = GitCheckpoint(sandbox.root)
await checkpoint.init(plan_version=plan_version)

# 每 step（_execute_step 返回后、sink.step_done 之前，保证事件携带 git_commit）
record = await self._execute_step(step, ctx)
if record.ok:
    commit = await checkpoint.commit_step(
        plan_version=plan_version, step_id=ctx.step_id, title=step.title,
    )
    record = record.model_copy(update={"git_commit": commit})
if sink is not None:
    await self._emit_safely(sink.step_done(record=record))
records.append(record)
if not record.ok:
    break  # fail-fast；失败 step 无 commit
```

### 字段落点：`StepExecution.git_commit`（Q7 的一部分）

15 号在 `ToolResult.git_commit` 预留了"Task 20 填"的注释，但 checkpoint 发生在 dispatcher 层（工具执行完之后），工具层返回 ToolResult 时 commit 尚不存在；在 dispatcher 层回填 ToolResult 需要重建整个结果模型（`model_copy` 传染到各调用点），不如把 commit hash 落在 StepExecution 上：

- `StepExecution` 新增 `git_commit: str | None = None`（成功 step 填 40 位 hash，失败/未集成恒 None）；
- `ToolResult.git_commit` 保持 None 不启用，15 文档追加设计变更说明（M5+ 若出现"工具内 checkpoint"场景再启用）；
- `step.done` WS 事件经 `model_dump(mode="json")` 自动携带该字段，前端零改动（M3-21 面板可直接展示）。

### 数据流

```
execute_plan
  ├─ Sandbox.ensure_root() → GitCheckpoint(sandbox.root).init → [baseline] [prar:v{n}] init
  └─ 每 step
       ├─ _execute_step → StepExecution(ok=...)
       ├─ ok → git add -A + git commit --allow-empty -m "[prar:v{n}:{step_id}] {title}"
       │        └─ git rev-parse HEAD → record.git_commit
       └─ step.done(record) → WS 前端
```

## 文件清单

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `backend/src/app/core/checkpoint.py` | 新增 | `GitCheckpoint` + `_run_git`（~120 行） |
| `backend/src/app/core/action_dispatcher.py` | 修改 | `StepExecution.git_commit` 字段；`execute_plan` 挂 init/commit_step |
| `backend/tests/test_checkpoint.py` | 新增 | C1-C10（真实 git CLI 集成测试） |
| `backend/tests/test_action_dispatcher.py` | 修改 | 新增 D1-D4；既有断言随字段更新 |
| `backend/tests/test_ws_act.py` | 修改 | `step.done` 事件断言随字段更新（若存在严格相等断言） |
| `docs/design/15-tool-abc-registry.md` | 修改 | 追加设计变更：`ToolResult.git_commit` 不在工具层填充的语义澄清 |
| `docs/design/18-action-dispatcher.md` | 修改 | 追加设计变更：`StepExecution.git_commit` 字段说明 |

## 实施步骤

1. 写 `tests/test_checkpoint.py`（TDD 红：`app.core.checkpoint` 不存在 → `ModuleNotFoundError`）
2. 实现 `core/checkpoint.py`（`_run_git` + `GitCheckpoint.init` / `commit_step`）
3. dispatcher 集成：`StepExecution.git_commit` + `execute_plan` 挂载；更新 `test_action_dispatcher.py`（D1-D4）与受影响的既有断言（含 `test_ws_act.py`）
4. 15 / 18 设计文档各追加设计变更章节
5. `cd backend && make test && make lint && make typecheck` 全绿（Windows 开发机）
6. **Linux VM 验证**：SSH 192.168.1.147 前置检查 `git --version`（缺失则按纪律提醒主人补齐，不降级）→ 同步 backend → 跑 `tests/test_checkpoint.py` + `tests/test_action_dispatcher.py` 全绿（真实 git）
7. 实施记录追加到本设计文档
8. commit：设计文档 + 代码 + 测试同 commit，message `feat(backend): Git checkpoint 每 step 一 commit (M3-20)`，`Refs: docs/design/20-git-checkpoint.md`

## 测试清单

### `test_checkpoint.py`（真实 git CLI 集成测试；`tmp_path` 隔离；测试进程注入 `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_NOSYSTEM` 隔离宿主 gitconfig）

| # | 测试 | 断言 |
| --- | --- | --- |
| C1 | init 建仓 | `.git` 存在；`git log` 恰 1 条，message=`[prar:v1] init` |
| C2 | init 幂等 | 已存在 repo 再 init → 不抛；再建一条基线（log=2） |
| C3 | commit_step 基本 | 返回 40 位 hex；log 递增；message 精确匹配 `[prar:v1:step_001] {title}` |
| C4 | 文件入库 | 预写 `steps/step_001/a.txt` → commit → `git show HEAD:steps/step_001/a.txt` 内容一致 |
| C5 | 空变更 commit | 无任何变更 commit_step → 仍新增 commit（--allow-empty 生效） |
| C6 | 身份注入 | `git log --format=%an\|%ae` = `prar-agent\|agent@prar.local`（宿主 gitconfig 已隔离） |
| C7 | title 特殊字符 | 中文 + 引号 title → commit 成功且 message 原样 |
| C8 | 非零退出 | `repo_root` 指向一个文件路径 → init raise `ToolExecutionError`，异常消息含 stderr 摘录 |
| C9 | 超时转换 | monkeypatch `asyncio.wait_for` 抛 `TimeoutError` → `ToolExecutionError`（转换逻辑单测） |
| C10 | grep 定位（26 契约） | 多 step commit 后 `git log --grep step_001` 恰命中 1 条且为对应 commit |

### `test_action_dispatcher.py` 新增

| # | 测试 | 断言 |
| --- | --- | --- |
| D1 | 成功 step 集成 | `record.git_commit` 非空；沙箱根 repo `git log` 含该 commit；step 写出的文件已入库 |
| D2 | 失败 step 无 commit | `record.git_commit is None`；repo 仅基线（log=1）；fail-fast 停止后续 step |
| D3 | 多 step 各一 commit | 两成功 step → log = 1 基线 + 2 |
| D4 | 同 session 二次执行 | 第二次 `execute_plan` → 第二条基线 + 各自 step commit（init 幂等，无交叉污染） |

### 边缘情况

- `title` 为空 → message 用 step_id 兜底（`title or step_id`）。
- step 文件含二进制/大文件 → `git add -A` 全量入库，MVP 无大小上限（与 16 的 stdout 无上限同口径，风险表记录）。
- 会话级 `sandbox.cleanup()` 会连同 `.git` 一并删除 → 正常语义（本任务不 cleanup，保持 18 现状）；26 号 rerun 需要 repo 存续，其设计的 cleanup 时机另行决策。

## 风险与未决

### 已识别风险

| 风险 | 缓解 |
| --- | --- |
| agent 可通过 shell 工具操作框架管理的 `.git`（误 reset/checkout 破坏 checkpoint 链） | MVP 接受并文档记录；M5+ 候选：shell 工具 argv 黑名单禁 git，或框架 repo 移出沙箱根 |
| 宿主 gitconfig 干扰（身份缺失/签名 hook） | 每条命令 `-c user.name/-c user.email` 注入固定身份；测试进程隔离宿主 config；沙箱 repo 的 `.git/hooks` 为 git init 默认（sample 不生效） |
| git 版本差异（`-b` 参数、默认分支名） | 不用 `git init -b`，不引用分支名——checkpoint 与 26 一律引用 HEAD / commit hash |
| 超时误杀慢 git（大量文件 add） | 30s 兜底仅防挂死；MVP 文件量级小，超限场景 M5+ Docker 沙箱再议 |
| `ToolResult.git_commit` 预留与落点不一致 | 15 文档追加设计变更说明，字段语义以本文档为准 |
| VM 上 git 缺失 | 实施步骤 6 前置 `git --version` 检查；缺失按纪律提醒主人补齐，不降级 |
| 26 号 revert 的冲突语义（revert 后重跑 step 与后续 commit 的叠加） | 20 只承诺「每 step 一 commit + message 可定位」；revert 冲突处理是 26 自己的设计域，本文档不越界 |

### 已决策（默认值，主人不反对就这么走）

| # | 项目 | 决策 | 反对就告诉我 |
| --- | --- | --- | --- |
| Q1 | 实现载体 | **git CLI subprocess**（偏离 ROADMAP 的 pygit2；理由见上） | "坚持 pygit2" |
| Q2 | 仓库位置 | **沙箱根 `sandbox/runs/<session_id>`**（`steps/` 全树入库，与 16 Q6 生命周期一致） | "独立 checkpoints/ 目录" |
| Q3 | 基线空 commit | **init 后建 `[prar:v{n}] init`**（26 revert 第一个 step commit 需要父提交） | "不建基线" |
| Q4 | 失败 / 空变更 | **失败 step 不 commit；成功但空变更 `--allow-empty` 仍 commit**（保证每成功 step 恰一 commit） | "空变更跳过" |
| Q5 | message 格式 | **`[prar:v{plan_version}:{step_id}] {title}`**（26 `git log --grep` 定位契约） | "其他格式" |
| Q6 | 提交身份 | **每命令 `-c user.name=prar-agent -c user.email=agent@prar.local`**，不依赖宿主 gitconfig | "用宿主 gitconfig" |
| Q7 | 失败语义与落点 | **checkpoint 环境故障 → `ToolExecutionError` 停机**；commit hash 落 `StepExecution.git_commit`（`ToolResult.git_commit` 预留不启用，15 追加设计变更说明） | "业务失败透传 / 回填 ToolResult" |
| Q8 | agent 可见框架 repo | **MVP 接受 shell 工具可操作 `.git` 的风险**，文档记录 | "现在禁 shell 的 git argv" |

如以上 8 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

---

## 实施记录（2026-09-03）

交付：`core/checkpoint.py`（`GitCheckpoint` + `_run_git`，约 75 行）+ `tests/test_checkpoint.py`（C1-C10）+ dispatcher 集成（`StepExecution.git_commit` 字段 + `execute_plan` 挂 init/commit_step）+ `tests/test_action_dispatcher.py` 新增 T22-T25 + 15/18 文档设计变更章节。零新增依赖。`test_ws_act.py` 零改动（W4 对 `step.done` 仅键级断言，字段经 `model_dump` 透传不破坏）。

### 验收数据

- Windows：pytest 284 passed / 4 skipped；ruff 零问题；mypy 74 文件零问题
- Linux VM（192.168.1.147，真实 git 2.43.0）：`test_checkpoint.py` + `test_action_dispatcher.py` 36 passed（1.70s）

### 行为发现

1. **测试隔离宿主 gitconfig**：C1-C10 用 autouse fixture 把 `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` 指向不存在路径 + `GIT_CONFIG_NOSYSTEM=1`，确保 C6 身份断言（`prar-agent|agent@prar.local`）不受宿主配置干扰。
2. **C9 超时模拟**：monkeypatch `asyncio.wait_for`，fake 内先 `coro.close()`（防未完成协程告警）再 raise `TimeoutError` → 断言 `ToolExecutionError` 消息含 "timed out"。
3. **C8 spawn 失败**：`repo_root` 指向文件路径 → git spawn 抛 OSError → 消息含 "git spawn failed"（与超时分支分离，双错误契约）。
4. **T25 同 session 二次执行**：第二次 `execute_plan` 按 init 幂等语义追加第二条基线 + 新 step commit，两次执行 `git_commit` 不同（hash 含时间戳），无交叉污染。
5. **hash 落点验证**：T22 `git show {commit}:steps/step_001/out.txt` 内容一致——证明 commit 捕获的是工具执行后的完整工作树。

---

## 设计变更（2026-09-04，来自 M4-26 局部 rerun）

**作废的契约**：本文档「验收标准 1」与 C10 承诺的 `git log --grep <step_id>` **可唯一定位** step commit，在引入局部 rerun 后不再成立。

- 原因：rerun 走 `git revert`（保留历史），同一 step_id 会同时存在「原 commit」和「`Revert "[prar:v{n}:{sid}] ..."`」两条记录；多轮 rerun 后重跑产生的新 commit 再叠加一条，`--grep` 命中数 ≥ 2。
- C10 本身仍绿：它只在「单次执行、无 rerun」的仓库上断言命中 1 条，属于该场景的回归保护，不再代表通用保证。
- 26 号实际定位方式：`GitCheckpoint.rollback_to` 全量扫 `git log --format=%H%x00%s`，用 `_step_id_of` 同时解析普通主题与 `Revert "..."` 主题，已解析出的 step_id 进 `reverted` 集合抵消，遇目标 step_id 停止收集 → 天然幂等（二次调用返回 0）。
- `last_run.steps[].git_commit`（DB）降级为**用户可见的审计留痕**，不参与 revert 定位。
- 仍然有效的契约：message 格式 `[prar:v{plan_version}:{step_id}] {title}`、每成功 step 恰一 commit、失败 step 无 commit、基线空 commit（revert 根提交需要父）。

后续任务若需按 step 定位 commit，走 `rollback_to` / `_step_id_of`，不要直接用 `--grep`。
