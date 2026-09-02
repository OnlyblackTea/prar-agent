# 21. 前端 Action 输出面板（VSCode terminal/log 风格）

## 目标

- **一句话**：前端消费 19 号任务交付的 `/api/ws/sessions/{session_id}/act` 事件流，在「进入 Action 阶段」后以 VSCode terminal 风格面板实时展示 agent 的每一步工具执行——输出可视、可滚动、可复制，`plan.done` 后展示完成态（含每 step 的 git commit 记录）。

- **验收标准**（缺一不可）：

  1. `cd frontend && pnpm lint` / `pnpm typecheck` / `pnpm test` 全绿（现有 N + 本任务新增 ~20 用例）
  2. `pnpm build` 成功
  3. 浏览器 E2E 冒烟（Windows 本机 dev 全栈）：创建 session → plan 生成 → 进入 Action → 面板实时流式 stdout → 复制按钮可用 → `plan.done` 完成态（含 git commit hash）
  4. **后端零改动**：19 的 `/act` 端点与事件契约原样消费，不改一行后端代码
  5. 本任务纯前端，无 Linux 分支（VM 验收不适用，记录说明）

- **ROADMAP 偏差说明**：ROADMAP 21 原文「前端 Action 输出面板（VSCode terminal/log 风格）| 输出可视、可滚动、可复制」。执行完成后的「Action Review UI + 用户评论 → 触发 rerun」（ROADMAP 27）是 M4 任务，本任务**不做 rerun**（后端也无此端点）；`plan.done` 后保留完成态展示，重新开始走 Reset。

## 现状

| # | 现状 | 问题 |
| --- | --- | --- |
| P1 | `App.tsx:172-180` `handleAdvance` 是占位符：`alert('进入 Action 阶段（M3 实现）')` | 用户点击后无任何执行 UI |
| P2 | `sessionReducer` 只有 idle/connecting/streaming/review/error 五态 | 无 acting 态，WS 事件无处安放 |
| P3 | `api/ws.ts` 只有 `/plan` 的 `PlanStreamClient` | `/act` 无客户端 |
| P4 | 19 的后端端点已就绪（`/act` + `advance-to-acting`） | 前端零接入，整条 M3 链路断在浏览器 |

## 输入 / 输出

- **输入**：用户在 review 态点「进入 Action 阶段」→ `advanceToActing(sessionId)`（HTTP，已存在 `api/sessions.ts:44`）→ 连 `/act` WS → onopen 发 `{"type": "execute"}` → 事件流进 reducer
- **输出**：`ActionOutputPanel` 渲染 run（steps 列表 + 每 step 的 stdout 流 + 状态徽标 + git commit）；`plan.done` → 完成态；`error` → 面板内失败态
- **事件契约（19 已定，原样消费）**：

| 事件 | 载荷 | 处理 |
| --- | --- | --- |
| `step.start` | index / step_id / title / tool / tool_args | 追加 step（status=running） |
| `tool.stdout` | step_id / chunk | 按 step_id 追加 stdout 文本 |
| `tool.exit` | step_id / exit_code / ok | 记录 exit_code（终端风格展示） |
| `step.done` | step_id / ok / attempts / output / artifacts / thoughts / failure_reason / git_commit | 定稿 step：status=done/failed，补 attempts/failure/git_commit |
| `plan.done` | total_steps / all_ok | run 完成态 |
| `error` | code / message | acting 期间：面板内 failed（**不**切全屏 ErrorBanner） |

## 接口设计

### 1. 状态模型：`acting` 态扩展 sessionReducer（判别联合加一成员）

```ts
// state/sessionReducer.ts
export interface ActionStep {
  index: number
  stepId: string
  title: string
  tool: string
  toolArgs: Record<string, unknown>
  status: 'running' | 'done' | 'failed'
  stdout: string        // tool.stdout 流式拼接（真流式）
  output: string        // step.done.output（stdout 为空时的回退，fs 类工具无 stdout 事件）
  exitCode: number | null
  attempts: number
  artifacts: string[]   // 后端 Path 列表 JSON 序列化为 string[]
  thoughts: string[]
  failureReason: string | null
  gitCommit: string | null
}

export interface ActionRun {
  status: 'running' | 'done' | 'failed'
  allOk: boolean | null
  error: string | null
  steps: ActionStep[]
}

export type SessionState =
  | ... // 既有五态不动
  | { status: 'acting'; sessionId: string; planVersion: number; plan: PlanDocument; run: ActionRun }
```

