interface ActionButtonProps {
  disabled: boolean
  onClick: () => void
}

export function ActionButton({ disabled, onClick }: ActionButtonProps) {
  return (
    <div className="action-bar" data-testid="action-bar">
      <button
        className="action-button"
        disabled={disabled}
        onClick={onClick}
        title={disabled ? '请先完成所有必选决策题' : '进入 Action 阶段'}
      >
        进入 Action
      </button>
    </div>
  )
}
