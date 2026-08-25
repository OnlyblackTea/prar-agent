import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { MergerResult, PlanDocument } from '@/types/shared'
import { MergeResultDrawer } from './MergeResultDrawer'

const prevPlan: PlanDocument = {
  title: 'T',
  summary: 'S',
  nodes: [
    { type: 'heading', level: 1, text: '概述' },
    { type: 'paragraph', text: '两周计划，每天安排约2-3小时学习时间' },
  ],
}

const newPlan: PlanDocument = {
  title: 'T',
  summary: 'S',
  nodes: [
    { type: 'heading', level: 1, text: '概述' },
    { type: 'paragraph', text: '两周计划，工作日每天安排约1小时学习时间' },
  ],
}

const result: MergerResult = {
  actions: [
    { comment_id: 'c1', decision: 'accept', reason: '合理诉求', patch: null },
    { comment_id: 'c2', decision: 'reject', reason: '超出范围', patch: null },
  ],
  overall_comment: '整体合理',
}

describe('MergeResultDrawer', () => {
  it('renders decision badges, reasons and diff for changed plan', () => {
    render(
      <MergeResultDrawer
        result={result}
        planChanged
        newVersion={2}
        prevPlan={prevPlan}
        newPlan={newPlan}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('Plan v2')).toBeDefined()
    expect(screen.getByText('合理诉求')).toBeDefined()
    expect(screen.getByText('超出范围')).toBeDefined()
    expect(screen.getByText('整体合理')).toBeDefined()
    // diff 展示修改行（改后文本进 ins）
    expect(screen.getByText('两周计划，工作日每天安排约1小时学习时间')).toBeDefined()
    expect(screen.queryByTestId('drawer-unchanged-banner')).toBeNull()
  })

  it('plan_changed=false shows "Plan unchanged" banner and no diff rows', () => {
    render(
      <MergeResultDrawer
        result={{ actions: [{ comment_id: 'c1', decision: 'reject', reason: '不改', patch: null }], overall_comment: '' }}
        planChanged={false}
        newVersion={1}
        prevPlan={prevPlan}
        newPlan={prevPlan}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('Plan unchanged')).toBeDefined()
    expect(screen.getByTestId('drawer-unchanged-banner')).toBeDefined()
    expect(screen.getByTestId('diff-empty')).toBeDefined()
  })

  it('close button and backdrop click both call onClose', () => {
    const onClose = vi.fn()
    const { container } = render(
      <MergeResultDrawer
        result={result}
        planChanged
        newVersion={2}
        prevPlan={prevPlan}
        newPlan={newPlan}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByLabelText('Close'))
    expect(onClose).toHaveBeenCalledTimes(1)
    // 点击遮罩关闭；点击抽屉本体不关
    fireEvent.click(screen.getByTestId('merge-drawer'))
    expect(onClose).toHaveBeenCalledTimes(1)
    const backdrop = container.querySelector('.drawer-backdrop')!
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
