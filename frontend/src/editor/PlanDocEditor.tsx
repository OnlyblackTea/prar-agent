import { EditorContent, useEditor, type AnyExtension } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { DecisionNode } from './nodes/DecisionNode'
import { GlossaryNode } from './nodes/GlossaryNode'
import { StepNode } from './nodes/StepNode'
import type { JSONContent } from '@tiptap/react'

interface PlanDocEditorProps {
  initialContent?: string
  doc?: JSONContent
}

export function PlanDocEditor({
  initialContent = '<p>Hello, PRAR-Agent.</p>',
  doc,
}: PlanDocEditorProps) {
  const editor = useEditor({
    extensions: [StarterKit, DecisionNode, GlossaryNode, StepNode] as AnyExtension[],
    content: doc ?? initialContent,
    editable: false,
  })

  if (!editor) return null

  return (
    <div className="plan-doc-editor" data-testid="plan-doc-editor">
      <EditorContent editor={editor} />
    </div>
  )
}
