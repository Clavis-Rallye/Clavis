import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PendingInvitations from './PendingInvitations';
import apiClient from '../../api/client';
import type { InvitationResponse } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const invitation: InvitationResponse = {
  id: 'inv-1',
  email: 'kollegin@augsburg.example',
  role: 'coordinator',
  display_name: null,
  language: 'de',
  invited_by_name: 'Admin',
  created_at: '2026-07-27T12:00:00Z',
  expires_at: '2026-08-03T12:00:00Z',
  mail_delivered: true,
};

const renderList = (invitations: InvitationResponse[] = [invitation]) => {
  const onChanged = vi.fn();
  const onNotify = vi.fn();
  const onError = vi.fn();
  const view = render(
    <PendingInvitations
      orgSlug="stadt-augsburg"
      invitations={invitations}
      onChanged={onChanged}
      onNotify={onNotify}
      onError={onError}
    />,
  );
  return { ...view, onChanged, onNotify, onError };
};

describe('PendingInvitations', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders nothing when there is nothing outstanding', () => {
    const { container } = renderList([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the address, role and who invited them', () => {
    renderList();
    expect(screen.getByText('kollegin@augsburg.example')).toBeInTheDocument();
    expect(screen.getByText(/eingeladen von admin/i)).toBeInTheDocument();
  });

  it('marks an invitation whose mail did not go out', () => {
    renderList([{ ...invitation, mail_delivered: false }]);
    expect(screen.getByText(/nicht zugestellt/i)).toBeInTheDocument();
  });

  it('resends and tells the admin the old link is dead', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: invitation });
    const { onNotify, onChanged } = renderList();

    await userEvent.click(screen.getByRole('button', { name: /erneut senden/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/orgs/stadt-augsburg/invitations/inv-1/resend',
      ),
    );
    expect(onNotify).toHaveBeenCalledWith(expect.stringMatching(/alte link gilt nicht mehr/i));
    expect(onChanged).toHaveBeenCalled();
  });

  it('asks before withdrawing an invitation', async () => {
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: undefined });
    const { onChanged } = renderList();

    await userEvent.click(screen.getByRole('button', { name: /zurückziehen/i }));
    // Confirmation first — a withdrawn invitation cannot be un-withdrawn.
    expect(vi.mocked(apiClient.delete)).not.toHaveBeenCalled();

    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /zurückziehen/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.delete)).toHaveBeenCalledWith(
        '/api/orgs/stadt-augsburg/invitations/inv-1',
      ),
    );
    expect(onChanged).toHaveBeenCalled();
  });
});