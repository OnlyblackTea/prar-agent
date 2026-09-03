import { useEffect, useRef, useState } from 'react'
import type { ActionRun, ActionStep } from '@/state/sessionReducer'

export interface ActionOutputPanelProps {
  run: ActionRun
  /** 仅 action_review 态传 true：执行中不给出重跑/评论入口 */
  reviewable?: boolean
  /** plan.nodes 里 rerunnable === true 的 step_id 集合 */
  rerunnableStepIds?: ReadonlySet<string>
  onRerun?: (stepId: string) => void
  onComment?: (step: ActionStep) => void
  /** 评论列表点击跳转时高亮的 step */
  highlightStepId?: string | null
}

function headerStatus(run: ActionRun): string {
  if (run.status === 'running') return '执行中…'
  if (run.status === 'failed') return '执行失败'
  return run.allOk ? '完成' : '完成（部分步骤失败）'
}

function stepBadge(step: ActionStep): string {
  if (step.status === 'done') return '✓'
  if (step.status === 'failed') return '✗'
  return '执行中'
}

interface StepCardProps {
  step: ActionStep
  canRerun: boolean
  canComment: boolean
  highlighted: boolean
  onRerun?: (stepId: string) => void
  onComment?: (step: ActionStep) => void
}

function StepCard({ step, canRerun, canComment, highlighted, onRerun, onComment }: StepCardProps) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    [],
  )

  const logText = step.stdout || step.output
  const hasDetails = step.thoughts.length > 0 || step.artifacts.length > 0

  const handleCopy = () => {
    const text = step.stdout || step.output
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true)
        timerRef.current = window.setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {
        // clipboard API 不可用（非 localhost http）时静默降级，log 区 user-select 可手动复制
      })
  }

  const className = [
    'action-step',
    `action-step-${step.status}`,
    highlighted ? 'action-step-highlight' : null,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className} data-step-id={step.stepId}>
      <div className="action-step-header">
        <span className="action-step-index">#{step.index}</span>
        <span className="action-step-title">{step.title}</span>
        <span className="action-step-tool">tool={step.tool}</span>
        {step.exitCode !== null && <span className="action-step-exit">exit {step.exitCode}</span>}
        <span className={`action-step-badge badge-${step.status}`}>{stepBadge(step)}</span>
        <button type="button" className="action-copy" onClick={handleCopy}>
          {copied ? '已复制' : '复制'}
        </button>
        {canComment && (
          <button
            type="button"
            className="action-comment-btn"
            onClick={() => onComment?.(step)}
          >
            评论
          </button>
        )}
        {canRerun && (
          <button
            type="button"
            className="action-rerun-btn"
            onClick={() => onRerun?.(step.stepId)}
          >
            重跑
          </button>
        )}
      </div>
      {Object.keys(step.toolArgs).length > 0 && (
        <pre className="step-args">{JSON.stringify(step.toolArgs, null, 2)}</pre>
      )}
      <pre className="step-log">{logText}</pre>
      {step.failureReason !== null && <p className="step-failure">{step.failureReason}</p>}
      <div className="action-step-meta">
        <span>attempts={step.attempts}</span>
        {step.gitCommit !== null && <span className="action-git">git: {step.gitCommit}</span>}
      </div>
      {hasDetails && (
        <details className="action-step-details">
          <summary>详情</summary>
          {step.thoughts.length > 0 && (
            <div>
              <strong>thoughts</strong>
              <ul>
                {step.thoughts.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </div>
          )}
          {step.artifacts.length > 0 && (
            <div>
              <strong>artifacts</strong>
              <ul>
                {step.artifacts.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}
        </details>
      )}
    </div>
  )
}

export function ActionOutputPanel({
  run,
  reviewable = false,
  rerunnableStepIds,
  onRerun,
  onComment,
  highlightStepId = null,
}: ActionOutputPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (run.status === 'running' && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [run.steps, run.status])

  const settled = run.status !== 'running'
  const canComment = reviewable && settled && onComment !== undefined
  const canRerun = reviewable && settled && onRerun !== undefined

  return (
    <section className="action-panel">
      <div className="action-panel-header">
        <h3>Action 执行输出</h3>
        <span className={`action-panel-status status-${run.status}`}>{headerStatus(run)}</span>
      </div>
      {run.error !== null && <div className="action-error-banner">{run.error}</div>}
      <div className="action-step-list" ref={scrollRef}>
        {run.steps.map((s) => (
          <StepCard
            key={s.stepId}
            step={s}
            canComment={canComment}
            canRerun={canRerun && (rerunnableStepIds?.has(s.stepId) ?? false)}
            highlighted={highlightStepId === s.stepId}
            onRerun={onRerun}
            onComment={onComment}
          />
        ))}
      </div>
      {settled && (
        <p className="action-summary">
          共 {run.steps.length} 步 ·{' '}
          {run.status === 'failed' ? '执行中断' : run.allOk ? '全部成功' : '部分失败'}
        </p>
      )}
    </section>
  )
}