新增 actions：

```ts
| { type: 'START_ACTING' }
| { type: 'WS_ACT_STEP_START'; index: number; step_id: string; title: string; tool: string; tool_args: Record<string, unknown> }
| { type: 'WS_ACT_TOOL_STDOUT'; step_id: string; chunk: string }
| { type: 'WS_ACT_TOOL_EXIT'; step_id: string; exit_code: number; ok: boolean }
| { type: 'WS_ACT_STEP_DONE'; step_id: string; ok: boolean; attempts: number; output: string; artifacts: string[]; thoughts: string[]; failure_reason: string | null; git_commit: string | null }
| { type: 'WS_ACT_PLAN_DONE'; total_steps: number; all_ok: boolean }
```

Reducer 规则：

- `START_ACTING`：仅 review 态受理 → `acting`，`run = { status:'running', allOk:null, error:null, steps:[] }`
- `WS_ACT_STEP_START`：追加 step；**step_id 重复时忽略**（防御，正常不会发生）
- `WS_ACT_TOOL_STDOUT` / `WS_ACT_TOOL_EXIT` / `WS_ACT_STEP_DONE`：按 step_id 定位更新；**未命中时静默忽略**（dispatcher 顺序保证 step.start 先行，忽略是防御）
- `WS_ACT_PLAN_DONE`：`run.status = 'done'`、`allOk = all_ok`（plan.done 已送达=执行走完，失败信息在各 step 的 failureReason 里，面板 summary 按 allOk 提示「完成」或「完成（部分步骤失败）」）
- `WS_ERROR`：**acting 态分支**→ `run.status='failed'`、`run.error = \`${code}: ${message}\``，状态保持 acting（plan 与已渲染输出不丢）；非 acting 走原全屏 error 逻辑
- 所有 `WS_ACT_*` 均加 `state.status !== 'acting'` 守卫（与既有 reducer 风格一致）

### 2. ActStreamClient + 事件映射（api/act.ts 新建）

照抄 `PlanStreamClient` 模式（ws.ts），差异点：

```ts
export type ActEvent =
  | { type: 'step.start'; index: number; step_id: string; title: string; tool: string; tool_args: Record<string, unknown> }
  | { type: 'tool.stdout'; step_id: string; chunk: string }
  | { type: 'tool.exit'; step_id: string; exit_code: number; ok: boolean }
  | { type: 'step.done'; step_id: string; ok: boolean; attempts: number; output: string; artifacts: string[]; thoughts: string[]; failure_reason: string | null; git_commit: string | null }
  | { type: 'plan.done'; total_steps: number; all_ok: boolean }
  | { type: 'error'; code: string; message: string }

export class ActStreamClient {
  connect(sessionId, onEvent: (e: ActEvent) => void, onClose, onOpen?)  // URL: /api/ws/sessions/{id}/act
  sendExecute(): void   // {"type":"execute"}
  close(): void
}

export function actEventToAction(event: ActEvent): SessionAction | null  // 纯函数映射，单测覆盖
```

- URL 拼接、onopen 后发送、malformed JSON try/catch 忽略——与 ws.ts 逐字同构
- **不重构 PlanStreamClient**（不动既有稳定代码；两个 client 各 ~30 行，抽象基类收益 < 风险）
- 映射函数放 api/act.ts 而非 App.tsx 内联：纯函数可脱离 React 单测（现有 `eventToAction` 在 App.tsx 是历史模式，此处选择可测性）

### 3. ActionOutputPanel 组件（components/ActionOutputPanel.tsx 新建）

```
┌ Action 执行输出 ────────────── [执行中…/完成/部分失败/失败] ─┐
│ ┌ #1 安装依赖  tool=shell [exit 0] [✓]      [复制] ┐       │
│ │ $ npm install                                     │       │
│ │ ... (stdout 流式, user-select, 自动滚动)           │       │
│ │ attempts=1  git: a1b2c3d4                        │       │
│ └───────────────────────────────────────────────────┘       │
│ ┌ #2 ...                                                    │
│ └───────────────────────────────────────────────────┘       │
│ [error 横幅（run.error 时）]  [summary：N steps / all ok]     │
└─────────────────────────────────────────────────────────────┘
```

