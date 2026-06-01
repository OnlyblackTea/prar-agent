# 09. 前端项目骨架 + Task 08 Hotfix

> **状态**：DRAFT，待 APPROVED
> **依赖**：Task 06（shared/schema.json）、Task 08（WebSocket 协议）
> **被依赖**：Task 10（Plan 文档渲染 + Decision 答题闭环）
> **commit 范围**：拆 2 个 commit — `09a` 前端骨架（大）/ `08fix` 后端 AdapterNotFoundError 修复（极小）

---

## 1. 目标

- **一句话**：搭建 `frontend/` 项目骨架（Vite + React + TS + Tiptap），跑通 `pnpm dev`，能在浏览器渲染一个纯 Tiptap 文档；同时修掉 Task 08 留下的 `AdapterNotFoundError` 重定义问题。
- **验收标准**：
  1. `cd frontend && pnpm install && pnpm dev` 启动 dev server（默认 5173）
  2. 浏览器打开 `http://localhost:5173` 显示一个 Tiptap editor，含一段示例文本
  3. `pnpm build` 成功（dist/ 产物）
  4. `pnpm lint` / `pnpm typecheck` 零警告
  5. `pnpm test` 至少 2 个测试用例（render snapshot + 配置 sanity）
  6. **Task 08 hotfix**：`backend/src/app/api/ws_plan.py` 不再重复定义 `AdapterNotFoundError`，从 `services.adapter_service` 导入；backend 全部测试仍 100% 绿

---

## 2. 现状

| # | 现状 | 问题 |
|---|------|------|
| P1 | `frontend/` 目录不存在 | Task 10 / 11 / 13 没有挂靠点 |
| P2 | `app/api/ws_plan.py` 本地定义 `AdapterNotFoundError` | Task 10 接通 DB session 后会与 `services.adapter_service.AdapterNotFoundError` 异类，`except` 子句漏接 |

---

## 3. 技术选型（前端）

| 维度 | 选择 | 理由 |
|------|------|------|
| 构建工具 | **Vite 5** | DESIGN.md §3 已定；HMR 快；与 Tauri 兼容 |
| 框架 | **React 18 + TS** | DESIGN.md §3 已定 |
| 富文本 | **Tiptap 2** | DESIGN.md §3 已定；自定义节点能力足够支撑 Decision / Glossary / Step |
| 包管理 | **pnpm** | CLAUDE.md 全局规范：`pnpm > npm > yarn` |
| 状态管理 | 不引入额外库 | M1 阶段只需要 useState/useReducer；M2+ 评估 Zustand |
| 测试 | **Vitest + @testing-library/react** | Vite 原生；与 React 19 兼容；快 |
| Lint/Format | **ESLint 9 (flat config) + Prettier** | 现代标准 |
| 类型同步 | 消费 `shared/schema.json`（Task 06） | M1 不接 `json-schema-to-typescript`（推后到 Task 10 真用得到时） |

---

## 4. 目录结构

```
frontend/
├── public/
├── src/
│   ├── main.tsx                # React 入口 + StrictMode
│   ├── App.tsx                 # 顶层组件（M1 = Tiptap demo）
│   ├── App.css
│   ├── index.css
│   ├── editor/
│   │   ├── PlanDocEditor.tsx   # Tiptap 编辑器封装（M1 = StarterKit only）
│   │   └── PlanDocEditor.test.tsx
│   ├── api/
│   │   └── ws.ts               # WebSocket 客户端工具（M1 = 仅类型定义和占位）
│   └── types/
│       └── shared.d.ts         # 手写最小 TS 类型，匹配 shared/schema.json 关键字段
├── .vscode/
│   └── settings.json           # 推荐用 prettier 作 default formatter
├── index.html
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── eslint.config.js
├── .prettierrc
├── .gitignore
└── README.md
```

---

## 5. 关键文件设计

### 5.1 `package.json`

```json
{
  "name": "prar-agent-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "@tiptap/react": "^2.10.0",
    "@tiptap/starter-kit": "^2.10.0",
    "@tiptap/pm": "^2.10.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "vite": "^5.4.0",
    "typescript": "^5.6.0",
    "vitest": "^2.1.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.5.0",
    "jsdom": "^25.0.0",
    "eslint": "^9.13.0",
    "@eslint/js": "^9.13.0",
    "typescript-eslint": "^8.10.0",
    "eslint-plugin-react-hooks": "^5.0.0",
    "eslint-plugin-react-refresh": "^0.4.0",
    "prettier": "^3.3.0"
  }
}
```

> ⚠️ 决策点 Q1：React 18 还是 React 19？
> A=React 18（推荐，生态稳定，Tiptap 2.10 官方支持）/ B=React 19（更新但 Tiptap 兼容性需 nightly）

