import { describe, expect, it } from 'vitest';
import { cleanPastedHtml } from './cleanPastedHtml';
import contract from './richTextContract.json';
import {
  GOOGLE_DOCS_PASTE_HTML,
  WEB_PASTE_HTML,
  WEB_WIDE_TABLE_PASTE_HTML,
  WORD_LISTS_PASTE_HTML,
  WORD_PASTE_HTML,
} from './pasteFixtures';

const MEDIA = '/api/orgs/stadt/media/content/rathaus.jpg';

describe('cleanPastedHtml on the clipboard samples', () => {
  it('cleans the Word sample: list paragraphs, formatting and the table', () => {
    expect(cleanPastedHtml(WORD_PASTE_HTML)).toEqual({
      html:
        '<p><strong>Aus Word: Treffpunkt</strong></p>' +
        '<ul><li><p>Word-Punkt eins</p></li><li><p>Word-Punkt zwei</p></li></ul>' +
        '<p><em>Kursiv aus Word</em> und gelb markiert</p>' +
        '<table><tbody><tr><td><p>Zelle 1</p></td><td><p>Zelle 2</p></td></tr></tbody></table>',
      imagesLeftOut: 0,
    });
  });

  it('cleans the Word sample with a numbered list and a second level', () => {
    expect(cleanPastedHtml(WORD_LISTS_PASTE_HTML)).toEqual({
      html:
        '<ol><li><p>Anmelden</p>' +
        '<ol><li><p>Mit <strong>Namen</strong></p></li><li><p>Mit Nummer</p></li></ol></li>' +
        '<li><p>Eintreten</p></li></ol>' +
        '<p>Danach <u>Pause</u> und <s>Ende</s></p>',
      imagesLeftOut: 0,
    });
  });

  it('does not turn the whole Google Docs text bold and keeps its bold and italic spans', () => {
    expect(cleanPastedHtml(GOOGLE_DOCS_PASTE_HTML)).toEqual({
      html:
        '<h2>Aus Google Docs: Programm</h2>' +
        '<p>Wir treffen uns <strong>pünktlich</strong> um <em>zehn Uhr</em> am Brunnen.</p>' +
        '<ul><li><p>Erster Punkt</p><ul><li><p><strong>Unterpunkt</strong></p></li></ul></li>' +
        '<li><p>Zweiter Punkt</p></li></ul>',
      imagesLeftOut: 0,
    });
  });

  it('cleans the web sample and counts the foreign image', () => {
    expect(cleanPastedHtml(WEB_PASTE_HTML)).toEqual({
      html:
        '<h2>Aus dem Web: Öffnungszeiten</h2>' +
        '<p>Die <strong>Fuggerei</strong> ist <a href="https://example.org/fuggerei">täglich</a> geöffnet.<br>Eintritt: 8 €</p>' +
        '<ul><li><p>Montag bis Sonntag</p></li><li><p>9 bis 18 Uhr</p></li></ul>' +
        '<p>Klick mich</p>' +
        '<h3>Kleine Überschrift</h3>' +
        '<p>Böser Link</p>',
      imagesLeftOut: 1,
    });
  });

  it('keeps only what the contract lists, for every sample', () => {
    const elements = contract.elements as Record<string, string[]>;
    for (const sample of [WORD_PASTE_HTML, WORD_LISTS_PASTE_HTML, GOOGLE_DOCS_PASTE_HTML, WEB_PASTE_HTML]) {
      const body = new DOMParser().parseFromString(cleanPastedHtml(sample).html, 'text/html').body;
      for (const element of Array.from(body.querySelectorAll('*'))) {
        const name = element.localName;
        expect(Object.keys(elements), name).toContain(name);
        for (const attribute of Array.from(element.attributes)) {
          expect(elements[name], `${name}@${attribute.name}`).toContain(attribute.name);
        }
      }
    }
  });
});