- Props：`{ run: ActionRun }`（纯展示，dispatch 不走组件）
- 每 step 卡片：header（`#index` + title + tool/tool_args（复用 `.step-args` 终端绿样式）+ exit code + 状态徽标 + 复制按钮）+ `<pre class="step-log">`（显示 `stdout || output`）+ meta 行（attempts、git_commit 等宽字体 hash chip、failure_reason 红字）+ `<details>` 折叠 thoughts/artifacts
- 自动滚动：`scrollRef` + `useEffect`，依赖 `run.steps`（每次 dispatch 新数组必触发）；仅 `run.status === 'running'` 时 `scrollTop = scrollHeight`（完成后不抢用户滚动）
- 复制：`navigator.clipboard.writeText(step.stdout || step.output)`，成功按钮短暂变「已复制」；失败静默（http 非 localhost 时 API 不可用，MVP 降级为手动选择——`user-select: text` 保证文本可选中）
- header 状态：running=「执行中…」/ done+allOk=「完成」/ done+!allOk=「完成（部分步骤失败）」/ failed=「执行失败」

### 4. App.tsx 集成

```ts
const actClientRef = useRef<ActStreamClient | null>(null)

const handleAdvance = async () => {
  if (state.status !== 'review') return
  try {
    await advanceToActing(state.sessionId)
    dispatch({ type: 'START_ACTING' })
    const client = new ActStreamClient()
    actClientRef.current = client
    client.connect(
      state.sessionId,
      (event) => {
        const action = actEventToAction(event)
        if (action) dispatch(action)
      },
      () => { /* onclose：plan.done/error 已把终态写入 run；意外断开保留已渲染内容 */ },
      () => client.sendExecute(),  // onopen 后才可 send（与 /plan 同模式）
    )
  } catch (err) {
    dispatch({ type: 'WS_ERROR', code: 'advance_failed', message: err instanceof Error ? err.message : 'Unknown error' })
  }
}
```

- `handleReset` 增加 `actClientRef.current?.close(); actClientRef.current = null`（防 acting 中 Reset 泄漏连接）
- `isBusy` 增加 `state.status === 'acting'`（acting 期间禁新建 session，防孤儿执行）
- 渲染：`{state.status === 'acting' && <ActionOutputPanel run={state.run} />}`，置于 review-layout 之后（plan 视图保留在上方，评论面板与 ActionButton 因 `isReview === false` 自动隐藏——零额外改动）

### 5. 样式（App.css 追加节，~80 行）

- 全站样式集中在 App.css（merge-drawer 同文件，607 行起），新增 `.action-panel` 节
- 终端配色沿用既有先例：`.step-log` 背景 `#1e1e2e` / 前景 `#cdd6f4`（与 App.css:90-96 的 `pre` 一致）；`.step-args`（App.css:283）直接复用
- `.step-log { user-select: text; overflow-x: auto; white-space: pre-wrap; }`（可复制 + 长行不爆版）
- 状态徽标：running 绿脉冲 / done 绿 / failed 红，与站点既有配色协调

## 文件清单

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `frontend/src/state/sessionReducer.ts` | 改 | acting 态 + ActionStep/ActionRun + 8 个 action + WS_ERROR acting 分支 |
| `frontend/src/api/act.ts` | 新 | ActEvent 类型 + ActStreamClient + actEventToAction 映射 |
| `frontend/src/components/ActionOutputPanel.tsx` | 新 | 面板组件（step 卡片 / 自动滚动 / 复制） |
| `frontend/src/App.tsx` | 改 | handleAdvance 替换 alert 占位符；actClientRef；面板渲染；isBusy/Reset 扩展 |
| `frontend/src/App.css` | 改 | `.action-panel` 样式节 |
| `frontend/src/state/sessionReducer.test.ts` | 增 | acting 分支用例 ~8 |
| `frontend/src/api/act.test.ts` | 新 | ActStreamClient + 映射（**项目首个 WS mock 先例**，FakeWebSocket 类）~5 |
| `frontend/src/components/ActionOutputPanel.test.tsx` | 新 | 组件渲染/流式追加/复制/终态 ~6 |
| 后端 | 不动 | 19 契约原样消费 |

