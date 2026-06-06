interface ErrorBannerProps {
  code: string
  message: string
  onDismiss?: () => void
}

export function ErrorBanner({ code, message, onDismiss }: ErrorBannerProps) {
  return (
    <div className="error-banner" data-testid="error-banner" role="alert">
      <div className="error-banner-code">{code}</div>
      <div className="error-banner-message">{message}</div>
      {onDismiss && (
        <button className="error-banner-dismiss" onClick={onDismiss}>
          ✕
        </button>
      )}
    </div>
  )
}
