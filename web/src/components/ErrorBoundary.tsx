import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

/**
 * Catches a rendering error below it and shows a message with a Reload button instead of a
 * blank page. `resetKey` (e.g. the route path) clears the error when the user navigates away.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string; resetKey?: string },
  { error: Error | null; resetKey: string | undefined }
> {
  constructor(props: { children: ReactNode; label?: string; resetKey?: string }) {
    super(props);
    this.state = { error: null, resetKey: props.resetKey };
  }

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(
    props: { resetKey?: string },
    state: { error: Error | null; resetKey: string | undefined },
  ) {
    if (props.resetKey !== state.resetKey) return { error: null, resetKey: props.resetKey };
    return null;
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ReconBench page error", error, info.componentStack);
  }

  override render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="panel error-boundary" role="alert">
        <AlertTriangle size={18} aria-hidden="true" />
        <div>
          <strong>{this.props.label ?? "This page"} hit an unexpected error.</strong>
          <span>Reload the page to continue. Saved changes are kept on the server.</span>
          <small>{this.state.error.message}</small>
        </div>
        <button className="button" type="button" onClick={() => window.location.reload()}><RefreshCw size={14} /> Reload</button>
      </section>
    );
  }
}