## 测试清单

| ID | 用例 | 断言要点 |
| --- | --- | --- |
| A1 | ActStreamClient 连接 URL | `ws://<host>/api/ws/sessions/{id}/act`（断言后缀） |
| A2 | onopen 后 sendExecute | sent 帧 == `{"type":"execute"}` |
| A3 | message → onEvent | FakeWebSocket push `step.start` JSON → onEvent 收到解析对象 |
| A4 | malformed JSON | push 非 JSON → 不抛、onEvent 不触发 |
| A5 | close() 幂等 | 两次 close 安全；二次 send 不抛 |
| A6 | actEventToAction 全事件映射 | 6 种事件 → 对应 action；未知 type → null |
| R1 | START_ACTING | review → acting，run 初始值正确（steps 空/status running/allOk null） |
| R2 | WS_ACT_STEP_START | 追加 step 且字段齐全；非 acting 态守卫（原状态引用不变） |
| R3 | WS_ACT_TOOL_STDOUT 拼接 | 同 step 多 chunk 顺序拼接；未知 step_id 静默忽略 |
| R4 | WS_ACT_TOOL_EXIT | exitCode 记录 |
| R5 | WS_ACT_STEP_DONE | status 按 ok 定稿；attempts/failureReason/gitCommit/output 落地；stdout 保留 |
| R6 | WS_ACT_PLAN_DONE | run.done + allOk 值 |
| R7 | WS_ERROR acting 分支 | run failed + error 文案；status 保持 acting（不切全屏 error） |
| C1 | 面板渲染 | step 标题/tool/log 文本/exit code 徽标可见 |
| C2 | stdout 回退 | stdout 空 → 显示 output（fs 类工具场景） |
| C3 | 失败 step | failure_reason 红字展示 |
| C4 | 完成态 | allOk summary + git_commit hash chip 展示；!allOk 提示「部分步骤失败」 |
| C5 | run.error | error 横幅渲染 |
| C6 | 复制按钮 | mock `navigator.clipboard.writeText` 收到日志文本 |

WebSocket mock 说明（项目首个先例）：`FakeWebSocket` 类记录 instances/url/sent，暴露 `onopen/onmessage/onclose/onerror` 供测试手动触发；`vi.stubGlobal('WebSocket', FakeWebSocket)` + `afterEach` 还原。jsdom 不实现 WebSocket，无需与其他测试隔离。

## 风险与未决

| ID | 风险 | 对策 |
| --- | --- | --- |
| R1 | `tool.stdout` 先于 `step.start` 到达（理论乱序） | dispatcher 顺序保证；reducer 未命中 step_id 时静默忽略，不崩 |
| R2 | stdout 事件高频（每行一帧）导致 reducer 每帧重渲 | 每帧一次 dispatch 是 React 常规负载；M3 沙箱单客户端，MVP 接受；如卡顿后续可做 chunk 合并（YAGNI） |
| R3 | jsdom 无真实滚动（scrollHeight=0） | 自动滚动逻辑人工 E2E 冒烟验证；单测只断言渲染不崩 |
| R4 | clipboard API 在非 localhost http 不可用 | 失败静默 + `user-select:text` 手动兜底；dev 走 localhost 全功能 |
| R5 | 执行完成后无 rerun 入口 | 后端 phase 已到 ACTION_REVIEW 且无返回端点；ROADMAP 27 补 rerun。MVP：面板保留终态展示，Reset 可重新开始 |
| R6 | App 层 handleAdvance 胶水无单测覆盖 | client/映射/reducer/组件四层全测 + 浏览器 E2E 冒烟覆盖胶水路径 |

### 已决策（默认值，主人不反对就这么走）

