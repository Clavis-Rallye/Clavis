import { describe, expect, it } from 'vitest';
import { cleanTips, tipsProblem } from './stationTips';

describe('tipsProblem', () => {
  const link = { label: { de: 'Mehr zum Dom' }, url: 'https://www.augsburg.de' };

  it('takes no tips and whole tips', () => {
    expect(tipsProblem(null)).toBeNull();
    expect(tipsProblem({ header: { de: '<p>Mehr</p>' }, links: [link] })).toBeNull();
  });

  it('names the missing heading first', () => {
    expect(tipsProblem({ header: {}, links: [link] })).toBe('header');
    expect(tipsProblem({ header: { de: '<p></p>' }, links: [link] })).toBe('header');
  });

  it('names a heading without a link, a link without a label and a link without an address', () => {
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [] })).toBe('links');
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [{ label: {}, url: link.url }] })).toBe('label');
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [{ label: link.label, url: ' ' }] })).toBe('url');
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [{ label: link.label, url: 'augsburg.de' }] })).toBe('url');
  });

  it('leaves out a language whose text was typed and deleted again', () => {
    expect(
      cleanTips({ header: { de: 'Mehr', en: '' }, links: [{ label: { de: 'Dom', en: ' ' }, url: ' https://a.de ' }] }),
    ).toEqual({ header: { de: 'Mehr' }, links: [{ label: { de: 'Dom' }, url: 'https://a.de' }] });
    expect(cleanTips(null)).toBeNull();
  });

  // Two short sentences must not be refused at 120 characters.
  it('takes a heading of several sentences and a long label, and names the two bounds', () => {
    const sentences = '<p>Hier findest du mehr zum Dom. Die Seite der Stadt nennt auch die Öffnungszeiten und den Weg.</p>';
    const heading = sentences.repeat(3);
    expect(heading.length).toBeGreaterThan(120);
    expect(tipsProblem({ header: { de: heading }, links: [link] })).toBeNull();
    expect(tipsProblem({ header: { de: 'x'.repeat(2000) }, links: [link] })).toBeNull();
    expect(tipsProblem({ header: { de: 'x'.repeat(2001) }, links: [link] })).toBe('headerTooLong');
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [{ label: { de: 'x'.repeat(200) }, url: link.url }] })).toBeNull();
    expect(tipsProblem({ header: { de: 'Mehr' }, links: [{ label: { de: 'x'.repeat(201) }, url: link.url }] })).toBe(
      'labelTooLong',
    );
  });
});
