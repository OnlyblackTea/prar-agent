import { describe, expect, it } from 'vitest'
import { planToTiptapDoc } from './serializer'

describe('planToTiptapDoc', () => {
  it('converts heading node', () => {
    const result = planToTiptapDoc({
      title: 'T',
      summary: 'S',
      nodes: [{ type: 'heading', level: 1, text: 'Hello' }],
    })
    expect(result.type).toBe('doc')
    expect(result.content).toHaveLength(1)
    expect(result.content![0]).toMatchObject({
      type: 'heading',
      attrs: { level: 1 },
      content: [{ type: 'text', text: 'Hello' }],
    })
  })

  it('converts paragraph node', () => {
    const result = planToTiptapDoc({
      title: 'T',
      summary: 'S',
      nodes: [{ type: 'paragraph', text: 'Some text' }],
    })
    expect(result.content![0]).toMatchObject({
      type: 'paragraph',
      content: [{ type: 'text', text: 'Some text' }],
    })
  })

  it('converts decision node', () => {
    const result = planToTiptapDoc({
      title: 'T',
      summary: 'S',
      nodes: [
        {
          type: 'decision',
          id: 'dec_001',
          question: 'Yes or no?',
          kind: 'single_choice',
          options: ['Yes', 'No'],
          answer: null,
          blocking: true,
        },
      ],
    })
    expect(result.content![0].type).toBe('decision')
    expect(result.content![0].attrs).toMatchObject({ id: 'dec_001', question: 'Yes or no?' })
  })

  it('converts glossary node', () => {
    const result = planToTiptapDoc({
      title: 'T',
      summary: 'S',
      nodes: [{ type: 'glossary', id: 'gls_001', term: 'API', definition: 'Application Programming Interface' }],
    })
    expect(result.content![0].type).toBe('glossary')
    expect(result.content![0].attrs).toMatchObject({ term: 'API' })
  })

  it('converts step node', () => {
    const result = planToTiptapDoc({
      title: 'T',
      summary: 'S',
      nodes: [
        {
          type: 'step',
          id: 'step_001',
          title: 'Run tests',
          description: 'Execute the test suite',
          tool: 'shell',
          tool_args: { command: 'pytest' },
          rerunnable: true,
        },
      ],
    })
    expect(result.content![0].type).toBe('step')
    expect(result.content![0].attrs).toMatchObject({ title: 'Run tests', tool: 'shell' })
  })

  it('handles empty nodes', () => {
    const result = planToTiptapDoc({ title: 'T', summary: 'S', nodes: [] })
    expect(result.content).toEqual([])
  })
})
