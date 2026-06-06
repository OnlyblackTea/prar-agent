import { useState } from 'react'

interface InitFormProps {
  onSubmit: (initRequest: string, adapterId: string) => void
  disabled: boolean
}

export function InitForm({ onSubmit, disabled }: InitFormProps) {
  const [initRequest, setInitRequest] = useState('')
  const [adapterId, setAdapterId] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!initRequest.trim() || !adapterId.trim()) return
    onSubmit(initRequest.trim(), adapterId.trim())
  }

  return (
    <form className="init-form" onSubmit={handleSubmit} data-testid="init-form">
      <div className="init-form-field">
        <label htmlFor="adapter-id">Adapter ID</label>
        <input
          id="adapter-id"
          type="text"
          placeholder="输入 adapter UUID"
          value={adapterId}
          onChange={(e) => setAdapterId(e.target.value)}
          disabled={disabled}
          required
        />
      </div>
      <div className="init-form-field">
        <label htmlFor="init-request">你的需求</label>
        <textarea
          id="init-request"
          placeholder="例如：实现一个 todo list 应用"
          value={initRequest}
          onChange={(e) => setInitRequest(e.target.value)}
          disabled={disabled}
          rows={3}
          required
        />
      </div>
      <button type="submit" disabled={disabled || !initRequest.trim() || !adapterId.trim()}>
        {disabled ? '处理中…' : '开始生成 Plan'}
      </button>
    </form>
  )
}