describe('cleanPastedHtml on the hostile parts', () => {
  const clean = (html: string) => cleanPastedHtml(html);

  it('drops scripts, frames, styles and event handlers with their content', () => {
    const { html } = clean(
      '<p onclick="alert(1)" style="color:red" class="x" id="y">Text</p><script>alert(1)</script><style>p{}</style>' +
        '<iframe src="https://example.org"></iframe><object data="x"></object><embed src="x"><form><input value="geheim"><button>Los</button></form>',
    );
    expect(html).toBe('<p>Text</p>');
  });

  it('keeps the text of a link with a script address and drops the address', () => {
    expect(clean('<p><a href="javascript:alert(3)">Böser Link</a></p>').html).toBe('<p>Böser Link</p>');
    expect(clean('<p><a href=" JaVaScRiPt:alert(3)">A</a><a href="data:text/html,x">B</a><a href="/relativ">C</a></p>').html).toBe(
      '<p>ABC</p>',
    );
  });

  it('keeps http, https and mailto links with the address only', () => {
    expect(
      clean('<p><a href="HTTP://example.org/a" target="_self" onclick="x()" style="color:red">A</a> <a href="mailto:a@example.org">B</a></p>').html,
    ).toBe('<p><a href="HTTP://example.org/a">A</a> <a href="mailto:a@example.org">B</a></p>');
  });

  it('leaves out every image outside the media route and counts them', () => {
    const result = clean(
      '<p>Vorher<img src="https://example.org/pixel.gif" alt="Pixel">Nachher</p>' +
        '<img src="data:image/png;base64,AAAA"><img src="/api/orgs/stadt/media/../secret.png"><img src="//example.org/a.png">',
    );
    expect(result.html).toBe('<p>VorherNachher</p>');
    expect(result.imagesLeftOut).toBe(4);
  });

  it('keeps an image of the media route as a block with its alt text and its size', () => {
    const result = clean(`<p>Vor <img src="${MEDIA}" alt="Rathaus" data-width="50" data-align="center" style="width:90px" loading="lazy" onerror="x()"> nach</p>`);
    expect(result.html).toBe(
      `<p>Vor</p><img src="${MEDIA}" alt="Rathaus" data-width="50" data-align="center"><p>nach</p>`,
    );
    expect(result.imagesLeftOut).toBe(0);
    expect(clean(`<img src="${MEDIA}" data-width="33" data-align="up">`).html).toBe(`<img src="${MEDIA}" alt="">`);
  });

  it('keeps a figure with an image of the media route and its caption', () => {
    expect(
      clean(`<figure data-width="75" data-align="right" style="x:y"><img src="${MEDIA}" alt="Rathaus"><figcaption><em>Das</em> Rathaus</figcaption></figure>`).html,
    ).toBe(
      `<figure data-width="75" data-align="right"><img src="${MEDIA}" alt="Rathaus"><figcaption><em>Das</em> Rathaus</figcaption></figure>`,
    );
  });

  it('turns a figure with a foreign image into its caption text', () => {
    const result = clean('<figure><img src="https://example.org/a.jpg"><figcaption>Unterschrift</figcaption></figure>');
    expect(result.html).toBe('<p>Unterschrift</p>');
    expect(result.imagesLeftOut).toBe(1);
  });

  it('drops hidden elements', () => {
    expect(clean('<p>Sichtbar</p><div style="display: none">Versteckt</div><p style="mso-hide:all">Auch</p>').html).toBe(
      '<p>Sichtbar</p>',
    );
  });
});

