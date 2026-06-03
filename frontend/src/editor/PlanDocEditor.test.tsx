import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PlanDocEditor } from './PlanDocEditor'

describe('PlanDocEditor', () => {
  it('renders initial content', () => {
    render(<PlanDocEditor initialContent="<p>Test content</p>" />)
    expect(screen.getByText('Test content')).toBeInTheDocument()
  })

  it('renders default text when no prop', () => {
    render(<PlanDocEditor />)
    expect(screen.getByText('Hello, PRAR-Agent.')).toBeInTheDocument()
  })
})
