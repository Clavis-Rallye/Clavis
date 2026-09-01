import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { lazy, type ComponentType } from 'react';
import LazyRoute from './LazyRoute';
import '../i18n';

// The real ClavisCircularProgress renders a custom element
// (<md-circular-progress>) that Testing Library can't query as usefully as a
// plain node — same stub pattern as WelcomeGuard.test.tsx.
vi.mock('./ClavisCircularProgress', () => ({
  default: () => <div data-testid="loading-indicator" />,
}));

describe('<LazyRoute>', () => {
  it('shows the loading indicator while the lazy page resolves, then the page', async () => {
    let resolveImport: (mod: { default: ComponentType }) => void = () => {};
    const LazyPage = lazy(
      () => new Promise<{ default: ComponentType }>((resolve) => { resolveImport = resolve; }),
    );

    render(
      <LazyRoute>
        <LazyPage />
      </LazyRoute>,
    );
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();

    resolveImport({ default: () => <div data-testid="page">Page content</div> });

    expect(await screen.findByTestId('page')).toBeInTheDocument();
    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('offers a reload instead of a blank page when the chunk fails to load', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onReload = vi.fn();
    const FailingPage = lazy(() => Promise.reject(new Error('chunk load failed')));

    render(
      <LazyRoute onReload={onReload}>
        <FailingPage />
      </LazyRoute>,
    );

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reload page' }));
    expect(onReload).toHaveBeenCalledOnce();

    consoleErrorSpy.mockRestore();
  });
});
