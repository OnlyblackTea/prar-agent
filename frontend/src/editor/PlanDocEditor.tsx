import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'

interface PlanDocEditorProps {
  initialContent?: string
}

export function PlanDocEditor({
  initialContent = '<p>Hello, PRAR-Agent.</p>',
}: PlanDocEditorProps) {
  const editor = useEditor({
    extensions: [StarterKit],
    content: initialContent,
    editable: false,
  })

  if (!editor) return null

  return (
    <div className="plan-doc-editor" data-testid="plan-doc-editor">
      <EditorContent editor={editor} />
    </div>
  )
}
