import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { Editor } from '@tiptap/react'
import type { Node as ProseMirrorNode } from '@tiptap/pm/model'
import { PlanDocEditor, type SelectionSnapshot } from './editor/PlanDocEditor'
import { planToTiptapDoc } from './editor/serializer'
import { applyAnchorMark } from './editor/marks/AnchorMark'
import './editor/marks/anchor.css'
import {
  sessionReducer,
  allBlockingAnswered,
  type ActionStep,
  type SessionState,
} from './state/sessionReducer'
import { SessionContext, type SessionContextValue } from './state/SessionContext'
import { PlanStreamClient, type WSEvent } from './api/ws'
import { ActStreamClient, actEventToAction } from './api/act'
import {
  createSession,
  advanceToActing,
  requestRerun,
  completeSession,
} from './api/sessions'
import { createComment, listComments } from './api/comments'
import { mergeReviews } from './api/merge'
import { getPlan, listPlans } from './api/plans'
import { findAnchorRange } from './editor/anchorMatch'
import type {
  CommentResponse,
  MergerResult,
  PlanDocument,
  PlanSummary,
} from '@/types/shared'
import { InitForm } from './components/InitForm'
import { ActionButton } from './components/ActionButton'
import { ErrorBanner } from './components/ErrorBanner'
import { CommentThreadPanel } from './components/CommentThreadPanel'
import { MergeResultDrawer } from './components/MergeResultDrawer'
import { ActionOutputPanel } from './components/ActionOutputPanel'
import './App.css'

function eventToAction(event: WSEvent) {
  switch (event.type) {
    case 'plan.start':
      return { type: 'WS_PLAN_START' as const, title: event.title, summary: event.summary }
    case 'plan.node':
      return { type: 'WS_PLAN_NODE' as const, node: event.node }
    case 'plan.done':
      return { type: 'WS_PLAN_DONE' as const, totalNodes: event.total_nodes }
    case 'error':
      return { type: 'WS_ERROR' as const, code: event.code, message: event.message }
    default:
      return { type: 'WS_ERROR' as const, code: 'unknown', message: 'Unknown event' }
  }
}

// merge 抽屉状态：决策结果 + 新版本快照（prevPlan 在 ref，关抽屉即弃）
interface DrawerState {
  result: MergerResult
  planChanged: boolean
  planVersion: number
  plan: PlanDocument
}

// 待提交评论：stepId 非 null 表示 step 评论（anchor_id 派生自 step_id，设计 27 D2）
interface PendingComment {
  sel: SelectionSnapshot
  stepId: string | null
}

/** 历史版本只读浏览时传入的空悬空集合（设计 §3.4：同版本精确匹配必然命中） */
const EMPTY_DANGLING: ReadonlySet<string> = new Set()

const STEP_ANCHOR_PREFIX = 'step:'

/** step 评论的 quote_context：失败原因优先，退回 stdout 首行，截断到 schema 上限 200（设计 27 D2） */
function stepQuoteContext(step: ActionStep): string {
  const firstLine = (step.stdout || step.output).split('\n', 1)[0]?.trim() ?? ''
  return (step.failureReason || firstLine).slice(0, 200)
}

