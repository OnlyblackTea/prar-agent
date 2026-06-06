import type { PlanNode, PlanDocument } from '@/types/shared'
import type { JSONContent } from '@tiptap/react'

/** 服务端 PlanDocument → Tiptap JSONContent */
export function planToTiptapDoc(plan: PlanDocument | { title: string; summary: string; nodes: PlanNode[] }): JSONContent {
  return {
    type: 'doc',
    content: plan.nodes.map(nodeToTiptap),
  }
}

function nodeToTiptap(node: PlanNode): JSONContent {
  switch (node.type) {
    case 'heading':
      return {
        type: 'heading',
        attrs: { level: node.level },
        content: [{ type: 'text', text: node.text }],
      }
    case 'paragraph':
      return {
        type: 'paragraph',
        content: [{ type: 'text', text: node.text }],
      }
    case 'decision':
      return { type: 'decision', attrs: { ...node } }
    case 'glossary':
      return { type: 'glossary', attrs: { ...node } }
    case 'step':
      return { type: 'step', attrs: { ...node } }
  }
}