describe('cleanPastedHtml on structure', () => {
  const clean = (html: string) => cleanPastedHtml(html).html;

  it('maps headings: h1 and h2 to h2, h3 to h6 to h3', () => {
    expect(clean('<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6>')).toBe(
      '<h2>a</h2><h2>b</h2><h3>c</h3><h3>d</h3><h3>e</h3><h3>f</h3>',
    );
  });

  it('keeps every contract element', () => {
    expect(
      clean(
        '<p>Ein <b>fetter</b>, <i>kursiver</i>, <u>unterstrichener</u> und <del>gestrichener</del> <strike>Text</strike><br>zwei</p>' +
          '<blockquote>Zitat</blockquote><hr><ol><li>eins</li></ol>',
      ),
    ).toBe(
      '<p>Ein <strong>fetter</strong>, <em>kursiver</em>, <u>unterstrichener</u> und <s>gestrichener</s> <s>Text</s><br>zwei</p>' +
        '<blockquote><p>Zitat</p></blockquote><hr><ol><li><p>eins</p></li></ol>',
    );
  });

  it('reads bold, italic, underline and strike-through from span styles', () => {
    expect(
      clean(
        '<p><span style="font-weight:bold">a</span> <span style="font-style: italic">b</span> <span style="text-decoration:underline">c</span> <span style="text-decoration: line-through">d</span> <span style="font-weight:normal">e</span></p>',
      ),
    ).toBe('<p><strong>a</strong> <em>b</em> <u>c</u> <s>d</s> e</p>');
  });

  it('ignores a bold element that is set to normal weight', () => {
    expect(clean('<b style="font-weight:normal"><p>Text</p></b>')).toBe('<p>Text</p>');
  });

  it('merges neighbouring marks and does not nest a mark in itself', () => {
    expect(clean('<p><b>a</b><b>b <b>c</b></b><i><b>d</b></i></p>')).toBe(
      '<p><strong>ab c<em>d</em></strong></p>',
    );
  });

  it('puts the marks of a block element on its text', () => {
    expect(clean('<b><p>eins</p><p>zwei</p></b>')).toBe('<p><strong>eins</strong></p><p><strong>zwei</strong></p>');
  });

  it('gives loose text and divs their paragraphs and does not glue them', () => {
    expect(clean('lose<div>eins<div>zwei</div></div>Rest<section>drei</section>')).toBe(
      '<p>lose</p><p>eins</p><p>zwei</p><p>Rest</p><p>drei</p>',
    );
  });

  it('turns non-breaking spaces and runs of white space into one space and trims', () => {
    expect(clean('<p>\n  Ein&nbsp;&nbsp; Wort\n  und   <b> noch </b> eins&nbsp;</p><p>&nbsp;</p><p><o:p></o:p></p>')).toBe(
      '<p>Ein Wort und <strong>noch </strong>eins</p>',
    );
  });

  it('drops leading and trailing line breaks and empty elements', () => {
    expect(clean('<p><br>Text<br></p><p><br></p><ul><li></li></ul><blockquote> </blockquote><h2></h2><p><b></b></p>')).toBe(
      '<p>Text</p>',
    );
  });

  it('puts the lines of a preformatted block into one paragraph with line breaks', () => {
    expect(clean('<pre>eins\nzwei <code>drei</code></pre>')).toBe('<p>eins<br>zwei drei</p>');
  });

  it('nests a list in a list item and a stray list in the list', () => {
    expect(clean('<ul><li>a<ul><li>b</li></ul></li><ol><li>c</li></ol><li>d</li></ul>')).toBe(
      '<ul><li><p>a</p><ul><li><p>b</p></li></ul><ol><li><p>c</p></li></ol></li><li><p>d</p></li></ul>',
    );
  });

  it('keeps a table with header cells, drops spans and widths, and turns a caption into a paragraph', () => {
    expect(
      clean(
        '<table width="100%" style="x:y"><caption>Titel</caption><thead><tr><th colspan="2" style="width:3cm">A</th></tr></thead>' +
          '<tbody><tr><td rowspan="2">1<br>2</td><td><ul><li>x</li></ul></td></tr><tr><td></td></tr></tbody></table><p>danach</p>',
      ),
    ).toBe(
      '<p>Titel</p><table><tbody><tr><th><p>A</p></th></tr>' +
        '<tr><td><p>1<br>2</p></td><td><ul><li><p>x</p></li></ul></td></tr><tr><td></td></tr></tbody></table><p>danach</p>',
    );
  });

  it('drops a table without cells', () => {
    expect(clean('<table><tr></tr></table><p>a</p>')).toBe('<p>a</p>');
  });

  it('starts a new list when the Word list changes its kind at the first level', () => {
    const item = (level: number, marker: string, text: string) =>
      `<p class=MsoListParagraph style='mso-list:l0 level${level} lfo1'><span style='mso-list:Ignore'>${marker}</span>${text}</p>`;
    expect(clean(item(1, '·', 'a') + item(1, '1.', 'b') + item(2, 'o', 'c') + item(1, '2.', 'd'))).toBe(
      '<ul><li><p>a</p></li></ul><ol><li><p>b</p><ul><li><p>c</p></li></ul></li><li><p>d</p></li></ol>',
    );
  });

  it('keeps a Word list paragraph without a marker as a list item', () => {
    expect(clean("<p style='mso-list:l0 level1 lfo1'>ohne</p>")).toBe('<ul><li><p>ohne</p></li></ul>');
  });

  it('returns an empty string for text without content', () => {
    expect(cleanPastedHtml('')).toEqual({ html: '', imagesLeftOut: 0 });
    expect(cleanPastedHtml('<meta charset="utf-8"><div> </div>')).toEqual({ html: '', imagesLeftOut: 0 });
  });

  it('escapes what it writes', () => {
    expect(clean('<p>a &lt;b&gt; &amp; c</p>')).toBe('<p>a &lt;b&gt; &amp; c</p>');
  });
});

