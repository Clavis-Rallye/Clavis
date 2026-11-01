import { describe, it, expect, beforeAll } from 'vitest';
import i18n from '../i18n';
import { sortableA11y } from './sortableA11y';

/**
 * Reordering stations by keyboard must not announce
 * dnd-kit's English defaults with UUIDs. The texts name the station and its
 * position in the UI language.
 */
describe('sortableA11y', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de');
  });

  const items = [
    { id: 'a1b2', label: 'Rathausplatz' },
    { id: 'c3d4', label: 'Perlachturm' },
    { id: 'e5f6', label: 'Fuggerei' },
  ];
  const { announcements, screenReaderInstructions } = sortableA11y(items, i18n.t);
  const active = { id: 'a1b2' } as never;

  it('instructs in German', () => {
    expect(screenReaderInstructions.draggable).toMatch(/^Zum Aufnehmen Leertaste oder Enter drücken/);
  });

  it('names the item and its position, never its id', () => {
    expect(announcements.onDragStart({ active })).toBe('Rathausplatz aufgenommen, Position 1 von 3.');
    expect(announcements.onDragOver({ active, over: { id: 'c3d4' } as never })).toBe(
      'Rathausplatz ist jetzt an Position 2 von 3.',
    );
    expect(announcements.onDragEnd({ active, over: { id: 'e5f6' } as never })).toBe(
      'Rathausplatz an Position 3 von 3 abgelegt.',
    );
    expect(announcements.onDragCancel({ active, over: null })).toBe(
      'Verschieben abgebrochen. Rathausplatz bleibt an seinem Platz.',
    );
    const all = [
      announcements.onDragStart({ active }),
      announcements.onDragEnd({ active, over: { id: 'e5f6' } as never }),
    ].join(' ');
    expect(all).not.toContain('a1b2');
  });
});
