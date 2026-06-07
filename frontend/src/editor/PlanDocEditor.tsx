import { useCallback } from 'react'
import {
  BubbleMenu,
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
  }, [editor, onRequestAddComment])

  if (!editor) return null

  return (
    <div className="plan-doc-editor" data-testid="plan-doc-editor">
      {onRequestAddComment && (
        <BubbleMenu editor={editor}>
          <button
            className="bubble-add-comment"
            onClick={handleAddComment}
          >
            Add Comment
          </button>
        </BubbleMenu>
      )}
      <EditorContent editor={editor} />
    </div>
  )
}
