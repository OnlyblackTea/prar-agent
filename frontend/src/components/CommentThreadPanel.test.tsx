import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CommentResponse } from '@/types/shared'
import { CommentThreadPanel } from './CommentThreadPanel'

const comment: CommentResponse = {
  id: 'c1',
  session_id: 's1',
  plan_version: 1,
  anchor_id: 'a1',
  quote: '原文',
  quote_context: '原文上下文',
  body: '请修改',
  resolved: false,
  created_at: '2026-08-25T12:00:00Z',
}

const baseProps = {
  comments: [comment],
  pendingSelection: null,
  onCancel: () => {},
  onSubmit: async () => {},
  onJumpToAnchor: () => {},
}

describe('CommentThreadPanel readonly', () => {
  it('normal mode shows Apply button', () => {
    render(<CommentThreadPanel {...baseProps} onApplyReviews={() => {}} unresolvedCount={1} />)
    expect(screen.getByText('Apply Reviews (1)')).toBeDefined()
  })

  it('readonly hides Apply button but still lists comments', () => {
    render(
      <CommentThreadPanel
        {...baseProps}
        onApplyReviews={() => {}}
        unresolvedCount={1}
        readonly
      />,
    )
    expect(screen.queryByText(/Apply Reviews/)).toBeNull()
    expect(screen.getByText('请修改')).toBeDefined()
  })

  it('readonly hides new-comment form even with pendingSelection', () => {
    render(
      <CommentThreadPanel
        {...baseProps}
        pendingSelection={{ from: 0, to: 5, quote: '原文', quoteContext: '上下文' }}
        readonly
      />,
    )
    expect(screen.queryByPlaceholderText('Leave a comment...')).toBeNull()
  })
})

describe('CommentThreadPanel dangling', () => {
  it('dangling anchor shows hint text and comment-dangling class', () => {
    const { container } = render(
      <CommentThreadPanel {...baseProps} danglingIds={new Set(['a1'])} />,
    )
    expect(screen.getByText('⚠ 原文已变更，锚点无法定位')).toBeDefined()
    expect(container.querySelector('.comment-dangling')).not.toBeNull()
  })

  it('dangling comment click does not trigger onJumpToAnchor', () => {
    const onJumpToAnchor = vi.fn()
    const { container } = render(
      <CommentThreadPanel
        {...baseProps}
        onJumpToAnchor={onJumpToAnchor}
        danglingIds={new Set(['a1'])}
      />,
    )
    fireEvent.click(container.querySelector('.comment-dangling') as HTMLElement)
    expect(onJumpToAnchor).not.toHaveBeenCalled()
  })

  it('without danglingIds behaves as before (regression)', () => {
    const onJumpToAnchor = vi.fn()
    const { container } = render(
      <CommentThreadPanel {...baseProps} onJumpToAnchor={onJumpToAnchor} />,
    )
    expect(container.querySelector('.comment-dangling')).toBeNull()
    expect(screen.queryByText('⚠ 原文已变更，锚点无法定位')).toBeNull()
    const item = container.querySelector('.comment-list li') as HTMLElement
    fireEvent.click(item)
    expect(onJumpToAnchor).toHaveBeenCalledWith('a1')
  })

  it('dangling + resolved classes stack on same item', () => {
    const resolvedComment = { ...comment, resolved: true }
    const { container } = render(
      <CommentThreadPanel
        {...baseProps}
        comments={[resolvedComment]}
        danglingIds={new Set(['a1'])}
      />,
    )
    const item = container.querySelector('.comment-list li') as HTMLElement
    expect(item.className).toContain('comment-resolved')
    expect(item.className).toContain('comment-dangling')
  })
})
