import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react';
import { Box, Button, Card, CardContent, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import ClavisCircularProgress from './ClavisCircularProgress';

function LazyRouteFallback() {
  const { t } = useTranslation('common');
  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '60vh',
        p: 2,
      }}
    >
      <ClavisCircularProgress indeterminate ariaLabel={t('loading')} />
    </Box>
  );
}

interface LazyRouteErrorBoundaryProps {
  children: ReactNode;
  onReload: () => void;
}

interface LazyRouteErrorBoundaryState {
  hasError: boolean;
}

// A dynamic import() rejection (chunk 404 after a deploy, or offline) throws
// during render like any other error — only a class component can catch it,
// React has no hook equivalent (as of Jan 2026).
class LazyRouteErrorBoundary extends Component<LazyRouteErrorBoundaryProps, LazyRouteErrorBoundaryState> {
  constructor(props: LazyRouteErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): LazyRouteErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('LazyRoute failed to load a chunk:', error, info);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: '60vh',
            p: 2,
          }}
        >
          <Card sx={{ maxWidth: 480, width: '100%' }}>
            <CardContent sx={{ p: 4, textAlign: 'center' }}>
              <Typography variant="h5" gutterBottom>
                Something went wrong
              </Typography>
              <Button variant="contained" onClick={this.props.onReload}>
                Reload page
              </Button>
            </CardContent>
          </Card>
        </Box>
      );
    }
    return this.props.children;
  }
}

interface LazyRouteProps {
  children: ReactNode;
  /** Overridable for tests; defaults to a full page reload. */
  onReload?: () => void;
}

/**
 * Wraps a `React.lazy` page: shows the app's progress indicator while its
 * chunk loads, and a reload prompt instead of a blank page if the chunk
 * fails to load.
 */
function LazyRoute({ children, onReload = () => window.location.reload() }: LazyRouteProps) {
  return (
    <LazyRouteErrorBoundary onReload={onReload}>
      <Suspense fallback={<LazyRouteFallback />}>{children}</Suspense>
    </LazyRouteErrorBoundary>
  );
}

export default LazyRoute;
