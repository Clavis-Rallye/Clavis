import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import ConfirmDialog from './ConfirmDialog';
import '../i18n';

describe('ConfirmDialog', () => {
  it('renders title and message when open', () => {
    render(
      <ConfirmDialog
        open={true}
        title="Delete rallye"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText('Delete rallye')).toBeInTheDocument();
    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
  });

  it('does not render content when closed', () => {
    render(
      <ConfirmDialog
        open={false}
        title="Delete rallye"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByText('Delete rallye')).not.toBeInTheDocument();
  });

  it('calls onConfirm when confirm button clicked', async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="T"
        message="M"
        confirmLabel="Yes"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Yes' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('calls onCancel when cancel button clicked', async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="T"
        message="M"
        cancelLabel="No"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('disables both buttons while loading', () => {
    render(
      <ConfirmDialog
        open={true}
        title="T"
        message="M"
        confirmLabel="OK"
        cancelLabel="Cancel"
        loading={true}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });

  it('uses error color for destructive variant', () => {
    render(
      <ConfirmDialog
        open={true}
        title="T"
        message="M"
        confirmLabel="Delete"
        destructive={true}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const confirmButton = screen.getByRole('button', { name: 'Delete' });
    expect(confirmButton.className).toMatch(/colorError/);
  });

  // A destructive dialog must not open with the focus on its confirm button,
  // or Enter then Space would archive a station.
  it('opens a destructive dialog on Abbrechen and a plain one on the confirm button', async () => {
    const { rerender } = render(
      <ConfirmDialog
        open
        title="Archivieren?"
        message="Wirklich archivieren?"
        confirmLabel="Archivieren"
        destructive
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abbrechen' })).toHaveFocus());

    rerender(
      <ConfirmDialog
        open={false}
        title="Archivieren?"
        message="Wirklich archivieren?"
        confirmLabel="Archivieren"
        destructive
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    render(
      <ConfirmDialog
        open
        title="Starten?"
        message="Lauf starten?"
        confirmLabel="Starten"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Starten' })).toHaveFocus());
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = render(
      <ConfirmDialog
        open={true}
        title="Löschen bestätigen"
        message="Soll der Eintrag wirklich gelöscht werden?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
