import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@/components/ui'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

/** Catches render throws at the shell level (docs/PLAN.md §12e step
 * 5.4: "any render throw blanks the whole page") — an error boundary
 * has no hook equivalent, so this is the one class component in the
 * app. "Reload" rather than a "retry" that just clears the error: a
 * render throw usually means bad/unexpected data already in the
 * component tree or query cache, which re-rendering the same tree
 * won't fix — a full reload re-fetches everything from scratch. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: 'var(--space-4)',
            padding: 'var(--space-9)',
            fontFamily: 'var(--font-sans)',
            color: 'var(--text-primary)',
          }}
        >
          <div style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)' }}>
            Something went wrong
          </div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--text-sm-size)',
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {this.state.error.message}
          </div>
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
