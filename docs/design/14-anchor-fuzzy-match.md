# 14. 评论锚定算法：fuzzy 回源 + 悬空评论 UI

> **状态**：APPROVED（2026-08-26）
> **依赖**：Task 11（AnchorMark + comments 表 + `findRangeByQuote` 简化版）、Task 12（merge 落 v{N+1}，v1 锚点全消）、Task 13（版本浏览 + reducer 零改动前提）
> **被依赖**：无（M2 收尾任务）
> **commit 范围**：单设计文档，拆 2 个 commit（14a 算法 / 14b UI 装配），设计文档随 14a 同入

> **实施澄清（2026-08-26，14a 编码期）**：§3.2 item 4 原措辞「若窗口是全文最佳，检查…」存在歧义（与 §6 case 8「多处相似文本 + 语境加成 → 选中 quoteContext 吻合的那处」矛盾：若只对并列取首后的唯一最佳窗口加成，语境无法区分并列候选）。消歧：**对所有候选窗口计算 `final = score·0.85 + ctx·0.15`，按 final 排名，final 并列取文档坐标最小**。已决议的算法、权重、阈值均不变，仅澄清排名机制。

---

## 1. 目标

把评论回放从「quote 严格 `indexOf`」升级为 **fuzzy 回源**：merge 改了文本后评论锚点仍能找回位置；找不到（置信度 < 0.7）时评论进入**悬空（dangling）UI 状态**，明示用户重新指认，而不是静默丢失。

### 验收标准

1. 评论的 quote 在新文档中**原样存在** → 回放成功，行为与现状一致（置信度 1.0）
2. quote 被**轻微改写**（改几个字、增删修饰语）→ 仍能回放锚点，位置对齐改写后的文本段
3. quote 被**彻底删除或重写**（置信度 < 0.7）→ 评论在侧边栏显示「悬空」标记 + 提示文案；编辑器内不落 Mark；点击该评论**不触发跳转**
4. 置信度计算纯函数（`anchorMatch.ts`）单测覆盖率 100%（WORKFLOW §6 对锚定算法的硬性要求）
5. 回归：评论创建（同版本即时落 Mark，不经过回放路径）、历史版本只读浏览、抽屉流程不受影响

---

## 2. 输入 / 输出

- 上游产物：
  - `comments` 表的 `quote` / `quote_context`（Task 11；context = quote ± 50 字符，含 quote 本身，`textBetween(from-50, to+50)`）
  - App.tsx `findRangeByQuote`（Task 11 简化版，本任务替换）
  - Task 13 建立的约束：reducer 零改动、历史版本浏览不打 anchor mark
- 交付物：
  - `frontend/src/editor/anchorMatch.ts`：fuzzy 回源纯函数（含相似度与阈值）
  - App.tsx 回放逻辑替换 + 悬空集合状态
  - `CommentThreadPanel` 悬空 UI（徽标 + 文案 + 禁跳转）
  - 相应单测（算法 100% 分支覆盖）与组件测试

**不做**（超出 M2 范围）：
- 后端锚定（`_quote_in_plan` sanity check 保持精确，职责只是防脏数据进库，不是回源）
- 「重新指认」交互本身（选新文本重绑旧评论）——本任务只**提示**需要重新指认；ROADMAP 的"提示重新指认"字面兑现到此为止，交互闭环留 post-MVP
- 跨版本回源（v1 评论回放到 v2 文档）——Task 13 已用「版本浏览」清偿 §5.5 债务；跨版本回源属 post-MVP
- 编辑器内悬空占位样式（评论已不在文档里，无位置可标）

## 3. 接口设计

### 3.1 核心纯函数 `anchorMatch.ts`

```ts
import type { Node as ProseMirrorNode } from '@tiptap/pm/model'

export interface AnchorMatch {
  from: number          // ProseMirror 文档坐标
  to: number
  confidence: number    // 0..1，1 = 精确命中
}

/** 回放置信度阈值（ROADMAP：命中率 < 0.7 判悬空） */
export const ANCHOR_MATCH_THRESHOLD = 0.7

/**
 * 在文档中为 (quote, quoteContext) 找最佳落点。
 * 策略分三级：
 *  1. 精确命中：quote 是某段文本的子串 → confidence 1.0（保持现状语义）
 *  2. fuzzy：滑窗扫描文本段，字符 bigram Dice 重合度最高的窗口 → confidence = 得分
 *  3. 语境加成：候选窗口与 quoteContext 前后缀匹配时小幅加分（见 §3.2）
 * 所有候选得分 < THRESHOLD → 返回 null（调用方标悬空）。
 */
export function findAnchorRange(
  doc: ProseMirrorNode,
  quote: string,
  quoteContext: string,
): AnchorMatch | null
```

### 3.2 相似度算法