export default function App() {
  const [state, dispatch] = useReducer(sessionReducer, { status: 'idle' } as SessionState)
  const clientRef = useRef<PlanStreamClient | null>(null)
  const actClientRef = useRef<ActStreamClient | null>(null)
  const editorRef = useRef<Editor | null>(null)
  const actionAreaRef = useRef<HTMLDivElement | null>(null)
  const [pending, setPending] = useState<PendingComment | null>(null)
  const [mergeBusy, setMergeBusy] = useState(false)
  // ===== M2-13：版本历史浏览 =====
  const prevPlanRef = useRef<PlanDocument | null>(null)
  const [drawer, setDrawer] = useState<DrawerState | null>(null)
  const [versions, setVersions] = useState<PlanSummary[]>([])
  const [viewingVersion, setViewingVersion] = useState<number | null>(null) // null = 当前版本
  const [historicPlan, setHistoricPlan] = useState<PlanDocument | null>(null)
  const [historicComments, setHistoricComments] = useState<CommentResponse[]>([])
  // ===== M2-14：回放悬空评论的 anchor_id 集合（设计 §3.3） =====
  const [dangling, setDangling] = useState<Set<string>>(new Set())
  // ===== M4-27：action_review 局部状态 =====
  const [highlightStep, setHighlightStep] = useState<string | null>(null)
  const [completeBusy, setCompleteBusy] = useState(false)
  // rerun/complete/评论失败留在面板局部，不进 reducer：dispatch WS_ERROR 会把
  // action_review 打成 error 态，用户丢失 review 现场（设计 27 D5）
  const [actionError, setActionError] = useState<string | null>(null)

  const reviewPlanVersion =
    state.status === 'review' || state.status === 'action_review' ? state.planVersion : 0

  // 稳定 doc 引用：只在展示文档变化时重算，避免每次 render 触发编辑器 setContent 重置选区/滚动。
  // acting / action_review 期间 plan 视图保留在上方（设计 21 §4、27 D2），故继续参与 currentPlan 派生。
  const currentPlan =
    state.status === 'streaming' ||
    state.status === 'review' ||
    state.status === 'acting' ||
    state.status === 'action_review'
      ? state.plan
      : null
  const browsingHistory = viewingVersion !== null && historicPlan !== null
  const displayPlan = browsingHistory ? historicPlan : currentPlan
  const tiptapDoc = useMemo(
    () =>
      displayPlan
        ? planToTiptapDoc(displayPlan)
        : { type: 'doc', content: [] },
    [displayPlan],
  )

  // 进入 review / action_review 或 planVersion 变化（merge 落 v{N+1}）时拉评论。
  // reviewPlanVersion 是 state.planVersion 的有意代理：非 review 态恒为 0，
  // 避免 planVersion 在无关状态迁移上触发重拉（设计 27.2 §1.2）。
  // state.sessionId 不入依赖：SessionState 的 idle/error 分支没有该属性，依赖数组处无法窄化；
  // 且 reducer 里 sessionId 只随 START_SESSION 变更（同时改 status），status 已是依赖（设计 27.2 §8）
  useEffect(() => {
    if (state.status !== 'review' && state.status !== 'action_review') return
    listComments(state.sessionId, state.planVersion).then((comments) => {
      dispatch({ type: 'LOAD_COMMENTS', comments })
    }).catch(() => {
      // no comments yet — fine
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reviewPlanVersion 代理 planVersion；sessionId 只随 status 变，见上
  }, [state.status, reviewPlanVersion])

  // 进入 review 或版本变化（merge 落 v{N+1}）时刷新版本列表。sessionId / reviewPlanVersion 理由同上
  useEffect(() => {
    if (state.status !== 'review') return
    listPlans(state.sessionId)
      .then((r) => setVersions(r.versions))
      .catch(() => {
        // 版本列表拉取失败不阻塞主流程，选择器不显示即可
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sessionId 只随 status 变，见上
  }, [state.status, reviewPlanVersion])

  // 回放未在 editor 中应用的 anchor mark（页面刷新后从 DB 拉回的评论需要重新打 mark）；
  // M2-14：精确匹配升级 fuzzy 回源，置信度 < 0.7 的评论进悬空集合（设计 §3.3）
  // step 评论不参与：StepNode 是 atom 节点，title 不在文本流里，锚点在 StepCard（设计 27 D2）
  // commentsLen 是 state.comments 的有意代理：按数组 identity 触发会让任何产出新 comments
  // 数组的 dispatch（内容未变也算）都重跑一轮全文档扫描 + ProseMirror 写入（设计 27.2 §1.2）
  const commentsLen = state.status === 'review' ? state.comments.length : 0
  useEffect(() => {
    if (state.status !== 'review' || !editorRef.current) return
    // 历史版本只读浏览时不打 anchor mark（当前版本的评论属于另一份文档）
    if (viewingVersion !== null) return
    const editor = editorRef.current
    const existingAnchors = new Set<string>()
    editor.state.doc.descendants((node: ProseMirrorNode) => {
      node.marks.forEach((m) => {
        if (m.type.name === 'anchor') {
          existingAnchors.add(m.attrs.anchor_id as string)
        }
      })
    })
    const nextDangling = new Set<string>()
    for (const c of state.comments) {
      if (existingAnchors.has(c.anchor_id)) continue
      const match = findAnchorRange(editor.state.doc, c.quote, c.quote_context)
      if (match) {
        applyAnchorMark(editor, match.from, match.to, {
          anchor_id: c.anchor_id,
          resolved: c.resolved,
        })
      } else {
        nextDangling.add(c.anchor_id)
      }
    }
    setDangling(nextDangling)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- commentsLen 代理 state.comments，见上
  }, [state.status, commentsLen, viewingVersion])

  const connectAct = useCallback((sid: string) => {
    const client = new ActStreamClient()
    actClientRef.current = client
    client.connect(
      sid,
      (event) => {
        const action = actEventToAction(event)
        if (action) dispatch(action)
      },
      () => {
        // plan.done/error 已把终态写入 run；意外断开保留已渲染内容
      },
      () => client.sendExecute(), // CONNECTING 时 send 会抛错，必须等 onopen（与 /plan 同模式）
    )
  }, [])

  const handleStart = async (initRequest: string, adapterId: string) => {
    try {
      const session = await createSession({
        init_request: initRequest,
        adapter_id: adapterId,
      })
      dispatch({ type: 'START_SESSION', sessionId: session.id, planVersion: session.current_plan_version })

      const client = new PlanStreamClient()
      clientRef.current = client
      client.connect(
        session.id,
        (event) => dispatch(eventToAction(event)),
        () => { /* WS closed */ },
        () => {
          // CONNECTING 时 send 会抛错，必须等 onopen 再发 generate 帧
          client.sendGenerate({
            init_request: initRequest,
            adapter_id: adapterId,
          })
        },
      )
    } catch (err) {
      dispatch({
        type: 'WS_ERROR',
        code: 'session_create_failed',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }

  const handleAdvance = async () => {
    if (state.status !== 'review') return
    try {
      await advanceToActing(state.sessionId)
      dispatch({ type: 'START_ACTING' })
      setActionError(null)
      connectAct(state.sessionId)
    } catch (err) {
      dispatch({
        type: 'WS_ERROR',
        code: 'advance_failed',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }

  /** 设计 27 D3：POST /rerun 登记 → START_RERUN 截断旧 steps → 重连 /act 消费 pending_rerun_from */
  const handleRerun = useCallback(async (stepId: string) => {
    if (state.status !== 'action_review') return
    const sid = state.sessionId
    setActionError(null)
    try {
      await requestRerun(sid, stepId)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'rerun_failed')
      return
    }
    dispatch({ type: 'START_RERUN', fromStepId: stepId })
    setPending(null)
    setHighlightStep(null)
    actClientRef.current?.close()
    connectAct(sid)
  }, [state, connectAct])

  /** 设计 27 D5：complete 失败（如 embedding 不可用 502）只落局部文案，保留 action_review 可重试 */
  const handleComplete = useCallback(async () => {
    if (state.status !== 'action_review') return
    setCompleteBusy(true)
    setActionError(null)
    try {
      await completeSession(state.sessionId)
      dispatch({ type: 'SESSION_COMPLETED' })
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'complete_failed')
    } finally {
      setCompleteBusy(false)
    }
  }, [state])

  const handleReset = () => {
    clientRef.current?.close()
    clientRef.current = null
    actClientRef.current?.close()
    actClientRef.current = null
    setPending(null)
    setHighlightStep(null)
    setActionError(null)
    setDrawer(null)
    dispatch({ type: 'RESET' })
  }

  // ===== Comment handlers =====

  const handleRequestAddComment = useCallback((sel: SelectionSnapshot) => {
    setPending({ sel, stepId: null })
  }, [])

  const handleRequestStepComment = useCallback((step: ActionStep) => {
    // from/to 对 step 评论无意义（后端不存，仅前端 applyAnchorMark 用），传 0（设计 27 D2）
    setPending({
      sel: { from: 0, to: 0, quote: step.title, quoteContext: stepQuoteContext(step) },
      stepId: step.stepId,
    })
    setHighlightStep(step.stepId)
  }, [])

  const handleSubmitComment = useCallback(async (body: string) => {
    if (!pending) return
    if (state.status !== 'review' && state.status !== 'action_review') return
    const isStep = pending.stepId !== null
    const anchor_id = isStep
      ? `${STEP_ANCHOR_PREFIX}${pending.stepId}`
      : crypto.randomUUID().replace(/-/g, '').slice(0, 16)
    const planVersion = state.planVersion
    const inActionReview = state.status === 'action_review'
    try {
      const comment = await createComment(state.sessionId, {
        anchor_id,
        plan_version: planVersion,
        quote: pending.sel.quote,
        quote_context: pending.sel.quoteContext,
        body,
      })
      // 写入成功后落 Mark；step 评论无文本节点可打（设计 27 D2）
      if (!isStep && editorRef.current) {
        applyAnchorMark(editorRef.current, pending.sel.from, pending.sel.to, {
          anchor_id,
          resolved: false,
        })
      }
      dispatch({ type: 'ADD_COMMENT', comment })
      setPending(null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'comment_failed'
      if (inActionReview) {
        setActionError(msg)
      } else {
        dispatch({ type: 'WS_ERROR', code: 'comment_create_failed', message: msg })
      }
    }
  }, [pending, state])

  const handleApplyReviews = useCallback(async () => {
    if (state.status !== 'review' && state.status !== 'action_review') return
    if (mergeBusy) return
    setMergeBusy(true)
    try {
      const result = await mergeReviews(state.sessionId)
      prevPlanRef.current = state.plan
      if (result.plan_changed) {
        dispatch({
          type: 'MERGE_COMPLETED',
          planVersion: result.plan_version,
          plan: result.plan,
        })
      }
      // 抽屉替换 alert（决策 §13-1.A）；全 reject 也照常打开，展示决策与 "Plan unchanged"
      setViewingVersion(null)
      setHistoricPlan(null)
      setPending(null)
      setHighlightStep(null)
      setActionError(null)
      setDrawer({
        result: result.merger_result,
        planChanged: result.plan_changed,
        planVersion: result.plan_version,
        plan: result.plan,
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'merge_failed'
      dispatch({ type: 'WS_ERROR', code: 'merge_failed', message: msg })
    } finally {
      setMergeBusy(false)
    }
  }, [state, mergeBusy])

  const handleVersionChange = useCallback(async (value: string) => {
    if (state.status !== 'review') return
    setDrawer(null) // 抽屉与版本浏览互斥（设计 §6）
    const version = Number(value)
    // 选中当前版本 = 切回 "current"：恢复 reducer 状态（设计 §3.4）
    if (value === 'current' || version === state.planVersion) {
      setViewingVersion(null)
      setHistoricPlan(null)
      setHistoricComments([])
      return
    }
    setViewingVersion(version)
    try {
      const [plan, comments] = await Promise.all([
        getPlan(state.sessionId, version),
        listComments(state.sessionId, version),
      ])
      setHistoricPlan(plan)
      setHistoricComments(comments)
    } catch {
      // 历史版本拉取失败退回当前版本
      setViewingVersion(null)
      setHistoricPlan(null)
    }
  }, [state])

  const findStepCard = useCallback((stepId: string): HTMLElement | null => {
    const root = actionAreaRef.current
    if (!root) return null
    // 遍历比对而非拼选择器：stepId 不参与 CSS selector，免受引号/特殊字符影响
    for (const el of Array.from(root.querySelectorAll<HTMLElement>('[data-step-id]'))) {
      if (el.dataset.stepId === stepId) return el
    }
    return null
  }, [])

  const handleJumpToAnchor = useCallback((anchorId: string) => {
    // step 评论的锚点是 ActionOutputPanel 里的 StepCard，不是编辑器文档（设计 27 D2）
    if (anchorId.startsWith(STEP_ANCHOR_PREFIX)) {
      const stepId = anchorId.slice(STEP_ANCHOR_PREFIX.length)
      const card = findStepCard(stepId)
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' })
        setHighlightStep(stepId)
      }
      return
    }
    if (!editorRef.current) return
    const { doc } = editorRef.current.state
    let pos: { from: number; to: number } | null = null
    doc.descendants((node, p) => {
      if (!node.isText) return
      node.marks.forEach((m) => {
        if (m.type.name === 'anchor' && m.attrs.anchor_id === anchorId) {
          pos = { from: p, to: p + node.nodeSize }
        }
      })
    })
    if (pos) {
      editorRef.current.commands.setTextSelection(pos)
      editorRef.current.commands.scrollIntoView()
    }
  }, [findStepCard])

  // ===== Derived state =====

  const isBusy =
    state.status === 'connecting' ||
    state.status === 'streaming' ||
    state.status === 'acting'
  const hasPlan =
    state.status === 'streaming' ||
    state.status === 'review' ||
    state.status === 'acting' ||
    state.status === 'action_review'
  const isReview = state.status === 'review'
  const reviewLike = isReview || state.status === 'action_review'
  const showActionPanel = state.status === 'acting' || state.status === 'action_review'
  const sessionId =
    state.status !== 'idle' && state.status !== 'error' ? state.sessionId : ''
  const nodes =
    state.status === 'streaming' ||
    state.status === 'review' ||
    state.status === 'acting' ||
    state.status === 'action_review'
      ? state.plan.nodes
      : []
  const comments = reviewLike
    ? browsingHistory
      ? historicComments
      : state.comments
    : []
  // rerunnable 来自 plan 节点（types/shared.d.ts），ActionStep 里没有 → 在此建映射（设计 27 D3）
  // 依赖 currentPlan 而非 nodes：后者 else 分支是每次 render 新建的 []，会令 memo 失效
  const rerunnableStepIds = useMemo(() => {
    const ids = new Set<string>()
    for (const n of currentPlan?.nodes ?? []) {
      if (n.type === 'step' && n.rerunnable) ids.add(n.id)
    }
    return ids
  }, [currentPlan])

  const contextValue: SessionContextValue = { sessionId, dispatch }

  return (
    <SessionContext.Provider value={contextValue}>
      <div className="app">
        <header className="app-header">
          <h1>PRAR Agent</h1>
          <p className="subtitle">Plan / Review / Action / Review</p>
        </header>

        <main className={reviewLike ? 'app-main-review' : ''}>
          <InitForm onSubmit={handleStart} disabled={isBusy} />

          {hasPlan && (
            <div className="review-layout">
              <div className="review-editor">
                {isReview && versions.length > 1 && (
                  <label className="version-picker">
                    Version{' '}
                    <select
                      value={viewingVersion ?? 'current'}
                      onChange={(e) => handleVersionChange(e.target.value)}
                    >
                      {versions.map((v) => (
                        <option key={v.version} value={v.version}>
                          v{v.version}
                          {v.version === state.planVersion ? ' (current)' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <PlanDocEditor
                  doc={tiptapDoc}
                  onRequestAddComment={
                    isReview && !browsingHistory ? handleRequestAddComment : undefined
                  }
                  editorRef={editorRef}
                />
              </div>
              {reviewLike && (
                <CommentThreadPanel
                  comments={comments}
                  pendingSelection={pending?.sel ?? null}
                  onCancel={() => setPending(null)}
                  onSubmit={handleSubmitComment}
                  onJumpToAnchor={handleJumpToAnchor}
                  onApplyReviews={browsingHistory ? undefined : handleApplyReviews}
                  mergeBusy={mergeBusy}
                  unresolvedCount={comments.filter((c) => !c.resolved).length}
                  readonly={browsingHistory}
                  danglingIds={browsingHistory ? EMPTY_DANGLING : dangling}
                />
              )}
            </div>
          )}

          {drawer && (
            <MergeResultDrawer
              result={drawer.result}
              planChanged={drawer.planChanged}
              newVersion={drawer.planVersion}
              prevPlan={prevPlanRef.current}
              newPlan={drawer.plan}
              onClose={() => setDrawer(null)}
            />
          )}

          {isReview && (
            <ActionButton
              disabled={!allBlockingAnswered(nodes)}
              onClick={handleAdvance}
            />
          )}

          {showActionPanel && (
            <div className="action-area" ref={actionAreaRef} data-testid="action-area">
              {actionError !== null && (
                <p className="action-local-error" data-testid="action-local-error">
                  {actionError}
                </p>
              )}
              {state.status === 'acting' && <ActionOutputPanel run={state.run} />}
              {state.status === 'action_review' && (
                <ActionOutputPanel
                  run={state.run}
                  reviewable
                  rerunnableStepIds={rerunnableStepIds}
                  onRerun={handleRerun}
                  onComment={handleRequestStepComment}
                  highlightStepId={highlightStep}
                />
              )}
              {state.status === 'action_review' && (
                <div className="action-bar" data-testid="complete-bar">
                  <button
                    type="button"
                    className="action-button complete-button"
                    onClick={handleComplete}
                    disabled={completeBusy}
                  >
                    {completeBusy ? '提交中…' : '标记完成'}
                  </button>
                </div>
              )}
            </div>
          )}

          {state.status === 'done' && (
            <div className="done-banner" data-testid="done-banner">
              <p>
                会话已完成 · <code>{state.sessionId}</code>
              </p>
              <button type="button" className="action-button" onClick={handleReset}>
                开始新会话
              </button>
            </div>
          )}

          {state.status === 'error' && (
            <ErrorBanner
              code={state.code}
              message={state.message}
              onDismiss={handleReset}
            />
          )}
        </main>
      </div>
    </SessionContext.Provider>
  )
}
