import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
