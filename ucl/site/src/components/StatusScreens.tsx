export function LoadingScreen() {
  return (
    <div className="status-page" role="status" aria-live="polite">
      <span className="brand" aria-hidden="true">
        <span className="brand__mark">UCL</span> Scout
      </span>
      <p className="status-page__mark status-page__mark--pulse">Loading matchday data…</p>
    </div>
  );
}

export function ErrorScreen({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="status-page" role="alert">
      <p className="status-page__title">Couldn't load matchday data</p>
      <p className="status-page__detail">{message}</p>
      <p className="status-page__detail">
        Check your connection, or the site's data files may not have published yet.
      </p>
      <button type="button" className="status-page__retry" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
