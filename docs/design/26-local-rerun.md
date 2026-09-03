# 26. 局部 Rerun（git revert + 重跑指定 step + 状态机回退）

> 对应 ROADMAP M4 #26：`局部 rerun（git revert <step_commit> + 重跑指定 step + state machine 回退）`，验收：**失败能修能重，git 历史干净**。
> 上游：任务 20 已交付每成功 step 一 commit（message 契约 `[prar:v{version}:{step_id}] {title}`，失败 step 不 commit）；任务 23 已交付 `metadata_json["last_run"]`（step 级 ok/fail + git_commit 定位）。本任务把两者拼成 rerun 闭环；UI 入口（重跑按钮）是任务 27 的 Action Review UI。

## 目标

- 一句话目标：ACTION_REVIEW 阶段用户指定某个 step 重跑——后端先 `git revert` 回退该 step 起的 commit，再从该 step 重执行到 plan 末尾，状态机 ACTION_REVIEW → ACTING 回退；历史线性可审计，工作区状态与 last_run 一致。
- 验收标准：
  1. 某 step 失败后（fail-fast 或 review 不满意），重跑该 step 及其后续 step 能真执行，git 历史干净（revert 保留历史，无 reset --hard 丢历史）。
  2. 重跑入口：`POST /api/sessions/{id}/rerun`（校验 + 状态机回退）→ WS `/act` execute 消费 `pending_rerun_from` 执行回退 + 重跑。
  3. 回退幂等：环境故障中断后重试，不重复 revert 已回退的 commit、不误伤用户后续修复。
  4. 重跑后 `metadata_json["last_run"]` 覆盖为新 run 摘要；`pending_rerun_from` 清除。
  5. 零新增依赖；Windows 三绿（pytest / ruff / mypy）；VM 真实 git CLI 跑通 revert + 重跑用例。

## 现状调研（2026-09-03）

| 依赖项 | 现状 | 结论 |
|---|---|---|
| checkpoint 契约（M3-20） | `GitCheckpoint.init`（基线空 commit `[prar:v{n}] init`）+ `commit_step`（每成功 step 一 commit，`--allow-empty`）；失败 step 不 commit；`StepExecution.git_commit` 40 位 hash | revert 的定位与回退基础齐备；基线保证首 step commit 有父（revert 可行） |
| last_run 数据源（M4-23） | ws_act acting 结束写 `metadata_json["last_run"] = {plan_version, all_ok, steps:[{step_id, ok, git_commit}]}`；`_parse_run_summary` 已建（session_service） | rerun 的 step 定位源；失败 step 在 steps 中且 git_commit=None |
| 状态机 | `ACTION_REVIEW → ACTING` 转移已合法（state_machine TRANSITIONS） | 回退无需改状态机；transition() 复用 |
| 执行面（ws_act） | execute 校验 phase=acting → `dispatcher.execute_plan`（全量执行所有 StepNode）→ 写 last_run → action_review | 需加 start_from 语义 + pending 消费；REST advance-to-acting → WS act 的两步模式可镜像 |
| dispatcher | `execute_plan` 无条件 `checkpoint.init`（幂等=追加新基线）+ 顺序执行全量 step | 需 start_from 参数 + 跳过 init 的判断（.git 已存在时不 init） |
| 沙箱生命周期 | `sandbox/runs/{session_id}` 会话级，不 cleanup；root 即 git repo | rerun 复用同一 repo，无需迁移 |
| 前端 | 无 action_review 相关 UI（grep 零命中） | 26 纯后端；UI 归 27 |
| 错误码惯例 | ws：session_not_found / illegal_phase / internal；REST：404/409 + code detail | 沿用 |

## 设计决策

### D1 触发面：REST rerun（状态回退）+ WS /act（执行回退）

`POST /api/sessions/{id}/rerun` body `{step_id: str}`：

1. 校验（见 D4 矩阵）→ 通过后 `transition(action_review → acting)`（复用 `SessionService`，新增 `request_rerun` 方法）。
2. `metadata_json["pending_rerun_from"] = step_id`，phase=acting，flush。

WS `/act` execute 首帧校验后：读 `session.metadata_json.get("pending_rerun_from")`。存在 → 执行回退（D2）+ `execute_plan(start_from=step_id)`；不存在 → 原有全量路径零改动。结束后 last_run 覆盖 + `pending_rerun_from` 删除。

理由：状态回退是 DB 变更放 REST（与 advance-to-acting / complete 对称）；执行是流式放 WS（复用 /act 事件管道）。与现有"REST 推进阶段 → WS 执行"两步模式一致，任务 27 UI 天然可用（点重跑 = 先 POST 再连 WS）。

### D2 回退语义：倒序 revert + 幂等（GitCheckpoint 扩展）

新增方法（core/checkpoint.py，零新依赖）：

```
rollback_to(plan_version, step_id) -> int:
  1. clean(): git reset --hard HEAD + git clean -fd
     （revert 要求干净工作区；同时清掉失败 step 的未提交残留）
  2. 收集待 revert：git log --format=%H 从 HEAD 向前，
     取 message 前缀 == f"[prar:v{plan_version}:" 的 step commit，
     直到命中 step_id == target 的 commit（含）为止 → list（时间正序）
  3. list 倒序逐条 git revert --no-edit <hash>；返回条数
```

