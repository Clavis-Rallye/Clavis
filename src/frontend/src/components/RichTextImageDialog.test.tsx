import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../api/media', () => ({
  ORG_MEDIA_IMAGE_TYPES: ['image/png', 'image/jpeg'],
  uploadOrgMedia: vi.fn(),
}));

import RichTextImageDialog from './RichTextImageDialog';

// The hidden file input must not be a stop of the dialog's focus trap.
describe('<RichTextImageDialog> keyboard', () => {
  const open = () => {
    render(<RichTextImageDialog open orgSlug="org" onClose={vi.fn()} onInsert={vi.fn()} />);
    return userEvent.setup();
  };

  it('moves forward through the choose button, the alternative text and cancel, and stays inside', async () => {
    const user = open();
    const dialog = screen.getByRole('dialog');
    const chooseButton = screen.getByRole('button', { name: 'Bild auswählen' });
    const altField = screen.getByRole('textbox', { name: /Alternativtext/ });
    const cancel = screen.getByRole('button', { name: 'Abbrechen' });

    await user.tab();
    expect(chooseButton).toHaveFocus();
    await user.tab();
    expect(altField).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus();
    // "Einfügen" is disabled without a file, so the trap wraps to the first stop.
    await user.tab();
    expect(chooseButton).toHaveFocus();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it('moves backward in the reverse order and stays inside', async () => {
    const user = open();
    const dialog = screen.getByRole('dialog');
    const chooseButton = screen.getByRole('button', { name: 'Bild auswählen' });
    const altField = screen.getByRole('textbox', { name: /Alternativtext/ });
    const cancel = screen.getByRole('button', { name: 'Abbrechen' });

    await user.tab();
    expect(chooseButton).toHaveFocus();
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(cancel).toHaveFocus();
    await user.tab({ shift: true });
    expect(altField).toHaveFocus();
    await user.tab({ shift: true });
    expect(chooseButton).toHaveFocus();
  });

  it('gives the hidden file input no tab stop and hides it from assistive technology', () => {
    open();
    const input = screen.getByTestId('rich-text-image-input');
    expect(input).toHaveAttribute('tabindex', '-1');
    expect(input).toHaveAttribute('aria-hidden', 'true');
  });

  it('still opens the file chooser through the button', async () => {
    const user = open();
    const input = screen.getByTestId('rich-text-image-input') as HTMLInputElement;
    const click = vi.spyOn(input, 'click');
    await user.click(screen.getByRole('button', { name: 'Bild auswählen' }));
    expect(click).toHaveBeenCalled();
  });
});
