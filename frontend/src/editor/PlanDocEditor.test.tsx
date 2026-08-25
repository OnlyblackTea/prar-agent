import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Editor } from '@tiptap/react'
import { describe, expect, it, vi } from 'vitest'
import { PlanDocEditor } from './PlanDocEditor'

describe('PlanDocEditor', () => {
  it('renders initial content', () => {
    render(<PlanDocEditor initialContent="<p>Test content</p>" />)
    expect(screen.getByText('Test content')).toBeInTheDocument()
  })

  it('renders default text when no prop', () => {
    render(<PlanDocEditor />)
    expect(screen.getByText('Hello, PRAR-Agent.')).toBeInTheDocument()
  })

  // 回归：选区非空时自绘气泡必须出现并可点击发出快照（替代 @tiptap BubbleMenu，
  // 其插件 show() 会 this.element.remove() 导致按钮永不渲染，E2E 实测踩坑）
  it('shows Add Comment bubble on non-empty selection and emits snapshot on click', async () => {
    const editorRef = { current: null } as React.MutableRefObject<Editor | null>
    const onRequestAddComment = vi.fn()
    render(
      <PlanDocEditor
        initialContent="<p>anchor text here</p>"
        onRequestAddComment={onRequestAddComment}
        editorRef={editorRef}
      />,
    )
    await waitFor(() => expect(editorRef.current).not.toBeNull())
    act(() => {
      editorRef.current?.commands.setTextSelection({ from: 1, to: 17 })
    })
    const btn = await waitFor(() => {
      const el = document.querySelector('.bubble-add-comment')
      expect(el).toBeInTheDocument()
      return el as HTMLButtonElement
    })
    fireEvent.click(btn)
    expect(onRequestAddComment).toHaveBeenCalledWith(
      expect.objectContaining({ from: 1, to: 17, quote: 'anchor text here' }),
    )
  })
})