### 5.2 `tsconfig.json` — strict TS

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "jsx": "react-jsx",
    "skipLibCheck": true,
    "isolatedModules": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`tsconfig.node.json` 单独处理 `vite.config.ts`（避免主项目 `noEmit` 限制）。

### 5.3 `vite.config.ts`

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Task 10 真接 backend 时取消注释
      // '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
```

### 5.4 `src/editor/PlanDocEditor.tsx`

```typescript
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'

interface PlanDocEditorProps {
  initialContent?: string
}

export function PlanDocEditor({
  initialContent = '<p>Hello, PRAR-Agent.</p>',
}: PlanDocEditorProps) {
  const editor = useEditor({
    extensions: [StarterKit],
    content: initialContent,
    editable: false, // M1 只渲染；编辑能力 Task 10+
  })

  if (!editor) return null

  return (
    <div className="plan-doc-editor">
      <EditorContent editor={editor} />
    </div>
  )
}
```

### 5.5 `src/App.tsx`

```typescript
import { PlanDocEditor } from './editor/PlanDocEditor'
import './App.css'

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>PRAR Agent</h1>
        <p className="subtitle">Plan / Review / Action / Review</p>
      </header>
      <main>
        <PlanDocEditor />
      </main>
    </div>
  )
}
```

### 5.6 `src/types/shared.d.ts` — 手写最小类型

M1 阶段不接 `json-schema-to-typescript` 工具链（增加构建复杂度但 M1 demo 用不到），手写最小集合：

```typescript
// 与 backend Pydantic 模型字段对齐，Task 10 真消费后再考虑自动生成
export type PlanNode =
  | { type: 'heading'; level: 1 | 2 | 3; text: string }
  | { type: 'paragraph'; text: string }
  | {
      type: 'decision'
      id: string
      question: string
      kind: 'single_choice' | 'multi_choice'
      options: string[]
      answer: string | null
      blocking: boolean
    }
  | { type: 'glossary'; id: string; term: string; definition: string }
  | {
      type: 'step'
      id: string
      title: string
      description: string
      tool: string
      tool_args: Record<string, unknown>
      rerunnable: boolean
    }

export interface PlanDocument {
  title: string
  summary: string
  nodes: PlanNode[]
}
```

> ⚠️ 决策点 Q2：M1 类型管理方式
> A=手写最小集合（推荐，简单可控）/ B=接 json-schema-to-typescript 自动生成 / C=完全不写，运行时 unknown

### 5.7 `src/api/ws.ts` — WS 客户端占位

```typescript
// Task 10 真接通时填实现
export interface PlanStartEvent { type: 'plan.start'; session_id: string; title: string; summary: string }
export interface PlanNodeEvent { type: 'plan.node'; index: number; node: import('@/types/shared').PlanNode }
export interface PlanDoneEvent { type: 'plan.done'; total_nodes: number }
export interface ErrorEvent { type: 'error'; code: string; message: string }

