import { Mark, mergeAttributes, type Editor } from '@tiptap/react'

export interface AnchorAttrs {
  anchor_id: string
  resolved: boolean
}

export const AnchorMark = Mark.create<AnchorAttrs>({
  name: 'anchor',
  inclusive: false,

  addAttributes() {
    return {
      anchor_id: { default: '' },
      resolved: { default: false },
    }
  },

  parseHTML() {
    return [{ tag: 'mark[data-anchor-id]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'mark',
      mergeAttributes(HTMLAttributes, {
        class: 'prar-anchor',
        'data-anchor-id': HTMLAttributes.anchor_id,
        'data-resolved': String(HTMLAttributes.resolved),
      }),
      0,
    ]
  },
})

/**
 * 底层 dispatch 绕过 Tiptap editable 检查，直接给 doc 打 Mark。
 */
export function applyAnchorMark(
  editor: Editor,
  from: number,
  to: number,
  attrs: AnchorAttrs,
): void {
  const tr = editor.state.tr.addMark(
    from,
    to,
    editor.state.schema.marks.anchor.create(attrs as unknown as Record<string, unknown>),
  )
  editor.view.dispatch(tr)
}
