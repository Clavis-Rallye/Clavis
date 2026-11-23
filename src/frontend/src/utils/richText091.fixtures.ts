/**
 * Pins the frontend behaviour of release 0.9.1: a corpus of rich-text documents,
 * what `sanitizeRichText` returns for each, and the HTML the `RichTextEditor`
 * reports after opening each one without an edit. The literals were recorded
 * from the unchanged 0.9.1 code. Never edit them by hand to make a test pass.
 */

export const RICH_TEXT_091_CORPUS: Record<string, string> = {
  everyKeptElement:
    '<h2>Überschrift</h2><h3>Unterüberschrift</h3><p>Ein <strong>fetter</strong>, <em>kursiver</em>, <u>unterstrichener</u> und <s>durchgestrichener</s> Text mit Zeilen<br>umbruch und einem <a href="https://example.org/seite" target="_blank">Link</a>.</p><ul><li>Äpfel</li><li>Öl</li></ul><ol><li>Eins</li><li>Zwei</li></ol><p><img src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Das Rathaus"></p>',
  nestedOrderedInBulleted:
    '<ul><li><p>Obere Ebene</p><ol><li><p>Erster Schritt</p></li><li><p>Zweiter Schritt</p></li></ol></li><li><p>Weiter oben</p></li></ul>',
  nestedBulletedInOrdered:
    '<ol><li><p>Erster Punkt</p><ul><li><p>Detail A</p></li><li><p>Detail B</p></li></ul></li><li><p>Zweiter Punkt</p></li></ol>',
  linkBlankWithRel:
    '<p>Mehr unter <a href="https://example.org/info" target="_blank" rel="noopener noreferrer">der Infoseite</a>.</p>',
  linkMailto: '<p>Fragen an <a href="mailto:info@example.org">info@example.org</a>.</p>',
  linkRelativePath: '<p>Siehe <a href="/hilfe/anfahrt">die Anfahrt</a>.</p>',
  imageMediaRoute:
    '<p><img src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Rathaus am Marktplatz"></p>',
  imageOutsideMediaRoute:
    '<p>Vorher<img src="https://example.org/pixel.gif" alt="Pixel">Nachher</p>',
  legacyPlainText: 'Erste Zeile\nZweite Zeile\n\nNeuer Absatz mit Größe & Maß',
  legacyServerUnfiltered:
    '<h1>Alter Titel</h1><pre><code>Zeile eins\nZeile zwei</code></pre><p>Ein Aufruf wie <code>ls -l</code> im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>',
  paragraphWithBreak: '<p>Zeile eins<br>Zeile zwei</p>',
  emptyString: '',
  emptyParagraph: '<p></p>',
};

export const SANITIZED_091: Record<string, string> = {
  everyKeptElement:
    '<h2>Überschrift</h2><h3>Unterüberschrift</h3><p>Ein <strong>fetter</strong>, <em>kursiver</em>, <u>unterstrichener</u> und <s>durchgestrichener</s> Text mit Zeilen<br>umbruch und einem <a href="https://example.org/seite" target="_blank">Link</a>.</p><ul><li>Äpfel</li><li>Öl</li></ul><ol><li>Eins</li><li>Zwei</li></ol><p><img src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Das Rathaus" loading="lazy"></p>',
  nestedOrderedInBulleted:
    '<ul><li><p>Obere Ebene</p><ol><li><p>Erster Schritt</p></li><li><p>Zweiter Schritt</p></li></ol></li><li><p>Weiter oben</p></li></ul>',
  nestedBulletedInOrdered:
    '<ol><li><p>Erster Punkt</p><ul><li><p>Detail A</p></li><li><p>Detail B</p></li></ul></li><li><p>Zweiter Punkt</p></li></ol>',
  linkBlankWithRel:
    '<p>Mehr unter <a href="https://example.org/info" target="_blank" rel="noopener noreferrer">der Infoseite</a>.</p>',
  linkMailto: '<p>Fragen an <a href="mailto:info@example.org">info@example.org</a>.</p>',
  linkRelativePath: '<p>Siehe <a href="/hilfe/anfahrt">die Anfahrt</a>.</p>',
  imageMediaRoute:
    '<p><img src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Rathaus am Marktplatz" loading="lazy"></p>',
  imageOutsideMediaRoute: '<p>VorherNachher</p>',
  legacyPlainText: 'Erste Zeile\nZweite Zeile\n\nNeuer Absatz mit Größe & Maß',
  legacyServerUnfiltered:
    '<h1>Alter Titel</h1><pre><code>Zeile eins\nZeile zwei</code></pre><p>Ein Aufruf wie <code>ls -l</code> im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>',
  paragraphWithBreak: '<p>Zeile eins<br>Zeile zwei</p>',
  emptyString: '',
  emptyParagraph: '<p></p>',
};

export const EDITOR_091: Record<string, string> = {
  everyKeptElement:
    '<h2>Überschrift</h2><h3>Unterüberschrift</h3><p>Ein <strong>fetter</strong>, <em>kursiver</em>, <u>unterstrichener</u> und <s>durchgestrichener</s> Text mit Zeilen<br>umbruch und einem <a target="_blank" rel="noopener noreferrer" href="https://example.org/seite">Link</a>.</p><ul><li><p>Äpfel</p></li><li><p>Öl</p></li></ul><ol><li><p>Eins</p></li><li><p>Zwei</p></li></ol><p></p><img loading="lazy" src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Das Rathaus"><p></p>',
  nestedOrderedInBulleted:
    '<ul><li><p>Obere Ebene</p><ol><li><p>Erster Schritt</p></li><li><p>Zweiter Schritt</p></li></ol></li><li><p>Weiter oben</p></li></ul>',
  nestedBulletedInOrdered:
    '<ol><li><p>Erster Punkt</p><ul><li><p>Detail A</p></li><li><p>Detail B</p></li></ul></li><li><p>Zweiter Punkt</p></li></ol>',
  linkBlankWithRel:
    '<p>Mehr unter <a target="_blank" rel="noopener noreferrer" href="https://example.org/info">der Infoseite</a>.</p>',
  linkMailto:
    '<p>Fragen an <a target="_blank" rel="noopener noreferrer" href="mailto:info@example.org">info@example.org</a>.</p>',
  linkRelativePath:
    '<p>Siehe <a target="_blank" rel="noopener noreferrer" href="/hilfe/anfahrt">die Anfahrt</a>.</p>',
  imageMediaRoute:
    '<p></p><img loading="lazy" src="/api/orgs/stadt/media/content/rathaus.jpg" alt="Rathaus am Marktplatz"><p></p>',
  imageOutsideMediaRoute:
    '<p>Vorher</p><img loading="lazy" src="https://example.org/pixel.gif" alt="Pixel"><p>Nachher</p>',
  legacyPlainText: '<p>Erste Zeile<br>Zweite Zeile</p><p>Neuer Absatz mit Größe &amp; Maß</p>',
  legacyServerUnfiltered:
    '<p>Alter Titel</p><pre><code>Zeile eins\nZeile zwei</code></pre><p>Ein Aufruf wie <code>ls -l</code> im Text.</p><blockquote><p>Ein Zitat.</p></blockquote><p></p>',
  paragraphWithBreak: '<p>Zeile eins<br>Zeile zwei</p>',
  emptyString: '<p></p>',
  emptyParagraph: '<p></p>',
};