export type WSEvent = PlanStartEvent | PlanNodeEvent | PlanDoneEvent | ErrorEvent
```

仅类型导出，无实际连接逻辑（Task 10 落地）。

---

## 6. Task 08 Hotfix

### 6.1 改动

**`backend/src/app/api/ws_plan.py`**：

```diff
- class AdapterNotFoundError(Exception):
-     """adapter_id 在 DB 中找不到。Task 10+ 移到 services 层。"""
+ from app.services.adapter_service import AdapterNotFoundError
```

**`backend/tests/test_ws_plan.py`** T3 测试：

```diff
- from app.api.ws_plan import AdapterNotFoundError
+ from app.services.adapter_service import AdapterNotFoundError
```

### 6.2 提交策略

> ⚠️ 决策点 Q3：08 hotfix 与 09 是否分 commit
> A=分两个 commit（推荐，工作流纪律：一个 commit 一个 Refs）— `fix(ws): 用 services 层的 AdapterNotFoundError (M1-08-fixup)` + `feat(frontend): 项目骨架 (M1-09)`
> B=合一个 commit（违反 WORKFLOW.md §3.3 跨任务禁律）

---

## 7. 文件清单

### 7.1 Task 09 新增（前端）

| 路径 | 说明 |
|------|------|
| `frontend/package.json` | 依赖 + scripts |
| `frontend/pnpm-lock.yaml` | 锁版本 |
| `frontend/tsconfig.json` | strict TS |
| `frontend/tsconfig.node.json` | vite.config.ts 用 |
| `frontend/vite.config.ts` | Vite + React + 路径别名 |
| `frontend/eslint.config.js` | flat config |
| `frontend/.prettierrc` | format 规则 |
| `frontend/.gitignore` | node_modules / dist / .vite |
| `frontend/index.html` | HTML 入口 |
| `frontend/src/main.tsx` | React root |
| `frontend/src/App.tsx` | 顶层组件 |
| `frontend/src/App.css` | 顶层样式 |
| `frontend/src/index.css` | 全局样式 |
| `frontend/src/editor/PlanDocEditor.tsx` | Tiptap 封装 |
| `frontend/src/editor/PlanDocEditor.test.tsx` | render snapshot |
| `frontend/src/api/ws.ts` | WS 事件类型占位 |
| `frontend/src/types/shared.d.ts` | 手写最小类型 |
| `frontend/src/test/setup.ts` | testing-library setup |
| `frontend/README.md` | 启动 3 行 |

### 7.2 Task 08 Hotfix 改造

| 路径 | 说明 |
|------|------|
| `backend/src/app/api/ws_plan.py` | 删本地 `AdapterNotFoundError`，改从 `services.adapter_service` 导入 |
| `backend/tests/test_ws_plan.py` | 同步改 import |

---

## 8. 实施步骤

### Commit 1: `fix(ws)` — 08-fixup（先做，独立）

| # | 步骤 | 验证 |
|---|------|------|
| 1 | `ws_plan.py` 删本地类 + import services 那个 | import 不报错 |
| 2 | `test_ws_plan.py` 改 import 路径 | `make test` 全 126 测试绿 |
| 3 | `make lint` | 0 error |
| 4 | git commit 单独提交 | message 含 `Refs: docs/design/09-frontend-skeleton.md §6` |

### Commit 2: `feat(frontend)` — 09

| # | 步骤 | 验证 |
|---|------|------|
| 1 | `mkdir frontend` + `pnpm init` 写 package.json | 文件存在 |
| 2 | 写 tsconfig / vite.config / eslint / prettier 配置 | `pnpm install` 成功 |
| 3 | 写 `src/main.tsx` + `App.tsx` + `PlanDocEditor.tsx` + 类型占位 | `pnpm typecheck` 通过 |
| 4 | 写 `.test.tsx` + `test/setup.ts` | `pnpm test` 全绿 |
| 5 | `pnpm dev` 浏览器肉眼看到 Tiptap 文档 | UI 渲染正确 |
| 6 | `pnpm build` | dist/ 产物存在 |
| 7 | `pnpm lint` | 0 error |
| 8 | git commit | message 含 `Refs: docs/design/09-frontend-skeleton.md` |

---

## 9. 测试清单

### 9.1 Task 09 前端测试（Vitest）

| # | 文件 | 测试 | 断言 |
|---|------|------|------|
| T1 | `PlanDocEditor.test.tsx` | `renders_initial_content` | 给定 initialContent，DOM 包含该文本 |
| T2 | `PlanDocEditor.test.tsx` | `renders_default_text_when_no_prop` | 不传 prop 时显示默认 "Hello, PRAR-Agent." |

### 9.2 Task 08 Hotfix 回归

- backend 全部 126 测试仍绿（T3 走 services 那个类）

---

## 10. 设计决策记录

| 决策 | 理由 |
|------|------|
| React 18 而非 19 | Tiptap 2.10 / @testing-library 等生态对 18 支持稳定；19 是新版风险大 |
| Tiptap editable=false | M1 阶段只渲染，Task 10 才接入答题/锚定/编辑能力 |
| 不引状态管理库 | M1 无跨组件共享状态；M2+ 评估 Zustand |
| 手写 TS 类型而非 codegen | M1 类型字段少；codegen 工具链增加 setup 复杂度，Task 10 真用到再上 |
| frontend / backend 完全隔离 | 与 CLAUDE.md "跨包禁止"对齐；通过 HTTP/WS 通信 |
| flat eslint config | ESLint 9 默认；与 typescript-eslint 8 配套；废弃 .eslintrc |
| 08 hotfix 与 09 分 commit | WORKFLOW.md §3.3 硬约束：一 commit 一任务 |

---

## 11. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| pnpm 在主人当前机器未装 | 中 | 低 | README 提示 `npm i -g pnpm` |
| Tiptap 与 React 18 SSR 警告 | 低 | 低 | StrictMode 双调用不影响功能 |
| jsdom 版本与 Vitest 不兼容 | 低 | 低 | 使用 jsdom 25.x（Vitest 2.1 官方推荐） |
| `pnpm dev` 端口 5173 被占 | 中 | 低 | Vite 自动顺延端口；不影响测试 |

---

## 12. 决策题汇总

| # | 题目 | 选项 | 推荐 |
|---|------|------|------|
| Q1 | React 版本 | A=18 / B=19 | **A** |
| Q2 | 共享类型管理 | A=手写最小 / B=json-schema-to-typescript / C=运行时 unknown | **B** |
| Q3 | 08 hotfix 与 09 是否合 commit | A=分两个 commit / B=合一个 | **A** |

---

主人审阅后回 `APPROVED`（或修改意见 + Q1-Q3 选择）即开始编码。
