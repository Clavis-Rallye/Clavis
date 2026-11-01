import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import RichTextLinkDialog from './RichTextLinkDialog';
import { normalizeLinkAddress } from './linkAddress';

describe('normalizeLinkAddress', () => {
  it('keeps an address with an allowed scheme', () => {
    expect(normalizeLinkAddress(' https://www.augsburg.de/rathaus ')).toBe('https://www.augsburg.de/rathaus');
    expect(normalizeLinkAddress('mailto:info@example.org')).toBe('mailto:info@example.org');
  });

  it('puts https in front of a bare address', () => {
    expect(normalizeLinkAddress('augsburg.de/rathaus')).toBe('https://augsburg.de/rathaus');
  });

  it('takes a host with a port and turns a bare mail address into a mail link', () => {
    expect(normalizeLinkAddress('augsburg.de:8080/x')).toBe('https://augsburg.de:8080/x');
    expect(normalizeLinkAddress('name@example.org')).toBe('mailto:name@example.org');
  });

  it('refuses a scheme without a host, a host that ends in a dot, and tel, which the server strips', () => {
    expect(normalizeLinkAddress('https://')).toBeNull();
    expect(normalizeLinkAddress('Ende.')).toBeNull();
    expect(normalizeLinkAddress('tel:+49821123')).toBeNull();
    expect(normalizeLinkAddress('mailto:')).toBeNull();
  });

  it('refuses what is no address, and every other scheme', () => {
    expect(normalizeLinkAddress('')).toBeNull();
    expect(normalizeLinkAddress('Rathaus Augsburg')).toBeNull();
    expect(normalizeLinkAddress('rathaus')).toBeNull();
    expect(normalizeLinkAddress('javascript:alert(1)')).toBeNull();
    expect(normalizeLinkAddress('ftp://example.org/file')).toBeNull();
  });
});

// The editor's own link dialog, not the browser's prompt.
describe('<RichTextLinkDialog>', () => {
  const open = () => {
    const onInsert = vi.fn();
    const onClose = vi.fn();
    render(<RichTextLinkDialog open onClose={onClose} onInsert={onInsert} />);
    return { onInsert, onClose };
  };

  it('inserts a checked address', () => {
    const { onInsert, onClose } = open();

    fireEvent.change(screen.getByLabelText('Adresse des Links'), { target: { value: 'augsburg.de' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link einfügen' }));

    expect(onInsert).toHaveBeenCalledWith('https://augsburg.de');
    expect(onClose).toHaveBeenCalled();
  });

  it('names a wrong address and inserts nothing', () => {
    const { onInsert, onClose } = open();

    fireEvent.change(screen.getByLabelText('Adresse des Links'), { target: { value: 'javascript:alert(1)' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link einfügen' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Das ist keine Web-Adresse');
    expect(onInsert).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('cancel inserts nothing', () => {
    const { onInsert, onClose } = open();

    fireEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));

    expect(onInsert).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
