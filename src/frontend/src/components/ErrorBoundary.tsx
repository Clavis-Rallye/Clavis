import React from 'react';
import { Box, Card, CardContent, Typography, Button, Alert } from '@mui/material';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  message: string;
}

class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, message: error.message || 'Unknown error' };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('ErrorBoundary caught an error:', error, info);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            minHeight: "60vh",
            p: 2
          }}>
          <Card sx={{ maxWidth: 480, width: '100%' }}>
            <CardContent sx={{ p: 4, textAlign: 'center' }}>
              <Typography variant="h5" gutterBottom>
                Something went wrong
              </Typography>
              <Alert severity="error" sx={{ mb: 3, textAlign: 'left' }}>
                {this.state.message}
              </Alert>
              <Button variant="contained" onClick={this.handleReload}>
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

export default ErrorBoundary;
