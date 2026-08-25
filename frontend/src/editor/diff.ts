/**
 * plan 版本节点级 diff（纯函数）。
 *
 * 节点 id 跨版本不稳定（后端 _assign_ids 按类型序号重排），不能按 id 对齐，
 * 用 LCS + bigram Jaccard 相似度（见 docs/design/13-plan-version-diff.md §3.3）。
 */
import type { PlanNode } from '@/types/shared'

export type DiffKind = 'unchanged' | 'added' | 'removed' | 'modified'

export interface DiffRow {
  kind: DiffKind
  oldNode?: PlanNode
  newNode?: PlanNode
}

/** 可匹配阈值：sim ≥ 0.5 视为同一节点（设计 §3.3） */
const MATCH_THRESHOLD = 0.5

/** 文本指纹：heading/paragraph 取 text；decision/step/glossary 取 attrs JSON（不含 id） */
function fingerprint(node: PlanNode): string {
  switch (node.type) {
    case 'heading':
      return `h${node.level}:${node.text}`
    case 'paragraph':
      return node.text
    case 'decision':
      return JSON.stringify({
        question: node.question,
        kind: node.kind,
        options: node.options,
        blocking: node.blocking,
      })
    case 'glossary':
      return JSON.stringify({ term: node.term, definition: node.definition })
    case 'step':
      return JSON.stringify({
        title: node.title,
        description: node.description,
        tool: node.tool,
        tool_args: node.tool_args,
        rerunnable: node.rerunnable,
      })
  }
}

function bigrams(s: string): Set<string> {
  const out = new Set<string>()
  for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2))
  return out
}

/** bigram Jaccard 相似度，0~1；完全相等短路返回 1 */
function similarity(a: string, b: string): number {
  if (a === b) return 1
  const ba = bigrams(a)
  const bb = bigrams(b)
  if (ba.size === 0 || bb.size === 0) return 0
  let inter = 0
  for (const g of ba) if (bb.has(g)) inter++
  return inter / (ba.size + bb.size - inter)
}

export function diffPlans(oldNodes: PlanNode[], newNodes: PlanNode[]): DiffRow[] {
  const m = oldNodes.length
  const n = newNodes.length
  if (m === 0 && n === 0) return []

  // sim 矩阵：类型不同记 0
  const sim: number[][] = Array.from({ length: m }, () => new Array<number>(n).fill(0))
  for (let i = 0; i < m; i++) {
    for (let j = 0; j < n; j++) {
      if (oldNodes[i].type === newNodes[j].type) {
        sim[i][j] = similarity(fingerprint(oldNodes[i]), fingerprint(newNodes[j]))
      }
    }
  }

  const matchable = (i: number, j: number): boolean => sim[i][j] >= MATCH_THRESHOLD

  // DP：dp[i][j] = 前 i 个旧节点与前 j 个新节点的最大累计相似度，优先匹配更相似的
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const viaMatch = matchable(i - 1, j - 1) ? dp[i - 1][j - 1] + sim[i - 1][j - 1] : -1
      dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1], viaMatch)
    }
  }

  // 回溯收集匹配对（逆序 → 反转）
  const matches: Array<[number, number]> = []
  let i = m
  let j = n
  while (i > 0 && j > 0) {
    if (matchable(i - 1, j - 1) && dp[i][j] === dp[i - 1][j - 1] + sim[i - 1][j - 1]) {
      matches.unshift([i - 1, j - 1])
      i--
      j--
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--
    } else {
      j--
    }
  }

  // 按序产出 DiffRow：匹配对之间的未匹配旧节点 removed、新节点 added
  const rows: DiffRow[] = []
  let oi = 0
  let ni = 0
  for (const [mo, mn] of matches) {
    while (oi < mo) rows.push({ kind: 'removed', oldNode: oldNodes[oi++] })
    while (ni < mn) rows.push({ kind: 'added', newNode: newNodes[ni++] })
    const kind: DiffKind = sim[mo][mn] === 1 ? 'unchanged' : 'modified'
    rows.push({ kind, oldNode: oldNodes[mo], newNode: newNodes[mn] })
    oi = mo + 1
    ni = mn + 1
  }
  while (oi < m) rows.push({ kind: 'removed', oldNode: oldNodes[oi++] })
  while (ni < n) rows.push({ kind: 'added', newNode: newNodes[ni++] })
  return rows
}
