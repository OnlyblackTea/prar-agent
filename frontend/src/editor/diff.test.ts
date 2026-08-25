import { describe, expect, it } from 'vitest'
import type { PlanNode } from '@/types/shared'
import { diffPlans } from './diff'

const p = (text: string): PlanNode => ({ type: 'paragraph', text })
const h = (text: string): PlanNode => ({ type: 'heading', level: 1, text })
const step = (id: string, title: string): PlanNode => ({
  type: 'step',
  id,
  title,
  description: 'd',
  tool: 'shell',
  tool_args: {},
  rerunnable: true,
})

describe('diffPlans', () => {
  it('identical nodes → all unchanged', () => {
    const rows = diffPlans([h('A'), p('B')], [h('A'), p('B')])
    expect(rows.map((r) => r.kind)).toEqual(['unchanged', 'unchanged'])
  })

  it('pure append → added at end', () => {
    const rows = diffPlans([p('A')], [p('A'), p('B')])
    expect(rows.map((r) => r.kind)).toEqual(['unchanged', 'added'])
    expect(rows[1].newNode).toEqual(p('B'))
  })

  it('pure removal → removed', () => {
    const rows = diffPlans([p('A'), p('B')], [p('A')])
    expect(rows.map((r) => r.kind)).toEqual(['unchanged', 'removed'])
    expect(rows[1].oldNode).toEqual(p('B'))
  })

  it('insertion in middle', () => {
    const rows = diffPlans([p('A'), p('C')], [p('A'), p('B'), p('C')])
    expect(rows.map((r) => r.kind)).toEqual(['unchanged', 'added', 'unchanged'])
  })

  it('single node text tweak → modified', () => {
    const rows = diffPlans(
      [p('本计划为期两周，每天安排约2-3小时学习时间')],
      [p('本计划为期两周，工作日每天安排约1小时学习时间')],
    )
    expect(rows.map((r) => r.kind)).toEqual(['modified'])
    expect(rows[0].oldNode).toBeDefined()
    expect(rows[0].newNode).toBeDefined()
  })

  it('realistic merge: tweak paragraph + remove one step', () => {
    const oldNodes = [
      h('概述'),
      p('两周计划，每天安排约2-3小时学习时间'),
      step('step_001', '建目录'),
      step('step_002', '装环境'),
    ]
    const newNodes = [
      h('概述'),
      p('两周计划，工作日每天安排约1小时学习时间'),
      step('step_001', '装环境'),
    ]
    const rows = diffPlans(oldNodes, newNodes)
    expect(rows.map((r) => r.kind)).toEqual(['unchanged', 'modified', 'removed', 'unchanged'])
    expect((rows[2].oldNode as PlanNode & { type: 'step' }).title).toBe('建目录')
  })

  it('complete rewrite below threshold → removed + added (设计 §7 已声明风险)', () => {
    const rows = diffPlans([p('原文内容')], [p('彻底重写的全新表述')])
    expect(rows.map((r) => r.kind)).toEqual(['removed', 'added'])
  })

  it('empty old → all added', () => {
    const rows = diffPlans([], [p('A')])
    expect(rows.map((r) => r.kind)).toEqual(['added'])
  })

  it('empty new → all removed', () => {
    const rows = diffPlans([p('A')], [])
    expect(rows.map((r) => r.kind)).toEqual(['removed'])
  })

  it('both empty → empty rows', () => {
    expect(diffPlans([], [])).toEqual([])
  })

  it('same text but different type → not matched', () => {
    const rows = diffPlans([p('同样的文本内容比较长一点')], [h('同样的文本内容比较长一点')])
    expect(rows.map((r) => r.kind).sort()).toEqual(['added', 'removed'])
  })

  it('id drift after deletion: remaining steps stay unchanged', () => {
    // _assign_ids 跨版本重排：删 step_001 后 step_002 变 step_001，
    // 指纹不含 id，故未变节点仍判 unchanged 而非 modified
    const oldNodes = [step('step_001', '建目录'), step('step_002', '装环境')]
    const newNodes = [step('step_001', '装环境')]
    const rows = diffPlans(oldNodes, newNodes)
    expect(rows.map((r) => r.kind)).toEqual(['removed', 'unchanged'])
  })
})