1. **文本段采集**：`doc.descendants` 收集所有 `isText` 节点（连同其全局 pos），与现状一致——atom 节点（decision/step/glossary）的内容不在 ProseMirror 文本树里，本任务**不扩展**到 attrs 匹配（11 §5.9 的跨节点缺陷仅针对文本内引文修复，见 §7 风险）。
2. **窗口**：对每段文本，窗口长在 `[⌊0.8·L⌋, ⌈1.5·L⌉]`（L = quote 长度）范围内枚举，步进 2 字符。merger 常在中途插入短语（如"从原始数据"→"从CSV等常见格式的原始数据"），定长窗口会把插入的尾部挤出得分窗口；长度范围吸收插入/删节（plan 文档量级 < 10K 字符，总计算量可忽略）。
3. **字符 bigram Dice 重合度**（字符级 2-gram：中文无词边界时保序的最小粒度）：

   ```
   score(a, b) = 2 * |common_bigrams(a, b)| / (|bigrams(a)| + |bigrams(b)|)
   ```

   - 选 bigram 而非 multiset：multiset 完全无序，"数据清洗与可视化" vs "可视化与数据清洗" 会得 1.0（anagram 盲区）；bigram 保留相邻顺序，同例约 0.71，更符合"改写程度"语义
   - 选 bigram 而非 LCS/Levenshtein：后两者 O(w²) 且面向整串对齐，用于滑窗扫描收益为零；bigram O(w)，分支少（100% 覆盖成本低），并与 13 的 diff 已用 bigram 的工程记忆一致
   - 边界：L < 2（单字符）且无精确命中 → 直接 null，不进 fuzzy
4. **语境加成**：对每个候选窗口，检查 `quoteContext` 去掉 quote 后的前缀/后缀（各 ≤50 字）在窗口邻域（窗口前/后 60 字符内）的字符 bigram Dice 重合度 `ctx_score`（非空侧平均）：

   ```
   final = score * 0.85 + ctx_score * 0.15
   ```

   按 `final` 排名、`final` 并列取文档坐标最小（见页首实施澄清）。作用：quote 本身被大幅改写但周围语境未变时，仍能把锚点钉在正确段落；同时防止不同位置出现相似文本时选错。
5. **并列取首**：多个窗口得分相同 → 取文档坐标最小的（与现状 `indexOf` 首次出现的语义一致）。

### 3.3 App 装配（替换 `findRangeByQuote`）

```tsx
// App.tsx
const [dangling, setDangling] = useState<Set<string>>(new Set()) // anchor_id 集合

// 回放 effect（现 118-141 行）改为：
const nextDangling = new Set<string>()
for (const c of state.comments) {
  if (existingAnchors.has(c.anchor_id)) continue
  const match = findAnchorRange(editor.state.doc, c.quote, c.quote_context)
  if (match) {
    applyAnchorMark(editor, match.from, match.to, {
      anchor_id: c.anchor_id, resolved: c.resolved,
    })
  } else {
    nextDangling.add(c.anchor_id)
  }
}
setDangling(nextDangling)
```

- `findRangeByQuote` 函数本体删除（唯一调用方就是此 effect）。
- 新增评论的即时路径（`handleSubmitComment`）**不走回放**：选区坐标直接落 Mark，不引入延迟与误差。
- 历史版本浏览分支（`viewingVersion !== null` 早退）保持不变。

### 3.4 悬空评论 UI（`CommentThreadPanel`）

```tsx
interface CommentThreadPanelProps {
  // ...existing
  /** 回放失败（置信度 < 0.7）的评论，按 anchor_id 标识 */
  danglingIds?: ReadonlySet<string>
}
```

- 列表项 `className` 追加 `comment-dangling`；blockquote 下追加一行提示：
  `⚠ 原文已变更，锚点无法定位`
- `onClick`：悬空评论**不调** `onJumpToAnchor`（无锚点可跳）
- 悬空与 `comment-resolved` 样式可叠加（opacity 不再二次叠加，以悬空样式为准）
- `readonly`（历史版本浏览）时：`danglingIds` 传空集（历史文档与评论同版本，精确匹配必然命中，无悬空概念）

## 4. 文件清单

| 文件 | 动作 | 作用 |
|------|------|------|
| `frontend/src/editor/anchorMatch.ts` | 新增 | `findAnchorRange` + `ANCHOR_MATCH_THRESHOLD` 纯函数 |
| `frontend/src/editor/anchorMatch.test.ts` | 新增 | 算法单测（100% 分支覆盖，WORKFLOW §6） |
| `frontend/src/App.tsx` | 修改 | 删 `findRangeByQuote`；回放 effect 换 fuzzy + dangling 状态；面板传 `danglingIds` |
| `frontend/src/components/CommentThreadPanel.tsx` | 修改 | `danglingIds` prop + 悬空样式类 + 提示文案 + 禁跳转 |
| `frontend/src/components/CommentThreadPanel.test.tsx` | 修改 | 追加悬空态用例 |
| `frontend/src/App.css` | 修改 | `.comment-dangling` 样式（虚线边框/琥珀色徽标） |

