import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionRun, ActionStep } from '@/state/sessionReducer'
import { ActionOutputPanel } from './ActionOutputPanel'

function makeStep(overrides: Partial<ActionStep> = {}): ActionStep {
  return {
    index: 1,
    stepId: 's1',
    title: '安装依赖',
    tool: 'shell',
    toolArgs: { cmd: 'npm install' },
    status: 'running',
    stdout: 'line1\nline2',
    output: '',
    exitCode: null,
    attempts: 1,
    artifacts: [],
    thoughts: [],
    failureReason: null,
    gitCommit: null,
    ...overrides,
  }
}

const runningRun: ActionRun = {
  status: 'running',
  allOk: null,
  error: null,
  steps: [makeStep()],
}

afterEach(() => {
  delete (navigator as { clipboard?: unknown }).clipboard
})

describe('ActionOutputPanel', () => {
  it('C1 renders step title, tool, tool args, exit badge and log', () => {
    render(
      <ActionOutputPanel
        run={{ ...runningRun, steps: [makeStep({ status: 'done', exitCode: 0 })] }}
      />,
    )
    expect(screen.getByText('Action 执行输出')).toBeDefined()
    expect(screen.getByText('安装依赖')).toBeDefined()
    expect(screen.getByText('tool=shell')).toBeDefined()
    expect(screen.getByText(/npm install/)).toBeDefined()
    expect(screen.getByText('exit 0')).toBeDefined()
    expect(screen.getByText(/line1/)).toBeDefined()
    expect(screen.getByText('✓')).toBeDefined()
  })

  it('C1b running step shows running badge and header status', () => {
    render(<ActionOutputPanel run={runningRun} />)
    expect(screen.getByText('执行中…')).toBeDefined()
    expect(screen.getByText('执行中')).toBeDefined()
  })

  it('C2 falls back to output when stdout is empty', () => {
    render(
      <ActionOutputPanel
        run={{
          ...runningRun,
          steps: [makeStep({ stdout: '', output: 'fs.read result', status: 'done' })],
        }}
      />,
    )
    expect(screen.getByText(/fs\.read result/)).toBeDefined()
  })

  it('C3 shows failure reason in .step-failure element', () => {
    const { container } = render(
      <ActionOutputPanel
        run={{ ...runningRun, steps: [makeStep({ status: 'failed', failureReason: 'exit code 1' })] }}
      />,
    )
    expect(screen.getByText('✗')).toBeDefined()
    const el = container.querySelector('.step-failure')
    expect(el).not.toBeNull()
    expect(el?.textContent).toContain('exit code 1')
  })

  it('C4 done + allOk shows summary and git commit chip', () => {
    render(
      <ActionOutputPanel
        run={{
          status: 'done',
          allOk: true,
          error: null,
          steps: [makeStep({ status: 'done', gitCommit: 'a1b2c3' })],
        }}
      />,
    )
    expect(screen.getByText('完成')).toBeDefined()
    expect(screen.getByText(/共 1 步 · 全部成功/)).toBeDefined()
    expect(screen.getByText('git: a1b2c3')).toBeDefined()
  })

  it('C4b done + !allOk hints partial failure', () => {
    render(
      <ActionOutputPanel
        run={{
          status: 'done',
          allOk: false,
          error: null,
          steps: [makeStep({ status: 'failed' })],
        }}
      />,
    )
    expect(screen.getByText('完成（部分步骤失败）')).toBeDefined()
    expect(screen.getByText(/共 1 步 · 部分失败/)).toBeDefined()
  })

  it('C5 renders error banner for failed run', () => {
    render(
      <ActionOutputPanel
        run={{ status: 'failed', allOk: null, error: 'internal: boom', steps: [makeStep()] }}
      />,
    )
    expect(screen.getByText('执行失败')).toBeDefined()
    expect(screen.getByText('internal: boom')).toBeDefined()
  })

  it('C6 copy button writes log text to clipboard and flips label', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<ActionOutputPanel run={runningRun} />)
    fireEvent.click(screen.getByRole('button', { name: '复制' }))
    expect(writeText).toHaveBeenCalledWith('line1\nline2')
    await waitFor(() => expect(screen.getByRole('button', { name: '已复制' })).toBeDefined())
  })

  it('C6b copy failure is silent and keeps label', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'))
    Object.assign(navigator, { clipboard: { writeText } })
    render(<ActionOutputPanel run={runningRun} />)
    fireEvent.click(screen.getByRole('button', { name: '复制' }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: '复制' })).toBeDefined()
  })
})