describe('cleanPastedHtml on tables', () => {
  const clean = (html: string) => cleanPastedHtml(html).html;

  it('keeps the table of the Word sample a table', () => {
    expect(clean(WORD_PASTE_HTML)).toContain(
      '<table><tbody><tr><td><p>Zelle 1</p></td><td><p>Zelle 2</p></td></tr></tbody></table>',
    );
  });

  it('turns a web table of six columns into the caption and one paragraph per row', () => {
    const html = clean(WEB_WIDE_TABLE_PASTE_HTML);
    expect(html).toBe(
      '<p>Fahrplan Sommer</p>' +
        '<p><strong>Linie · Start · Ziel · Abfahrt · Dauer · Preis</strong></p>' +
        '<p>Linie 1 · Passau · Wien · 9:00 · 7 Stunden · 45 €</p>' +
        '<p>Linie 2 · Linz · Donaudampfschifffahrtsgesellschaft · 11:30 · 3 Stunden · 25 €</p>',
    );
    expect(html).not.toMatch(/style|class|<table/);
    const words = [
      'Fahrplan', 'Sommer', 'Linie', 'Start', 'Ziel', 'Abfahrt', 'Dauer', 'Preis', 'Passau', 'Wien', 'Linz',
      '9:00', '11:30', 'Stunden', '45', '25', '€', 'Donaudampfschifffahrtsgesellschaft',
    ];
    for (const word of words) expect(html).toContain(word);
  });

  it('keeps a web table of three columns a table', () => {
    const html = clean(
      '<table class="x" style="width:100%"><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead><tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody></table>',
    );
    expect(html).toBe(
      '<table><tbody><tr><th><p>A</p></th><th><p>B</p></th><th><p>C</p></th></tr><tr><td><p>1</p></td><td><p>2</p></td><td><p>3</p></td></tr></tbody></table>',
    );
  });
});

describe('cleanPastedHtml in linear time', () => {
  // A wall-time limit measures the runner, a ratio measures the growth.
  const bestOf = (runs: number, fn: () => unknown): number => {
    let best = Infinity;
    for (let i = 0; i < runs; i += 1) {
      const start = performance.now();
      fn();
      best = Math.min(best, performance.now() - start);
    }
    return best;
  };

  it('grows by less than a factor of ten when the style value grows by four', () => {
    const styleSmall = 'text-decoration:'.repeat(31_250);
    const styleLarge = 'text-decoration:'.repeat(125_000);
    const small = bestOf(3, () => cleanPastedHtml(`<p><span style="${styleSmall}">a</span></p>`));
    const large = bestOf(3, () => cleanPastedHtml(`<p><span style="${styleLarge}">a</span></p>`));
    expect(large / Math.max(small, 1)).toBeLessThan(10);
    expect(large).toBeLessThan(10_000);
    expect(cleanPastedHtml(`<p><span style="${styleLarge}">a</span></p>`).html).toBe('<p>a</p>');
  });

  it('still counts an underline and a strike-through in a normal style', () => {
    const html =
      '<p><span style="color:red; text-decoration : underline">a</span>' +
      '<span style="TEXT-DECORATION-LINE:underline line-through;color:red">b</span>' +
      '<span style="text-decoration:none;text-decoration:line-through">c</span>' +
      '<span style="text-decoration:none">d</span></p>';
    expect(cleanPastedHtml(html).html).toBe('<p><u>a<s>b</s></u><s>c</s>d</p>');
  });
});