后端零改动；`shared/schema.json` 不动。

## 5. 实施步骤

1. `anchorMatch.test.ts` 先写（红）：覆盖 §6 全部 case
2. `anchorMatch.ts` 实现（绿）；自查覆盖率 100%（`vitest run --coverage` 或人工核对分支）
3. `CommentThreadPanel` 加 `danglingIds` prop + 测试
4. App.tsx 装配：替换回放逻辑、删 `findRangeByQuote`、传 `danglingIds`
5. `.comment-dangling` 样式
6. 前端全套回归（vitest + tsc + eslint）
7. commit 14a：设计文档 + `anchorMatch.ts` + 算法测试
8. commit 14b：面板/App 装配 + 组件测试 + 样式
9. E2E 冒烟（浏览器手工，不计 commit）：生成 → 2 条评论 → merge（一条改写引用文本、一条删掉引用文本）→ 验证一条回放、一条悬空

## 6. 测试清单

**`anchorMatch.test.ts`（算法，须 100% 覆盖）**

> 实施注记（14a）：case 1 拆 1/1b（多字符/单字符精确命中），case 11 拆 a/b（阈值两侧），追加 12a/12b 覆盖语境加成的单侧与空邻域分支；case 10 以 70 字符填充覆盖 `Math.max(0, start-60)` 分支。共 16 个 `it`。

| # | case | 期望 |
|---|------|------|
| 1 | quote 精确存在 | confidence 1.0，from/to 精确 |
| 2 | quote 轻微改写（改 1-2 字） | 命中，confidence ≥ 0.7，范围 = 改写后文本段 |
| 3 | quote 中途被插入短语（窗口长范围生效） | 命中，confidence ≥ 0.7 |
| 4 | quote 被彻底替换（零重合） | 返回 null |
| 5 | quote 空串 / 文档空 | 返回 null |
| 6 | 单字符 quote 无精确命中 | 返回 null（不进 fuzzy） |
| 7 | quote 比任何文本段都长 | 返回 null（窗口不存在） |
| 8 | 多处相似文本 + 语境加成 | 选中 quoteContext 吻合的那处 |
| 9 | 语序对调（anagram 反例） | bigram 得分显著低于 multiset 假想的 1.0（写死期望值） |
| 10 | 并列得分 | 取文档坐标最小者 |
| 11 | 阈值边界：得分 0.69 → null；0.70 → 命中 | 阈值语义精确 |

**`CommentThreadPanel.test.tsx`（追加）**

| # | case | 期望 |
|---|------|------|
| 12 | `danglingIds` 含某评论 | 显示提示文案 + `comment-dangling` 类 |
| 13 | 悬空评论点击 | `onJumpToAnchor` 不被调用 |
| 14 | 不传 `danglingIds`（默认） | 行为与现状一致（回归） |

**边缘情况**：
- 跨节点 quote（11 已知缺陷）：本任务不修，`findAnchorRange` 仍按单文本段匹配，跨段引文照旧悬空——**但会正确进入悬空态而非静默消失**，这即是本任务的兜底价值
- 全角/半角、空白差异：multiset 按字符计，不做归一化（M2 范围外，记入风险）
- 悬空集合在评论新增/清空后自动重算（effect 依赖不变）

**集成入口**：`cd frontend && pnpm test && pnpm typecheck && pnpm lint`

## 7. 风险与未决

| # | 项 | 说明 / 建议 |
|---|----|------------|
| 1 | 字符 bigram 对"远处互换"仍有残余盲区 | 相邻字符保序，但相隔较远的两块对调仍感知不到。语境加成可部分对冲；plan 评审场景改写以局部为主，接受此误差 |
| 2 | 阈值 0.7 是经验值 | 写死常量 `ANCHOR_MATCH_THRESHOLD`，E2E 若发现误判再走设计变更调参 |
| 3 | atom 节点内容（decision question / step desc）不参与回放 | 与 11 现状一致；merger 主要改 paragraph，接受 |
| 4 | 「重新指认」交互未闭环 | 本任务只提示；交互留 post-MVP（§2 已声明） |

**已决议（2026-08-26，主人表态"按推荐"）**：

1. **相似度算法**：✅ 字符 bigram Dice + 窗口长范围 `[⌊0.8L⌋, ⌈1.5L⌉]` + 语境加成（§3.2 现行版本）。
2. **悬空评论提示文案**：✅ 「⚠ 原文已变更，锚点无法定位」。
3. **commit 拆分**：✅ 14a 算法 / 14b 装配两个（§5 步骤 7/8）。

---

## 8. M2 收尾说明

本任务完成后 ROADMAP M2 验收全达成：「完整跑一轮 Plan v1 → 留 3 条评论 → 触发 Review Merger → Plan v2 真的改变（12）、diff 视图可见（13）、评论位置不丢（14）」。
