import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RichTextImageSettingsDialog, { type ImageSettings } from './RichTextImageSettingsDialog';

const image: ImageSettings = { alt: 'Das Rathaus', caption: 'Am Markt', width: '50', align: 'center' };

const renderDialog = (overrides: Partial<ImageSettings> = {}) => {
  const onSave = vi.fn();
  const onClose = vi.fn();
  render(
    <RichTextImageSettingsDialog open image={{ ...image, ...overrides }} onSave={onSave} onClose={onClose} />,
  );
  return { onSave, onClose, user: userEvent.setup() };
};

describe('<RichTextImageSettingsDialog>', () => {
  it('shows the alternative text, the caption, the size and the position of the image', () => {
    renderDialog();
    expect(screen.getByRole('dialog', { name: 'Beschreibung und Bildunterschrift' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /Alternativtext/ })).toHaveValue('Das Rathaus');
    expect(screen.getByRole('textbox', { name: /Bildunterschrift/ })).toHaveValue('Am Markt');
    expect(screen.getByRole('radiogroup', { name: 'Größe' })).toBeInTheDocument();
    expect(screen.getByRole('radiogroup', { name: 'Position' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Mittel' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Mitte' })).toBeChecked();
    expect(screen.getAllByRole('radio', { name: /Klein|Mittel|Groß|Volle Breite|Originalgröße/ })).toHaveLength(5);
    expect(screen.getAllByRole('radio', { name: /^(Links|Mitte|Rechts)$/ })).toHaveLength(3);
  });

  it('shows an image without a size as original size and without a position as left', () => {
    renderDialog({ width: null, align: null });
    expect(screen.getByRole('radio', { name: 'Originalgröße' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Links' })).toBeChecked();
  });

  it('saves the changed values', async () => {
    const { onSave, user } = renderDialog();
    const alt = screen.getByRole('textbox', { name: /Alternativtext/ });
    await user.clear(alt);
    await user.type(alt, ' Rathaus von vorn ');
    const caption = screen.getByRole('textbox', { name: /Bildunterschrift/ });
    await user.clear(caption);
    await user.type(caption, 'Neu');
    await user.click(screen.getByRole('radio', { name: 'Groß' }));
    await user.click(screen.getByRole('radio', { name: 'Rechts' }));
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    expect(onSave).toHaveBeenCalledWith({ alt: 'Rathaus von vorn', caption: 'Neu', width: '75', align: 'right' });
  });

  it('saves original size as no width', async () => {
    const { onSave, user } = renderDialog();
    await user.click(screen.getByRole('radio', { name: 'Originalgröße' }));
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ width: null }));
  });

  it('keeps an image without a position without one when the position is not touched', async () => {
    const { onSave, user } = renderDialog({ align: null });
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ align: null }));
  });

  it('turns an emptied caption into no caption', async () => {
    const { onSave, user } = renderDialog();
    await user.clear(screen.getByRole('textbox', { name: /Bildunterschrift/ }));
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ caption: null }));
  });

  it('refuses to save without alternative text and says why, as the insert dialog does', async () => {
    const { onSave, user } = renderDialog();
    await user.clear(screen.getByRole('textbox', { name: /Alternativtext/ }));
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText('Bitte beschreibe das Bild in einem Satz.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /Alternativtext/ })).toBeInvalid();
  });

  it('closes without saving', async () => {
    const { onSave, onClose, user } = renderDialog();
    await user.click(screen.getByRole('button', { name: 'Abbrechen' }));
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('opens again with the values of the image, not the unsaved ones', async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<RichTextImageSettingsDialog open image={image} onSave={onSave} onClose={onClose} />);
    await user.type(screen.getByRole('textbox', { name: /Bildunterschrift/ }), ' mehr');
    rerender(<RichTextImageSettingsDialog open={false} image={image} onSave={onSave} onClose={onClose} />);
    rerender(<RichTextImageSettingsDialog open image={image} onSave={onSave} onClose={onClose} />);
    expect(await screen.findByRole('textbox', { name: /Bildunterschrift/ })).toHaveValue('Am Markt');
  });
});
