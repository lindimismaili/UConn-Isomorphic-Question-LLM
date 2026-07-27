import { Component } from 'react';
import { AppShell, Button, Card, CardBody, CardHeader, EmptyState } from './ui/index.js';

/**
 * Last-resort boundary for unexpected render and lifecycle errors in the
 * application tree. The fallback intentionally omits exception details so
 * internal data is not exposed in the user-facing interface.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    this.props.onError?.(error, errorInfo);
  }

  handleReload = () => {
    if (this.props.onReload) {
      this.props.onReload();
      return;
    }
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <AppShell>
        <Card
          variant="failed"
          role="alert"
          aria-label="Unexpected application error"
        >
          <CardHeader
            title="Something went wrong"
            subtitle="The interface stopped rendering because of an unexpected error."
            actions={(
              <Button variant="primary" onClick={this.handleReload}>
                Reload application
              </Button>
            )}
          />
          <CardBody>
            <EmptyState variant="modal" error>
              If a workflow was already running, it may still be processing on the server.
            </EmptyState>
          </CardBody>
        </Card>
      </AppShell>
    );
  }
}