关键语义：

- **失败 step（无 commit）为目标**：收集为空 → 只 clean，不回退任何 commit（其后的 step 因 fail-fast 未执行、无 commit）。这是最常见场景，恰好零 revert。
- **成功 step 为目标**：revert target..HEAD 的全部 step commit，工作区回到 target 前一个 step 完成态。
- **幂等**：若上次 revert 已成功（HEAD 的 step commit 已不含 target）→ 收集为空 → 直接重执行。环境故障中断重试不会二次 revert。
- **基线不 revert**：init 的 message 不以 `[prar:v{n}:` 开头，自然排除。
- **Revert 提交不含在收集范围**：revert commit 的 message 是 `Revert "[prar:v{n}:{step_id}] ..."`，前缀不匹配，不会被收集。
- revert 冲突/失败 → `ToolExecutionError` 上抛（环境故障停机红线），WS 走 internal。

### D3 执行面：dispatcher 支持 start_from

`ActionDispatcher.execute_plan(..., start_from: str | None = None)`：

- `start_from=None`：全量（18/20/21 调用方零改动）。
- 非 None：跳过 target 之前的 StepNode（不执行、不发事件、不建 workdir）；匹配不到 → `ValueError`（api 层已校验，此为防御边界）。
- `checkpoint.init` 仅在 `(sandbox.root / ".git").exists()` 为 False 时执行——rerun 复用既有 repo，不追加新基线。20 号 C2"重复 init 追加基线"契约不动（init 本身不改），判断放 dispatcher。

### D4 校验矩阵（POST rerun）

| 条件 | 状态码 | code |
|---|---|---|
| session 不存在 | 404 | session_not_found |
| phase ≠ action_review | 409 | illegal_phase_transition |
| last_run 缺失 | 409 | no_run |
| step_id 不在 plan 节点（或为空） | 404 | step_not_found |
| step_id 不在 last_run.steps | 404 | step_not_executed |

成功响应：`{phase: "acting", rerun_from: step_id}`。

### D5 metadata 生命周期与失败回退

- **写入**：REST rerun 校验通过后写 `pending_rerun_from` + 切 acting。
- **成功消费**：WS /act 执行完成（无论 all_ok）→ last_run 覆盖（23 号同款，all_ok 按新 records 计算）+ 删除 `pending_rerun_from` + 切 action_review。
- **失败回退（关键决策）**：rerun 执行中环境故障（ToolExecutionError 等）→ WS 异常分支把 phase 切回 action_review 并**保留** pending。重试路径：用户再点 rerun → REST 校验 action_review 通过 → WS 消费 pending → D2 幂等（HEAD 的 step commit 已不含 target 时收集为空，只 clean + 重执行）——任何中断点（revert 前 / revert 后 / 部分步骤已重跑）重试都安全。
- **首轮（非 rerun）执行失败**：维持 19 号既有行为不变（phase 停 acting + internal 错误），不在本任务范围。

### D6 边界与非目标

- 不做"改 plan 后重跑"编排（27 的评论 → merge → rerun 组合，26 只提供机制）。
- 不做"仅重跑单 step 不跑后续"（语义 = 从指定 step 到末尾；fail-fast 时天然只跑剩余）。
- 不做前端、不做沙箱 cleanup（会话级生命周期不变）。
- 不做 step_id 特殊字符防御（20 号 message 契约既有风险，26 沿用）。

### D7 成本与架构

- 零新增依赖（git CLI + 标准库）；零新增 LLM 调用（revert 纯 git；修正轮 LLM 仅工具失败时触发，与 18 一致）。
- api 层不直接调 git：api → SessionService（REST）/ dispatcher（WS，经 GitCheckpoint）→ git CLI，符合架构红线。

## 风险表

| 风险 | 应对 |
|---|---|
| revert 中间 commit 冲突（step 间文件交叉修改） | MVP 接受：每 step 独立 workdir + 顺序执行，交叉修改少见；冲突 → ToolExecutionError → internal，用户可见，不静默 |
| 多次 rerun 后 `git log --grep step_id` 不再唯一（20 号 C10 契约） | 26 的定位源改为 DB last_run.steps[].git_commit（hash 精确）；grep 仅调试用。设计变更记录追加到 20 号文档 |
| 环境故障中断 → 重试死锁（phase 卡 acting） | D5 决策：WS 失败分支 phase 回退 action_review + pending 保留；D2 幂等保证重试安全 |
| repo 被外部删除/损坏 | git 命令失败 → ToolExecutionError → internal，不静默（环境故障红线） |
| revert 后旧 commit 仍占历史（"干净"误解） | revert 是保留历史的回退——线性可审计即"干净"（ROADMAP 原文即 revert，非 reset） |

## 测试计划

### test_checkpoint.py 新增（真 git CLI，tmp_path，沿用 C1-C10 风格）

