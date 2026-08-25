import { Node as PMNode, Schema } from '@tiptap/pm/model'
import { describe, expect, it } from 'vitest'
import { ANCHOR_MATCH_THRESHOLD, findAnchorRange } from './anchorMatch'

// 最简 schema：findAnchorRange 只遍历 isText 节点，atom 节点不参与（设计 §3.2 item 1）
const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: { content: 'text*', group: 'block' },
    text: { group: 'inline' },
  },
})

/** 单段文档：paragraph text 节点全局 pos = 1 */
function docWithParagraph(text: string): PMNode {
  return PMNode.fromJSON(schema, {
    type: 'doc',
    content: [{ type: 'paragraph', content: text ? [{ type: 'text', text }] : [] }],
  })
}

/** 多段文档：每段一个 paragraph，text 节点 pos = 1 + 各段长度与节点开销累计 */
function docWithParagraphs(texts: string[]): PMNode {
  return PMNode.fromJSON(schema, {
    type: 'doc',
    content: texts.map((t) => ({
      type: 'paragraph',
      content: t ? [{ type: 'text', text: t }] : [],
    })),
  })
}

describe('findAnchorRange', () => {
  it('threshold constant is 0.7 per design', () => {
    expect(ANCHOR_MATCH_THRESHOLD).toBe(0.7)
  })

  it('case 1: exact quote hit → confidence 1.0 with precise range', () => {
    const doc = docWithParagraph('这是为期14天的学习计划')
    const match = findAnchorRange(doc, '为期14天', '')
    expect(match).not.toBeNull()
    expect(match).toMatchObject({ from: 3, to: 8, confidence: 1 })
  })

  it('case 1b: single-char quote exact hit still works via exact path', () => {
    const doc = docWithParagraph('这是为期14天的学习计划')
    const match = findAnchorRange(doc, '天', '')
    expect(match).not.toBeNull()
    expect(match!.confidence).toBe(1)
  })

  it('case 2: slight rewrite (1 char) → hit with confidence ≥ threshold', () => {
    const doc = docWithParagraph('为期14天，每天安排约2小时学习时间')
    const match = findAnchorRange(doc, '为期14天，每天安排约3小时学习时间', '')
    expect(match).not.toBeNull()
    expect(match!.confidence).toBeGreaterThanOrEqual(ANCHOR_MATCH_THRESHOLD)
    expect(match).toMatchObject({ from: 1, to: 19, confidence: 0.75 })
  })

  it('case 3: mid-quote phrase insertion absorbed by window length range', () => {
    const doc = docWithParagraph('从CSV等常见格式的原始数据到报告')
    const match = findAnchorRange(doc, '从原始数据到报告', '')
    expect(match).not.toBeNull()
    expect(match!.confidence).toBeGreaterThanOrEqual(ANCHOR_MATCH_THRESHOLD)
    // 最佳窗口 "原始数据到报告"（7 chars，start=10）：dice 12/13 高于 8-char 窗口的 12/14
    expect(match).toMatchObject({ from: 11, to: 18, confidence: 0.785 })
  })

  it('case 4: zero-overlap replacement → null', () => {
    const doc = docWithParagraph('wxyz0123456789wxyz0123')
    const match = findAnchorRange(doc, 'abcdefghijkl', '')
    expect(match).toBeNull()
  })

  it('case 5: empty quote / empty doc → null', () => {
    expect(findAnchorRange(docWithParagraph('内容'), '', '')).toBeNull()
    expect(findAnchorRange(docWithParagraph(''), 'hello', '')).toBeNull()
  })

  it('case 6: single-char quote without exact hit → null (no fuzzy)', () => {
    const doc = docWithParagraph('abcdef')
    expect(findAnchorRange(doc, 'X', '')).toBeNull()
  })

  it('case 7: quote longer than any text block → null (no window)', () => {
    const doc = docWithParagraph('short')
    expect(findAnchorRange(doc, 'abcdefghijklmnopqrst', '')).toBeNull()
  })

  it('case 8: similar texts + context boost picks the quoteContext-matching one', () => {
    const doc = docWithParagraphs([
      '第一阶段先完成数据清洗和模型训练。',
      '前期准备完成后，先安排数据清洗和模型训练。随后开展评估。',
    ])
    // quote 精确不存在（与→和 改写）；quoteContext 前后缀与第二段窗口邻域高重合
    const quote = '先安排数据清洗与模型训练'
    const quoteContext = '前期准备完成后，' + quote + '。随后开展评估'
    const match = findAnchorRange(doc, quote, quoteContext)
    expect(match).not.toBeNull()
    // 第二段 text 节点 pos = 20（para1 paragraph size 19）；窗口起点 index 8、winLen 12
    expect(match).toMatchObject({ from: 28, to: 40, confidence: 0.84 })
  })

  it('case 9: word-order swap (anagram trap) scores below threshold → null', () => {
    const doc = docWithParagraph('可视化与数据清洗')
    // multiset Dice 会假想 1.0；bigram 保留相邻顺序 → 显著低于阈值
    expect(findAnchorRange(doc, '数据清洗与可视化', '')).toBeNull()
  })

  it('case 10: tied final scores → earliest document position wins', () => {
    // 70 个填充字符把窗口推到 index 70：覆盖语境邻域 Math.max(0, start-60) 分支
    const pad = 'A'.repeat(70)
    const doc = docWithParagraphs([
      pad + '第一阶段先完成数据清洗及模型训练。',
      pad + '第二阶段先完成数据清洗及模型训练。',
    ])
    // quoteContext = quote 本身（无前后缀）→ 语境加成为 0，两处 final 并列
    const quote = '第一阶段先完成数据清洗和模型训练'
    const match = findAnchorRange(doc, quote, quote)
    expect(match).not.toBeNull()
    expect(match!.from).toBe(71)
    expect(match!.confidence).toBeGreaterThanOrEqual(ANCHOR_MATCH_THRESHOLD)
  })

  it('case 12a: prefix-only context with 1-char prefix → empty-bigram side scores 0', () => {
    // prefix = "。"（1 字符，无 bigram）→ ctx 0；覆盖 prefix 单侧与 ba.size===0 分支
    const doc = docWithParagraph('完成了。先安排数据清洗和模型训练。')
    const quote = '先安排数据清洗与模型训练。'
    const match = findAnchorRange(doc, quote, '。' + quote)
    expect(match).toMatchObject({ from: 5, to: 18, confidence: 0.708 })
  })

  it('case 12b: suffix-only context with empty right neighborhood → ctx 0', () => {
    // 窗口在段落结尾：right 邻域为空 → bb.size===0；覆盖 suffix 单侧分支
    const doc = docWithParagraph('先安排数据清洗和模型训练。')
    const quote = '先安排数据清洗与模型训练。'
    const match = findAnchorRange(doc, quote, quote + '。随后')
    expect(match).toMatchObject({ from: 1, to: 14, confidence: 0.708 })
  })

  it('case 11a: final just below threshold (≈0.695) → null', () => {
    const doc = docWithParagraph('abcXdefghijk')
    // quote 12 chars：窗口 12 chars 改 1 字 → dice 18/22 → final ≈ 0.695 < 0.7
    expect(findAnchorRange(doc, 'abcdefghijkl', '')).toBeNull()
  })

  it('case 11b: final just above threshold (≈0.739) → hit', () => {
    const doc = docWithParagraph('abcXdefghijkl')
    const match = findAnchorRange(doc, 'abcdefghijkl', '')
    expect(match).not.toBeNull()
    expect(match).toMatchObject({ from: 1, to: 14, confidence: 0.739 })
  })
})
