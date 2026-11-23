import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../api/media', () => ({
  ORG_MEDIA_IMAGE_TYPES: ['image/png', 'image/jpeg'],
  uploadOrgMedia: vi.fn(),
}));

import RichTextImageDialog from './RichTextImageDialog';
import { uploadOrgMedia } from '../api/media';

// The hidden file input must not be a stop of the dialog's focus trap.
describe('<RichTextImageDialog> keyboard', () => {
  const open = () => {
    render(<RichTextImageDialog open orgSlug="org" onClose={vi.fn()} onInsert={vi.fn()} />);
    return userEvent.setup();
  };

  it('moves forward through the choose button, the alternative text, the caption and cancel, and stays inside', async () => {
    const user = open();
    const dialog = screen.getByRole('dialog');
    const chooseButton = screen.getByRole('button', { name: 'Bild auswählen' });
    const altField = screen.getByRole('textbox', { name: /Alternativtext/ });
    const captionField = screen.getByRole('textbox', { name: /Bildunterschrift/ });
    const cancel = screen.getByRole('button', { name: 'Abbrechen' });

    await user.tab();
    expect(chooseButton).toHaveFocus();
    await user.tab();
    expect(altField).toHaveFocus();
    await user.tab();
    expect(captionField).toHaveFocus();
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
    const captionField = screen.getByRole('textbox', { name: /Bildunterschrift/ });
    const cancel = screen.getByRole('button', { name: 'Abbrechen' });

    await user.tab();
    expect(chooseButton).toHaveFocus();
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(cancel).toHaveFocus();
    await user.tab({ shift: true });
    expect(captionField).toHaveFocus();
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

describe('<RichTextImageDialog> caption', () => {
  const insertWith = async (caption: string | null) => {
    vi.mocked(uploadOrgMedia).mockResolvedValue({ url: '/media/a.png' } as never);
    const onInsert = vi.fn();
    render(<RichTextImageDialog open orgSlug="org" onClose={vi.fn()} onInsert={onInsert} />);
    const user = userEvent.setup();
    await user.upload(
      screen.getByTestId('rich-text-image-input'),
      new File([new Uint8Array([137, 80])], 'a.png', { type: 'image/png' }),
    );
    await user.type(screen.getByRole('textbox', { name: /Alternativtext/ }), 'Ein Bild');
    if (caption !== null) await user.type(screen.getByRole('textbox', { name: /Bildunterschrift/ }), caption);
    await user.click(screen.getByRole('button', { name: 'Einfügen' }));
    await waitFor(() => expect(onInsert).toHaveBeenCalled());
    return onInsert;
  };

  it('shows an optional caption field with its helper below the alt text', () => {
    render(<RichTextImageDialog open orgSlug="org" onClose={vi.fn()} onInsert={vi.fn()} />);
    expect(screen.getByRole('textbox', { name: 'Bildunterschrift (optional)' })).not.toBeRequired();
    expect(screen.getByText('Steht unter dem Bild, für alle sichtbar.')).toBeInTheDocument();
  });

  it('passes the caption to the inserted image', async () => {
    const onInsert = await insertWith('  Das Rathaus  ');
    expect(onInsert).toHaveBeenCalledWith({ src: '/media/a.png', alt: 'Ein Bild', caption: 'Das Rathaus' });
  });

  it('inserts without a caption key when the field stays empty', async () => {
    const onInsert = await insertWith(null);
    expect(onInsert).toHaveBeenCalledWith({ src: '/media/a.png', alt: 'Ein Bild' });
  });
});