| # | 测试 | 断言 |
|---|---|---|
| C11 | rollback_to 成功 step | 收集数 = target..HEAD 条数；revert 后 target 创建的文件消失；返回条数正确 |
| C12 | rollback_to 失败 step（无 commit） | 返回 0；工作区残留被 clean（未提交文件消失） |
| C13 | rollback_to 幂等 | 连续两次调用：第二次返回 0 |
| C14 | revert 冲突 | 构造冲突（手动改文件再 commit 交叉）→ ToolExecutionError |

### test_action_dispatcher.py 新增

| # | 测试 | 断言 |
|---|---|---|
| T26 | start_from 跳过前置 step | 前置 step 不执行（无记录）、事件不发送；commit 数 = 执行 step 数 |
| T27 | .git 已存在时跳过 init | 预 init 后 execute_plan → 基线数不变 |
| T28 | start_from 匹配不到 | ValueError |
| T29 | start_from=None 回归 | 行为与 20 号一致（全量 + init） |

### test_sessions_rerun_api.py 新建（D4 矩阵 + 成功路径）

REST 校验矩阵 5 行 + 成功路径（phase=acting、pending_rerun_from 写入、响应体）。

### test_ws_act.py 新增

| # | 测试 | 断言 |
|---|---|---|
| W10 | pending 消费 | rollback_to 被调（patch 验证参数）+ execute_plan 收到 start_from + last_run 覆盖 + pending 删除 |
| W11 | 无 pending 回归 | rollback_to 不被调用（原路径零改动） |
| W12 | 环境故障回退 | dispatcher 抛 ToolExecutionError → phase 回 action_review、pending 保留 |

### VM 真实验证（一次性脚本，跑完删除）

真实 git CLI：init → 3 step 提交 → 模拟 step_002 失败重跑 → 断言 revert 序列、工作区状态、`git log` 线性含 Revert 提交、重跑后 last_run 一致、零残留（flush+rollback）。

---

## 实施记录（2026-09-04）

交付：`core/checkpoint.py` 新增模块级 `_step_id_of` + `GitCheckpoint.rollback_to`；`core/action_dispatcher.py` `execute_plan` 新增 `start_from`（跳过前置 step + `.git` 已存在则跳过 init）；`services/session_service.py` 新增 `request_rerun`（D4 校验矩阵）；`api/sessions.py` 新增 `POST /{session_id}/rerun`（`RerunRequest`/`RerunResponse`）；`api/ws_act.py` 消费 `pending_rerun_from` + D5 环境故障回退。零新增依赖、零新增 LLM 调用、api 层不直接调 git。

### 验收数据

- Windows：pytest 400 passed / 4 skipped / 1 deselected；ruff 零问题；mypy 55 文件零问题
- Linux VM（192.168.1.147，真实 git 2.43.0 + 真 Postgres）：5 个相关测试文件 73 passed（2.65s），含 12 个真 DB rerun 服务用例（flush+rollback，零残留）
- VM 一次性脚本（真实 git CLI + 真实沙箱，跑完已删）：
  - A：init → 3 step 提交 → `rollback_to(step_001)` 返回 2，`f0.txt` 保留 / `f1.txt`、`f2.txt` 消失，`git log` 线性 6 条（基线 + 3 step + 2 `Revert "..."`）；二次调用返回 0（幂等）；脏文件 `dirty.txt` 被 `clean -fd` 清掉、未知 step 返回 0
  - B：真实 dispatcher 首轮 3 step（4 commit）→ `start_from="step_001"` 重跑得 8 commit（2 revert + 2 新）、基线仍 1 条（init 被跳过）、3 个 `steps/step_00{i}/out.txt` 全部就位；`_NoLLMActor` 全程未被调用 → rerun 路径零 LLM 成本

### 行为发现

1. **幂等靠 `Revert "..."` 主题解析**：原 step commit 永远留在其 Revert 提交下方，单看 `--grep` 无法判断是否已回退。`rollback_to` 顺序扫 `git log --format=%H%x00%s`，把已解析出的 step_id 收进 `reverted` 集合，命中集合即跳过 → 二次调用收集为空、返回 0。
2. **定位源不用 `--grep`**：多次 rerun 后同一 step_id 对应多条 commit，`git log --grep <step_id>` 不再唯一（20 号原假设失效，已回写该文档）。26 号改为全量扫描 + `reverted` 抵消，DB `last_run.steps[].git_commit` 只作用户可见的审计留痕，不参与 revert 定位。
3. **宿主 gitconfig 必须隔离**：`tests/conftest.py` 新增 autouse fixture 注入 `commit.gpgsign=false`，否则宿主签名配置让 `git revert`/`commit` 卡在 pinentry，24 个 dispatcher/checkpoint 用例超时。
4. **D5 只覆盖 rerun 中断**：`ws_act` 的 try/except 仅在 `pending_rerun is not None` 时把 phase 拨回 `action_review` 并保留 pending（用户再点即重试）；首跑失败维持 19 号原行为。回退 commit 后 re-raise，外层仍照常推 `internal` 错误帧并关连接。
5. **`RerunRequest.step_id` 不加 `min_length`**：空串要走到服务层命中 D4 的 `step_not_found`（404），而不是被 pydantic 提前挡成 422。