| ID | 决策点 | 决策 | 备选 |
| --- | --- | --- | --- |
| Q1 | 面板形态 | **底部全宽终端面板**（plan 视图保留在上方） | 替换式全屏（丢 plan 上下文） |
| Q2 | acting 状态建模 | **SessionState 判别联合加 'acting' 成员** | 旁路 useState（破坏单一 reducer 模式） |
| Q3 | WS 客户端 | **新文件独立 ActStreamClient**（PlanStreamClient 零改动） | 抽基类重构（动稳定代码） |
| Q4 | 日志展示来源 | **流式 stdout 优先，空则回退 step.done.output** | 只用 output（丢真流式）；只用 stdout（fs 工具无输出） |
| Q5 | acting 期间 WS_ERROR | **面板内 failed 态**（保留 plan 与已渲染输出） | 全屏 ErrorBanner（丢上下文） |
| Q6 | 复制实现 | **navigator.clipboard + 失败静默 + 文本可选中兜底** | execCommand 回退（已废弃 API） |
| Q7 | plan.done 后入口 | **无 rerun**（ROADMAP 27 补）；Reset 重新开始 | 前端假 rerun 按钮（后端不支持，会骗用户） |
| Q8 | 样式位置 | **App.css 追加节**（merge-drawer 同文件先例） | 独立 CSS 文件 |
| Q9 | tool.exit 入 state | **记 exitCode**（终端风格徽标，step.done 无 exit_code 字段） | 丢弃（丢失信息） |
| Q10 | 事件映射函数位置 | **api/act.ts 纯函数**（可单测） | App.tsx 内联（eventToAction 历史模式） |

如以上 10 项主人无异议，请直接回复 `APPROVED`，我立即按本设计落代码并 commit。

## 实施记录（2026-09-03）

交付：`frontend/src/state/sessionReducer.ts`（acting 判别联合成员 + `ActionStep`/`ActionRun` + 8 个 `WS_ACT_*` actions + `START_ACTING`）+ `frontend/src/api/act.ts`（`ActEvent` 判别联合 6 接口 + `actEventToAction` 纯函数 + `ActStreamClient`，照抄 `PlanStreamClient` 模式）+ `frontend/src/components/ActionOutputPanel.tsx`（StepCard/ActionOutputPanel，复制按钮 + 自动滚动）+ `App.tsx` 集成（`handleAdvance` 替换 alert、`actClientRef` 生命周期、派生状态加 acting）+ `App.css` `.action-panel` 节。后端零改动（消费 19 号已交付的 `/act` WS 端点）。零新增依赖。

### 验收数据

- 单测：`act.test.ts` 13 passed（FakeWebSocket 先例：`vi.stubGlobal('WebSocket', ...)`）+ `sessionReducer` 8 个 acting 用例 + `ActionOutputPanel.test.tsx` 9 passed（clipboard mock 成功/失败双路径）——共 101 tests / 10 files 全绿
- 三绿：lint 0 errors（App.tsx 3 个 exhaustive-deps warning 为既有，非本轮引入）；typecheck 通过；build 成功（160 modules）
- 浏览器 E2E 冒烟（真实 qwen3-max-e2e + VM PostgreSQL）：新建 session → plan 流式（7 节点 v1 落库）→ review 点击「进入 Action」→ advance-to-acting 200 → act WS 连接 → 面板流式渲染「执行中…」→ 完成态。真实走完 2 步：`fs.write`（✓、exit 0、stdout "wrote 24 bytes"、git chip `b98938d`）+ `shell`（✗、exit 2、failure_reason 完整 stderr、「actor gave up」）→ 汇总「共 2 步 · 部分失败」。步骤失败根因是 agent 生成相对路径命令（sandbox cwd 为 step 目录），属 plan 内容/后端提示词问题，非前端缺陷；且恰好真实验证了 C3/C4b 失败路径渲染

### 行为发现

1. **隐藏浏览器中的 clipboard 限制**：browser-use 无用户手势时 `navigator.clipboard.writeText` 静默拒绝，按钮保持「复制」不闪「已复制」——这正是设计的 C6b 静默兜底路径；成功路径由组件测试 C6 mock 验证，人工浏览器有手势时走成功分支。
2. **fake 点击限制**：in-app browser 视口不可见时 pointer 类操作（click/fill 后续）被拒，`evaluate_script` 的 DOM `click()` 仍可触发 React 事件，冒烟全程用脚本点击完成。
3. **onopen 时序纪律复验**：`ActStreamClient` 与 `PlanStreamClient` 同模式——`sendExecute` 必须在 onopen 回调内发（CONNECTING 时 send 抛错）；冒烟中 advance → connect → execute 链路一次走通。
4. **plan 视图保留**：acting 期间 plan 编辑器仍在上方（Q1 决策落地），冒烟确认双区共存渲染无干扰。
