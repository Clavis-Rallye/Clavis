import { describe, it, expect, afterEach } from 'vitest';
import { resolveTarget, selectorOf, visibleText } from './pointer';

function mount(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  document.body.appendChild(host);
  return host;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('selectorOf (same shape as the observer)', () => {
  it('stops at the first id', () => {
    const host = mount('<main id="content"><section><button class="a b c">Go</button></section></main>');
    expect(selectorOf(host.querySelector('button'))).toBe('main#content>section>button.a.b');
  });

  it('prefers a data-testid over classes', () => {
    const host = mount('<div><span data-testid="station-card" class="x">S</span></div>');
    expect(selectorOf(host.querySelector('span'))).toBe('body>div>div>span[data-testid=station-card]');
  });

  it("skips emotion's generated classes and keeps the stable ones", () => {
    const host = mount('<div><h1 class="MuiTypography-root css-1821gv5 MuiTypography-body1">Clavis</h1></div>');
    expect(selectorOf(host.querySelector('h1'))).toBe('body>div>div>h1.MuiTypography-root.MuiTypography-body1');
  });

  it('keeps at most four levels', () => {
    const host = mount('<div><ul><li><p><em><b>deep</b></em></p></li></ul></div>');
    expect(selectorOf(host.querySelector('b'))).toBe('li>p>em>b');
  });

  it('answers ? for no element', () => {
    expect(selectorOf(null)).toBe('?');
  });
});

describe('resolveTarget', () => {
  it('lifts a click on an icon to its button', () => {
    const host = mount('<button aria-label="Speichern"><svg><path id="p"></path></svg></button>');
    expect(resolveTarget(host.querySelector('#p'))).toBe(host.querySelector('button'));
  });

  it('keeps a plain element', () => {
    const host = mount('<h1>Stationen</h1>');
    expect(resolveTarget(host.querySelector('h1'))).toBe(host.querySelector('h1'));
  });
});

describe('visibleText', () => {
  it('squashes whitespace and cuts at 300 characters', () => {
    const host = mount(`<p>  Eins
      zwei   ${'x'.repeat(400)}</p>`);
    const text = visibleText(host.querySelector('p'));
    expect(text.startsWith('Eins zwei x')).toBe(true);
    expect(text).toHaveLength(300);
  });

  it('falls back to the aria-label of an icon button', () => {
    const host = mount('<button aria-label="Konto-Menü"><svg></svg></button>');
    expect(visibleText(host.querySelector('button'))).toBe('Konto-Menü');
  });

  it('never reads a password or a code field', () => {
    const host = mount(
      '<input type="password" placeholder="Passwort" /><input name="totp" placeholder="Code aus der App" />',
    );
    const [password, totp] = host.querySelectorAll('input');
    password.value = 'geheim123';
    totp.value = '123456';
    expect(visibleText(password)).toBe('Passwort');
    expect(visibleText(totp)).toBe('Code aus der App');
  });

  it('reads the value of an ordinary field', () => {
    const host = mount('<input name="team" placeholder="Teamname" />');
    const input = host.querySelector('input')!;
    input.value = 'Die Füchse';
    expect(visibleText(input)).toBe('Die Füchse');
  });
});
