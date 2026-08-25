import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { PlanNode } from '@/types/shared'
import type { DiffRow } from '@/editor/diff'
import { PlanDiffView } from './PlanDiffView'

const p = (text: string): PlanNode => ({ type: 'paragraph', text })
const h = (text: string): PlanNode => ({ type: 'heading', level: 1, text })

describe('PlanDiffView', () => {
  it('renders four kinds with distinct style classes', () => {
    const rows: DiffRow[] = [
      { kind: 'unchanged', oldNode: h('概述'), newNode: h('概述') },
      { kind: 'added', newNode: p('新段落') },
      { kind: 'removed', oldNode: p('旧段落') },
      { kind: 'modified', oldNode: p('改前文本'), newNode: p('改后文本') },
    ]
    const { container } = render(<PlanDiffView rows={rows} />)
    expect(screen.getByTestId('plan-diff')).toBeDefined()
    expect(container.querySelector('.diff-unchanged')).not.toBeNull()
    expect(container.querySelector('.diff-added')).not.toBeNull()
    expect(container.querySelector('.diff-removed')).not.toBeNull()
    expect(container.querySelector('.diff-modified')).not.toBeNull()
    expect(screen.getByText('新段落')).toBeDefined()
    expect(screen.getByText('旧段落')).toBeDefined()
  })

  it('modified row shows old text in del and new text in ins', () => {
    const rows: DiffRow[] = [
      { kind: 'modified', oldNode: p('改前文本'), newNode: p('改后文本') },
    ]
    const { container } = render(<PlanDiffView rows={rows} />)
    expect(container.querySelector('del')?.textContent).toBe('改前文本')
    expect(container.querySelector('ins')?.textContent).toBe('改后文本')
  })

  it('renders step summary with title and description', () => {
    const rows: DiffRow[] = [
      {
        kind: 'added',
        newNode: {
          type: 'step',
          id: 'step_001',
          title: '建目录',
          description: '创建项目根目录',
          tool: 'shell',
          tool_args: {},
          rerunnable: true,
        },
      },
    ]
    render(<PlanDiffView rows={rows} />)
    expect(screen.getByText('[步骤] 建目录 — 创建项目根目录')).toBeDefined()
  })

  it('empty rows → No changes placeholder', () => {
    render(<PlanDiffView rows={[]} />)
    expect(screen.getByTestId('diff-empty')).toBeDefined()
  })
})
