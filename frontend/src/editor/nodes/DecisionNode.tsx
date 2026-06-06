import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, Node } from '@tiptap/react'
import { useSession } from '../../state/SessionContext'

function DecisionView({ node }: { node: { attrs: Record<string, unknown> } }) {
  const { id, question, kind, options, answer, blocking } = node.attrs as {
    id: string
    question: string
    kind: string
    options: string[]
    answer: string | null
    blocking: boolean
  }
  const ctx = useSession()

  const handleSelect = async (choice: string) => {
    if (!ctx || !ctx.sessionId) return
    const res = await fetch(`/api/sessions/${ctx.sessionId}/decisions/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: choice }),
    })
    if (res.ok) {
      ctx.dispatch({ type: 'ANSWER_DECISION', id, answer: choice })
    }
  }

  return (
    <NodeViewWrapper className="decision-node" data-testid="decision-node">
      <div className="decision-question">
        {question}
        {blocking && <span className="decision-badge">必选</span>}
      </div>
      <div className="decision-options">
        {options.map((opt: string) => (
          <label key={opt} className="decision-option">
            <input
              type={kind === 'multi_choice' ? 'checkbox' : 'radio'}
              name={id}
              value={opt}
              checked={answer === opt}
              onChange={() => handleSelect(opt)}
              disabled={!!answer}
            />
            <span>{opt}</span>
          </label>
        ))}
      </div>
      <NodeViewContent />
    </NodeViewWrapper>
  )
}

export const DecisionNode = Node.create({
  name: 'decision',
  group: 'block',
  atom: true,

  addAttributes() {
    return {
      id: { default: '' },
      question: { default: '' },
      kind: { default: 'single_choice' },
      options: { default: [] },
      answer: { default: null },
      blocking: { default: true },
    }
  },

  parseHTML() {
    return [{ tag: 'div[data-type="decision"]' }]
  },

  renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
    return ['div', { ...HTMLAttributes, 'data-type': 'decision' }]
  },

  addNodeView() {
    return ReactNodeViewRenderer(DecisionView)
  },
})
