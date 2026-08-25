import type { Node as ProseMirrorNode } from '@tiptap/pm/model'

export interface AnchorMatch {
  /** ProseMirror 文档坐标 */
  from: number
  to: number
  /** 0..1，1 = 精确命中 */
  confidence: number
}

/** 回放置信度阈值（ROADMAP：命中率 < 0.7 判悬空） */
export const ANCHOR_MATCH_THRESHOLD = 0.7

/** 语境加成权重（设计 §3.2）：final = score * 0.85 + ctx_score * 0.15 */
const SCORE_WEIGHT = 0.85
const CTX_WEIGHT = 0.15

/** 滑窗步进（字符）——性能折中，残余窗口单独补足 */
const WINDOW_STEP = 2

/** 窗口长度范围：quote 长度 L 的 [⌊0.8L⌋, ⌈1.5L⌉] */
const WIN_MIN_RATIO = 0.8
const WIN_MAX_RATIO = 1.5

/** 语境邻域半径：窗口前/后各 60 字符 */
const CTX_NEIGHBOR = 60

/** 浮点噪声归一到 3 位小数（阈值 0.7 比较需确定性） */
const round3 = (x: number): number => Math.round(x * 1000) / 1000

function bigrams(s: string): Set<string> {
  const out = new Set<string>()
  for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2))
  return out
}

/** 字符 bigram Dice 系数，0~1；任一侧无 bigram 记 0 */
function bigramDice(a: string, b: string): number {
  const ba = bigrams(a)
  const bb = bigrams(b)
  if (ba.size === 0 || bb.size === 0) return 0
  let inter = 0
  for (const g of ba) if (bb.has(g)) inter++
  return (2 * inter) / (ba.size + bb.size)
}

/** 语境重合度：prefix/suffix（非空侧）与窗口邻域的 bigram Dice 平均 */
function contextScore(
  text: string,
  start: number,
  winLen: number,
  prefix: string,
  suffix: string,
): number {
  const left = text.slice(Math.max(0, start - CTX_NEIGHBOR), start)
  const right = text.slice(start + winLen, start + winLen + CTX_NEIGHBOR)
  let sum = 0
  let count = 0
  if (prefix.length > 0) {
    sum += bigramDice(prefix, left)
    count++
  }
  if (suffix.length > 0) {
    sum += bigramDice(suffix, right)
    count++
  }
  return count > 0 ? round3(sum / count) : 0
}

/** 收集全部文本段（连同全局 pos）；atom 节点内容不在 ProseMirror 文本树中（设计 §3.2 item 1） */
function collectTextSegments(doc: ProseMirrorNode): Array<{ text: string; pos: number }> {
  const segments: Array<{ text: string; pos: number }> = []
  doc.descendants((node, pos) => {
    if (node.isText && node.text) segments.push({ text: node.text, pos })
  })
  return segments
}

/**
 * 在文档中为 (quote, quoteContext) 找最佳落点。
 * 策略分三级：
 *  1. 精确命中：quote 是某段文本的子串 → confidence 1.0（保持现状语义）
 *  2. fuzzy：滑窗扫描文本段，字符 bigram Dice 重合度最高的窗口 → confidence = 得分
 *  3. 语境加成：候选窗口与 quoteContext 前后缀匹配时小幅加分（见设计 §3.2）
 * 所有候选 final < THRESHOLD → 返回 null（调用方标悬空）。
 */
export function findAnchorRange(
  doc: ProseMirrorNode,
  quote: string,
  quoteContext: string,
): AnchorMatch | null {
  if (!quote) return null

  const segments = collectTextSegments(doc)

  // 1. 精确命中（与 Task 11 findRangeByQuote 语义一致：首个出现）
  for (const seg of segments) {
    const idx = seg.text.indexOf(quote)
    if (idx >= 0) {
      return { from: seg.pos + idx, to: seg.pos + idx + quote.length, confidence: 1 }
    }
  }

  // 2. fuzzy（单字符无精确命中 → 无 bigram 可匹配，直接悬空）
  if (quote.length < 2) return null

  // quoteContext 中剥离 quote：前缀/后缀各 ≤50 字；异常数据（不含 quote）则无加成
  const qIdx = quoteContext.indexOf(quote)
  const prefix = qIdx >= 0 ? quoteContext.slice(0, qIdx) : ''
  const suffix = qIdx >= 0 ? quoteContext.slice(qIdx + quote.length) : ''

  const minLen = Math.floor(quote.length * WIN_MIN_RATIO)
  const maxLen = Math.ceil(quote.length * WIN_MAX_RATIO)
  let best: { from: number; to: number; final: number } | null = null

  const consider = (
    text: string,
    pos: number,
    start: number,
    winLen: number,
  ): { from: number; to: number; final: number } | null => {
    const score = round3(bigramDice(quote, text.slice(start, start + winLen)))
    if (score === 0) return null
    const ctx = contextScore(text, start, winLen, prefix, suffix)
    const final = round3(score * SCORE_WEIGHT + ctx * CTX_WEIGHT)
    return { from: pos + start, to: pos + start + winLen, final }
  }

  // 纯函数比较：final 高者胜；final 并列取文档坐标最小（设计 §3.2 item 5）
  const pickBetter = (
    current: { from: number; to: number; final: number } | null,
    candidate: { from: number; to: number; final: number } | null,
  ): { from: number; to: number; final: number } | null => {
    if (!candidate) return current
    if (!current) return candidate
    if (candidate.final > current.final) return candidate
    if (candidate.final === current.final && candidate.from < current.from) return candidate
    return current
  }

  for (const seg of segments) {
    for (let winLen = minLen; winLen <= maxLen; winLen++) {
      if (winLen > seg.text.length) continue
      const last = seg.text.length - winLen
      for (let start = 0; start <= last; start += WINDOW_STEP) {
        best = pickBetter(best, consider(seg.text, seg.pos, start, winLen))
      }
      // 步进可能跳过残余窗口（起点为奇数偏移），补足保证正确性
      if (last % WINDOW_STEP !== 0) {
        best = pickBetter(best, consider(seg.text, seg.pos, last, winLen))
      }
    }
  }

  if (!best || best.final < ANCHOR_MATCH_THRESHOLD) return null
  return { from: best.from, to: best.to, confidence: best.final }
}
