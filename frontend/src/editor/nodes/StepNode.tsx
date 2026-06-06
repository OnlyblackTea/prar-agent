import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, Node } from '@tiptap/react'

function StepView({ node }: { node: { attrs: Record<string, unknown> } }) {
  const { title, description, tool, tool_args, rerunnable } = node.attrs as {
    id: string
    title: string
    description: string
    tool: string
    tool_args: Record<string, unknown>
    rerunnable: boolean
  }
  return (
    <NodeViewWrapper className="step-node" data-testid="step-node">
      <header className="step-header">
        <strong>{title}</strong>
        <code className="step-tool">{tool}</code>
        {rerunnable && <span className="step-badge">可重跑</span>}
      </header>
      <p className="step-description">{description}</p>
      <pre className="step-args">{JSON.stringify(tool_args, null, 2)}</pre>
      <NodeViewContent />
    </NodeViewWrapper>
  )
}

export const StepNode = Node.create({
  name: 'step',
  group: 'block',
  atom: true,

  addAttributes() {
    return {
      id: { default: '' },
      title: { default: '' },
      description: { default: '' },
      tool: { default: '' },
      tool_args: { default: {} },
      rerunnable: { default: true },
    }
  },

  parseHTML() {
    return [{ tag: 'div[data-type="step"]' }]
  },

  renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
    return ['div', { ...HTMLAttributes, 'data-type': 'step' }]
  },

  addNodeView() {
    return ReactNodeViewRenderer(StepView)
  },
})
