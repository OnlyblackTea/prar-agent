import { NodeViewWrapper, ReactNodeViewRenderer, Node } from '@tiptap/react'

function GlossaryView({ node }: { node: { attrs: Record<string, unknown> } }) {
  const { term, definition } = node.attrs as { term: string; definition: string }
  return (
    <NodeViewWrapper
      as="span"
      className="glossary-term"
      title={definition}
      data-testid="glossary-node"
    >
      {term}
    </NodeViewWrapper>
  )
}

export const GlossaryNode = Node.create({
  name: 'glossary',
  group: 'inline',
  inline: true,
  atom: true,

  addAttributes() {
    return {
      id: { default: '' },
      term: { default: '' },
      definition: { default: '' },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-type="glossary"]' }]
  },

  renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
    return ['span', { ...HTMLAttributes, 'data-type': 'glossary' }]
  },

  addNodeView() {
    return ReactNodeViewRenderer(GlossaryView)
  },
})
