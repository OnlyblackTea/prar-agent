import { useCallback, useEffect, useRef, useState } from 'react'
import {
  EditorContent,
  useEditor,
  type AnyExtension,
  type Editor,
} from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { DecisionNode } from './nodes/DecisionNode'
import { GlossaryNode } from './nodes/GlossaryNode'
import { StepNode } from './nodes/StepNode'
import { AnchorMark } from './marks/AnchorMark'
import type { JSONContent } from '@tiptap/react'

export interface SelectionSnapshot {
  from: number
  to: number
  quote: string
  quoteContext: string
}

interface PlanDocEditorProps {
  initialContent?: string
  doc?: JSONContent
  onRequestAddComment?: (sel: SelectionSnapshot) => void
  editorRef?: React.MutableRefObject<Editor | null>
}

export function PlanDocEditor({
  initialContent = '<p>Hello, PRAR-Agent.</p>',
  doc,
  onRequestAddComment,
  editorRef,
}: PlanDocEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [bubble, setBubble] = useState<{ top: number; left: number } | null>(null)

  const editor = useEditor({
    extensions: [
      StarterKit,
      DecisionNode,
      GlossaryNode,
      StepNode,
      AnchorMark,
    ] as AnyExtension[],
    content: doc ?? initialContent,
    editable: false,
  })

  // 回写 ref
  if (editorRef && editor) {
    editorRef.current = editor
  }

  const handleAddComment = useCallback(() => {
    if (!editor || !onRequestAddComment) return
    const { from, to } = editor.state.selection
    if (from === to) return
    const quote = editor.state.doc.textBetween(from, to, '\n')
    if (quote.length > 2000) return
    const ctxStart = Math.max(0, from - 50)
    const ctxEnd = Math.min(editor.state.doc.content.size, to + 50)
    const quoteContext = editor.state.doc.textBetween(ctxStart, ctxEnd, '\n')
    onRequestAddComment({ from, to, quote, quoteContext })
    setBubble(null)
  }, [editor, onRequestAddComment])

  // 自绘选区气泡：@tiptap BubbleMenu 插件 show() 里 this.element.remove()
  // 会把按钮 div 从 React 树摘下托管给 tippy，在本项目 editable: false 只读场景下
  // 导致按钮永不出现。改为监听 selectionUpdate 自算坐标、在容器内绝对定位。
  useEffect(() => {
    if (!editor) return
    const updateBubble = () => {
      const { from, to } = editor.state.selection
      if (from === to || !editor.view) {
        setBubble(null)
        return
      }
      let top = 0
      let left = 0
      try {
        const coords = editor.view.coordsAtPos(from)
        const rect = containerRef.current?.getBoundingClientRect()
        if (rect) {
          top = coords.top - rect.top - 40
          left = coords.left - rect.left
        }
      } catch {
        // jsdom 无 Range.getClientRects，退回容器原点（保证测试中按钮可见可点）
      }
      setBubble({ top, left })
    }
    editor.on('selectionUpdate', updateBubble)
    return () => {
      editor.off('selectionUpdate', updateBubble)
    }
  }, [editor])

  // useEditor 只在挂载时取一次 content：流式节点与 merge 后的新 plan 必须显式同步；
  // 依赖 doc 引用（App 每次 dispatch 都生成新对象），避免逐帧对比开销。
  useEffect(() => {
    if (editor && doc) {
      editor.commands.setContent(doc, false)
    }
  }, [editor, doc])

  // 钩子必须全部在早退之前调用：useEditor 首渲染返 null，若早退跳过钩子，
  // React 报 "Rendered fewer hooks" 并丢弃本次更新（BubbleMenu 永不渲染）。
  if (!editor) return null

  return (
    <div
      className="plan-doc-editor"
      data-testid="plan-doc-editor"
      ref={containerRef}
    >
      {onRequestAddComment && bubble && (
        <button
          className="bubble-add-comment"
          style={{ top: bubble.top, left: bubble.left }}
          onClick={handleAddComment}
        >
          Add Comment
        </button>
      )}
      <EditorContent editor={editor} />
    </div>
  )
}
