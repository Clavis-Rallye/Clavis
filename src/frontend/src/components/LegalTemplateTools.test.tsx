import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LegalTemplateTools from './LegalTemplateTools';
import i18n from '../i18n';
import { legalTemplate } from '../utils/legalTemplates';

const renderTools = (props: Partial<React.ComponentProps<typeof LegalTemplateTools>> = {}) => {
  const onInsert = vi.fn();
  render(
    <LegalTemplateTools
      kind="imprint"
      fieldLabel="Impressum"
      value=""
      savedValue={null}
      onInsert={onInsert}
      {...props}
    />,
  );
  return onInsert;
};

describe('LegalTemplateTools', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de');
  });

  it('hands the template to the owner when the field is empty', async () => {
    const onInsert = renderTools();

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

    expect(onInsert).toHaveBeenCalledExactlyOnceWith(legalTemplate('imprint'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('treats an editor document without visible text as empty', async () => {
    const onInsert = renderTools({ value: '<p> </p>' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

    expect(onInsert).toHaveBeenCalledOnce();
  });

  it('asks first when the field holds text, and inserts only on confirmation', async () => {
    const onInsert = renderTools({ value: '<p>Eigener Text</p>' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

    expect(await screen.findByRole('dialog', { name: 'Text ersetzen?' })).toBeInTheDocument();
    expect(onInsert).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Ersetzen' }));

    expect(onInsert).toHaveBeenCalledExactlyOnceWith(legalTemplate('imprint'));
  });

  it('disables the button while the page saves', () => {
    renderTools({ disabled: true });

    expect(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' })).toBeDisabled();
  });

  it('shows no warning when neither text has a placeholder', () => {
    renderTools({ value: '<p>Fertig</p>', savedValue: '<p>Fertig</p>' });

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('lists the distinct placeholders of the editor text', () => {
    renderTools({
      value: '<p>[NAME DER ORGANISATION]<br>[E-MAIL-ADRESSE]</p><p>[E-MAIL-ADRESSE]</p>',
      savedValue: '<p>[NAME DER ORGANISATION]</p>',
    });

    expect(screen.getByRole('status')).toHaveTextContent(
      'Dieser Text enthält noch Platzhalter: [NAME DER ORGANISATION], [E-MAIL-ADRESSE]',
    );
  });

  it('warns about the saved text when the editor is already clean', () => {
    renderTools({ value: '<p>Stadt Musterstadt</p>', savedValue: '<p>[NAME DER ORGANISATION]</p>' });

    expect(screen.getByRole('status')).toHaveTextContent(
      'Der gespeicherte Text enthält noch Platzhalter: [NAME DER ORGANISATION]. Speichere deine Änderungen, damit die veröffentlichte Seite sie nicht mehr zeigt.',
    );
  });

  it('says in the English UI that the template is German, and inserts the German text', async () => {
    await i18n.changeLanguage('en');
    const onInsert = renderTools();

    expect(screen.getByText(/^The template is in German, because it is a legal text for a German operator\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Insert template: Impressum' }));

    expect(onInsert.mock.calls[0]?.[0]).toContain('<h2>Anbieter</h2>');
  });

  it('describes the button with the helper text', () => {
    renderTools();

    expect(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' })).toHaveAccessibleDescription(
      /Die Vorlage ist ein Entwurf ohne rechtliche Prüfung\./,
    );
  });
});
