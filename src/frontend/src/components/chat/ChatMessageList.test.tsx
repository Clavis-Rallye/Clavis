import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatMessageList from './ChatMessageList';

describe('ChatMessageList', () => {
  // The list scrolls (axe rule scrollable-region-focusable), so it must take
  // the keyboard and carry a name. It stays a live log.
  it('takes the keyboard, takes its name from the prop and stays a polite log', () => {
    render(<ChatMessageList messages={[]} label="Teamchat" />);
    const log = screen.getByRole('log', { name: 'Teamchat' });
    expect(log).toHaveAttribute('tabindex', '0');
    expect(log).toHaveAttribute('aria-live', 'polite');
  });

  it('still renders as a log when no name is given', () => {
    render(<ChatMessageList messages={[]} />);
    expect(screen.getByRole('log')).toHaveAttribute('tabindex', '0');
  });

  // The list is the only part of a chat column that scrolls. It grows into the
  // room the column has and may shrink to nothing. The participant chat and
  // the coordinator chat both build their column on this.
  it('scrolls inside itself and takes the room its column leaves', () => {
    const messages = Array.from({ length: 50 }, (_, i) => ({
      id: `m${i}`,
      organization_id: 'org-1',
      team_id: 'team-1',
      author_id: 'someone',
      content: `Nachricht ${i + 1}`,
      is_system_message: false,
      author_role: 'participant' as const,
      author_display_name: 'Anna',
      created_at: '2026-01-01T10:00:00Z',
    }));
    render(<ChatMessageList messages={messages} label="Teamchat" />);

    const log = screen.getByRole('log', { name: 'Teamchat' });
    expect(log).toHaveStyle({ flexGrow: '1', minHeight: '0', overflowY: 'auto' });
    expect(log).toHaveTextContent('Nachricht 50');
  });
});
