import { describe, it, expect, afterEach } from 'vitest';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import i18n from '../i18n';
import { fillLegalTemplate, legalTemplate, type LegalTemplateKind, type LegalTemplateValues } from './legalTemplates';
import type { GeocodeSource } from './geocode';
import { findLegalPlaceholders } from './legalPlaceholders';

const KINDS: LegalTemplateKind[] = ['imprint', 'privacy', 'accessibility'];

// The allow-list of the server-side sanitiser (backend `services/richtext.py`).
// A template that stays inside it comes back from a save unchanged.
const ALLOWED_TAGS = new Set(['p', 'br', 'strong', 'em', 'u', 's', 'h2', 'h3', 'ul', 'ol', 'li', 'a', 'img']);
// The limit of one legal text in the API schema.
const MAX_LENGTH = 20000;

describe('legal templates', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de');
  });

  it.each(KINDS)('ships a template for %s that fits the stored field', (kind) => {
    const html = legalTemplate(kind);
    expect(html.length).toBeGreaterThan(500);
    expect(html.length).toBeLessThan(MAX_LENGTH);
  });

  it.each(KINDS)('keeps the template for %s inside the sanitiser allow-list', (kind) => {
    const html = legalTemplate(kind);
    const tags = [...html.matchAll(/<\/?([a-z0-9]+)((?:\s[^>]*)?)>/g)];
    expect(tags.length).toBeGreaterThan(0);
    for (const [whole, tag, attributes] of tags) {
      expect(ALLOWED_TAGS, whole).toContain(tag);
      if (tag === 'a' && !whole.startsWith('</')) {
        expect(attributes).toMatch(/^ href="https:\/\/[^"]+" target="_blank" rel="noopener noreferrer"$/);
      } else {
        expect(attributes, whole).toBe('');
      }
    }
    // No comment, no entity the sanitiser would rewrite, and no page title:
    // the page renders the title itself, so the stored text starts below it.
    expect(html).not.toContain('<!--');
    expect(html).not.toContain('<h1');
    expect(html).not.toMatch(/&(?!amp;)/);
  });

  it.each(KINDS)('keeps the template for %s in the form the editor writes', (kind) => {
    const html = legalTemplate(kind);
    const editor = new Editor({
      extensions: [
        StarterKit.configure({ heading: { levels: [2, 3] }, link: false }),
        Link.configure({ HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' } }),
      ],
      content: html,
    });
    const written = editor.getHTML();
    editor.destroy();
    // The editor reads the template and writes the same document back. So an
    // insert followed by an edit changes only what the user edits. The one
    // difference is the order of the link attributes: the template keeps the
    // order of the server-side sanitiser, the editor writes `href` last.
    const inSanitiserOrder = written.replace(
      /<a target="_blank" rel="noopener noreferrer" href="([^"]+)">/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">',
    );
    expect(inSanitiserOrder === html).toBe(true);
  });

  it.each(KINDS)('leaves placeholders in the template for %s', (kind) => {
    expect(findLegalPlaceholders(legalTemplate(kind)).length).toBeGreaterThan(0);
  });

  it('keeps the placeholders exactly as the source text writes them', () => {
    expect(findLegalPlaceholders(legalTemplate('imprint'))).toContain('[NAME DER ORGANISATION]');
    expect(findLegalPlaceholders(legalTemplate('privacy'))).toEqual(
      expect.arrayContaining(['[E-MAIL-ADRESSE]', '[90 TAGE]', '[365 TAGE]', '[DATUM]']),
    );
    expect(findLegalPlaceholders(legalTemplate('accessibility'))).toContain(
      '[FRIST, HÖCHSTENS SECHS WOCHEN]',
    );
  });

  it('states the periods the app knows, without a placeholder', () => {
    const html = legalTemplate('privacy', {
      retentionDays: { LK_A: 60, LK_B: 120, LK_C: 400, LK_H: 180 },
      backupDays: 365,
      backupDaysConfirmed: true,
      technicalLogDays: 120,
      auditLogDays: 730,
    });
    expect(html).toContain('<strong>Gastkonten:</strong> 60 Tage nach dem Ende des Laufs');
    expect(html).toContain('Chat-Nachrichten:</strong> 120 Tage nach dem Ende des Laufs');
    expect(html).toContain('Mitgliederliste der Teams:</strong> 400 Tage nach dem Ende des Laufs');
    expect(html).toContain('Rückmeldung:</strong> 180 Tage nach dem Ende des Laufs löscht die App sie');
    expect(html).toContain('<strong>Datensicherungen:</strong> 365 Tage. So lange');
    expect(html).toContain('<strong>Technische Protokolle:</strong> höchstens 120 Tage.');
    expect(html).toContain('<strong>Sicherheitsprotokoll:</strong> 730 Tage.');
    expect(html).not.toContain('{{');
    const open = findLegalPlaceholders(html);
    expect(open).not.toContain('[90 TAGE]');
    expect(open.some((p) => p.includes('SICHERUNG'))).toBe(false);
  });

  it('takes the log periods from the values and not from fixed text', () => {
    // Without the fixed "90 Tage" / "365 Tage" of the old section 8 the two
    // log lines answer only to the tokens.
    const html = legalTemplate('privacy', { technicalLogDays: 45, auditLogDays: 400 });
    expect(html).toContain('<strong>Technische Protokolle:</strong> höchstens 45 Tage.');
    expect(html).toContain('<strong>Sicherheitsprotokoll:</strong> 400 Tage.');
  });

  it('keeps the backup period a placeholder until the operator states it', () => {
    const html = legalTemplate('privacy', { backupDays: 30, backupDaysConfirmed: false });
    expect(html).toContain('<strong>Datensicherungen:</strong> [30 TAGE]. So lange');
    expect(findLegalPlaceholders(html)).toContain('[30 TAGE]');
  });

  it('writes every unknown period as a placeholder', () => {
    const html = legalTemplate('privacy');
    expect(html).not.toContain('{{');
    expect(findLegalPlaceholders(html)).toEqual(
      expect.arrayContaining([
        '[90 TAGE]',
        '[365 TAGE]',
        '[DAUER, NACH DER EINE SICHERUNG ÜBERSCHRIEBEN ODER GELÖSCHT WIRD]',
      ]),
    );
  });

  it('writes the log periods as bracket placeholders until the settings arrive', () => {
    expect(findLegalPlaceholders(legalTemplate('privacy'))).toEqual(
      expect.arrayContaining(['[DAUER DER TECHNISCHEN PROTOKOLLE]', '[DAUER DES SICHERHEITSPROTOKOLLS]']),
    );
    expect(findLegalPlaceholders(fillLegalTemplate('{{LOG_TECH}} {{LOG_AUDIT}}'))).toEqual([
      '[DAUER DER TECHNISCHEN PROTOKOLLE]',
      '[DAUER DES SICHERHEITSPROTOKOLLS]',
    ]);
  });

  it('says in section 8 why the security log outlives a deleted account', () => {
    const html = legalTemplate('privacy', { technicalLogDays: 90, auditLogDays: 365 });
    // The paragraph on the security log: what stays, until when, why, on which ground.
    expect(html).toContain('Die Einträge des Sicherheitsprotokolls über dich bleiben dann noch bis zum Ende der Frist');
    expect(html).toContain('Sie behalten deinen Anzeigenamen, deine E-Mail-Adresse und deine IP-Adresse.');
    expect(html).toContain('um Missbrauch und Angriffe aufzuklären und nachzuweisen, wer welche Daten geändert hat');
    expect(html).toContain('Art. 17 Abs. 3 Buchstabe e DSGVO');
  });

  it('no longer says that names and answer text stay for ever', () => {
    const html = legalTemplate('privacy');
    expect(html).not.toContain('Die App löscht sie nicht automatisch');
    expect(html).not.toContain('bleibt danach in der Mitgliederliste deines Teams stehen');
  });

  it('fills a singular day and leaves other braces alone', () => {
    expect(fillLegalTemplate('{{LK_A}} / {{BACKUP}} / {{name}}', { retentionDays: { LK_A: 1 }, backupDays: 1 })).toBe(
      '1 Tag / [1 TAG] / {{name}}',
    );
  });

  it('offers the German template in the English UI too', async () => {
    const german = legalTemplate('imprint');
    await i18n.changeLanguage('en');
    expect(legalTemplate('imprint')).toBe(german);
    expect(german).toContain('<h2>Anbieter</h2>');
  });

  describe('the templates state what the code does', () => {
    // Every value the installation knows, so a placeholder left in the text is one only the operator can fill.
    const KNOWN = {
      retentionDays: { LK_A: 60, LK_B: 120, LK_C: 400, LK_H: 180 },
      backupDays: 365,
      backupDaysConfirmed: true,
      technicalLogDays: 120,
      auditLogDays: 730,
      abandonedRunDays: 45,
      resultLinkDays: 30,
      map: { tileUrl: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png', sources: ['local', 'nominatim'] as GeocodeSource[] },
    };
    const privacy = (values: LegalTemplateValues = KNOWN) => legalTemplate('privacy', values);
    const textOf = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

    it('sweeps a run that never ends after the stated days from its start or creation', () => {
      const html = privacy();
      expect(html).toContain('löscht die App die Gastkonten 45 Tage nach dem Start des Laufs');
      expect(html).toContain('nach seiner Anlage');
      expect(html).not.toContain('ohne Aktivität');
      expect(html).not.toContain('nach 60 Tagen');
      // No value: a placeholder in square brackets, never a guess.
      const unknown = privacy({});
      expect(unknown).toContain('[DAUER, NACH DER EIN NIE BEENDETER LAUF VERFÄLLT] nach dem Start des Laufs');
      expect(findLegalPlaceholders(unknown)).toContain('[DAUER, NACH DER EIN NIE BEENDETER LAUF VERFÄLLT]');
      expect(fillLegalTemplate('{{ABANDONED}}', { abandonedRunDays: 1 })).toBe('1 Tag');
    });

    it('states the log periods as settings, the cleanup job, and the recipient address in the log', () => {
      const html = privacy();
      expect(html).toContain('<strong>Technische Protokolle:</strong> höchstens 120 Tage.');
      expect(html).toContain('Die Fristen der Protokolle sind Einstellungen dieser Installation.');
      expect(html).toContain('Die Dateien löscht das Aufräum-Skript, das der Betreiber einrichtet, nach ihrem Alter.');
      // logging.py rotates the files with a count of old files, so the app does delete some.
      expect(html).not.toContain('Die App löscht sie nicht');
      expect(html).toContain('Das technische Protokoll kann die E-Mail-Adresse der Person enthalten, an die die App eine E-Mail schickt.');
    });

    it('states the browser cache per kind as the code keeps it, and what logout removes', () => {
      const text = textOf(privacy());
      expect(text).toContain('Antworten des Servers, zum Beispiel Läufe, Stationen und Mitteilungen: höchstens 24 Stunden und höchstens 50 Einträge.');
      // Precached files (vite.config.ts globPatterns) have no period by time; only the runtime rules do.
      expect(text).toContain(
        'Die Dateien der App selbst legt der Browser beim ersten Aufruf ab: das Grundgerüst, die Stile, die Schriften und die Bilder der App. Für sie gilt keine Frist nach Zeit. Sie bleiben, bis eine neue Version der App sie ersetzt.',
      );
      expect(text).toContain('Was der Browser während der Nutzung nachlädt, behält er für diese Zeit:');
      expect(text).toContain('Nachgeladene Programmteile der App: 30 Tage und höchstens 100 Dateien.');
      expect(text).toContain('Bilder, die die App nachlädt: 30 Tage und höchstens 100 Bilder.');
      expect(text).toContain('Weitere Programmdateien, Stile und Schriften: 7 Tage und höchstens 60 Dateien.');
      expect(text).toContain('Kartenbilder: 7 Tage und höchstens 500 Bilder.');
      expect(text).toContain('Die Seiten, die du aufgerufen hast: ohne feste Frist. Ein neuer Aufruf ersetzt sie.');
      expect(text).not.toContain('Nachgeladene Programmteile und Bilder der App: 30 Tage');
      expect(text).not.toContain('Andere Programmdateien, Stile und Schriften: 7 Tage');
      expect(text).not.toContain('Das Grundgerüst der App und die Seiten, die du aufgerufen hast');
      expect(text).toContain('Nie zwischengespeichert werden');
      expect(text).toContain(
        'Beim Abmelden löscht die App die zwischengespeicherten Antworten des Servers und den Einrichtungsschlüssel der Anmeldung in zwei Schritten.',
      );
      expect(text).not.toContain('für höchstens 24 Stunden zwischen');
    });

    it('names the outside search service, the eight-hit rule, the contact and a placeholder for its operator', () => {
      const html = privacy();
      expect(html).toContain('Findet sein eigenes Ortsverzeichnis weniger als acht Treffer, fragt er zusätzlich den Dienst Nominatim.');
      expect(html).toContain('Der Server nennt sich dem Dienst mit dem Namen der Software, ihrer Version und der Adresse des Projekts.');
      expect(html).toContain('[NUR WENN DER BETREIBER DER INSTALLATION EINEN KONTAKT EINGESTELLT HAT:');
      expect(html).toContain('[NAME UND SITZ DES BETREIBERS DES DIENSTES]');
      expect(html).not.toContain('Findet er die Adresse nicht in seinem eigenen Ortsverzeichnis');
      // Without an index of its own the server asks on every search, and Photon already while typing.
      const photon = privacy({ map: { sources: ['photon'] } });
      expect(photon).toContain('Er hat kein eigenes Ortsverzeichnis und fragt den Dienst Photon bei jeder Suche.');
      expect(photon).toContain('Schon beim Tippen fragt er Photon, sobald drei Zeichen stehen.');
    });

    it('gives a station editor without a search field its own short paragraph', () => {
      const none = privacy({ map: { sources: [] } });
      expect(none).toContain('Diese Installation bietet keine Adresssuche an: Das Suchfeld erscheint nicht');
      expect(none).not.toContain('Diese Suche läuft über unseren Server');
    });

    it('says the evaluation of runs shows team names, points and times per team', () => {
      const html = privacy();
      expect(html).toContain('Die Auswertung eines Laufs zeigt Teamnamen, Punkte und Zeiten je Team.');
      expect(html).not.toContain('Zahlen ohne Namen');
    });

    it('names the Spielleitung, the Erstellung and the Verwaltung where answers and recordings are seen', () => {
      const who = 'die Spielleitung, die Erstellung und die Verwaltung';
      const html = textOf(privacy());
      expect(html).toContain('Die Spielleitung, die Erstellung und die Verwaltung unserer Organisation sehen');
      expect(html).toContain(`Deine Aufnahmen sehen dein Team sowie ${who} unserer Organisation in der App.`);
      expect(html).not.toContain('nur dein Team und die Personen');
      expect(html).not.toContain('Personen, die den Lauf für uns betreuen');
      const imprint = legalTemplate('imprint');
      expect(imprint).toContain(`sowie ${'die Spielleitung, die Erstellung und die Verwaltung'} von [NAME DER ORGANISATION] in der App.`);
      expect(imprint).not.toContain('Personen, die den Lauf');
    });

    it('names the security data of an account', () => {
      const html = privacy();
      expect(html).toContain('Den Prüfwert (Hash) deines vorherigen Passworts.');
      expect(html).toContain('Nach zu vielen Fehlversuchen speichert die App, bis wann dein Konto gesperrt ist.');
      expect(html).not.toContain('Sicherheitsschlüssel');
      expect(html).not.toContain('öffentlichen Schlüssel');
    });

    it('names the join steps, the sidebar, the second step and the operator cookie', () => {
      const text = textOf(privacy());
      expect(text).toContain('die Schritte beim Beitritt: dein Anzeigename, die Organisation, der Code des Laufs und der Stand der Teambildung');
      expect(text).toContain('die Einstellung der Seitenleiste');
      expect(text).toContain('die kurzlebigen Daten der Anmeldung in zwei Schritten');
      expect(text).toContain('Es heißt „instance_token“');
    });

    it('says what the result link shows and takes its period from the installation', () => {
      const html = privacy();
      expect(html).toContain('die Teamnamen, die Punkte, den Platz, den Sieger, den Namen des Laufs und sein Ende');
      expect(html).toContain('Er bleibt 30 Tage nach der Veröffentlichung erreichbar. Veröffentlichen wir ihn erneut, beginnt die Frist neu.');
      expect(html).toContain('<strong>Ergebnis-Links:</strong> 30 Tage nach der Veröffentlichung. Bei einer neuen Veröffentlichung beginnt die Frist neu.');
      expect(html).not.toContain('Er zeigt nur Teamnamen, Punkte und Platz');
      const unknown = privacy({});
      expect(unknown).toContain('Er bleibt [DAUER DES ERGEBNIS-LINKS] nach der Veröffentlichung erreichbar.');
      expect(findLegalPlaceholders(unknown)).toContain('[DAUER DES ERGEBNIS-LINKS]');
    });

    it('says what the download holds and that other data is requested', () => {
      const text = textOf(privacy());
      expect(text).toContain('Die Datei enthält dein Konto (E-Mail-Adresse, Anzeigename, Rolle, Anlage und letzte Anmeldung)');
      expect(text).toContain('Fotos, Videos und Tonaufnahmen sind nicht darin, nur Verweise auf sie.');
      expect(text).toContain('Andere Daten fragst du bei uns an.');
    });

    it('no longer claims that every page is checked automatically at every change', () => {
      const html = legalTemplate('accessibility');
      expect(html).not.toContain('automatische Prüfung aller Seiten mit axe-core, die bei jeder Änderung');
      expect(html).not.toContain('11.07.2026');
      expect(html).toContain('[DATUM DER LETZTEN VOLLEN PRÜFUNG]');
      expect(html).toContain('Bei jeder Änderung der Software läuft die automatische Prüfung mit axe-core für einzelne Bausteine der App');
      expect(html).toContain('Alle Seiten prüft das Projekt nur bei der vollen Prüfung.');
    });

    it('keeps the form of the template — du form, short sentences, the role names of the app', () => {
      for (const kind of KINDS) {
        const html = legalTemplate(kind, KNOWN);
        const text = textOf(html).replace(/\[[^\]]*\]/g, ' ');
        expect(text, kind).not.toMatch(/(?:[,.:;] |^)Sie (?:können|sind|haben|müssen)\b|\bIhr(?:e|en|em|er|es)?\b/);
        expect(text, kind).not.toMatch(/Koordinator|Coordinator|Creator|Org-?Admin|Administrator/);
        // A paragraph, a list item or a heading ends a sentence, whatever its last character is.
        for (const block of html.split(/<\/(?:p|li|h2|h3)>/)) {
          for (const sentence of textOf(block).replace(/\[[^\]]*\]/g, ' ').split(/(?<=[.!?])\s+/)) {
            expect(sentence.split(' ').length, sentence).toBeLessThanOrEqual(40);
          }
        }
      }
    });

    it('P16: carries the test feedback module as one bracket the operator keeps or drops', () => {
      const brackets = findLegalPlaceholders(privacy()).filter((placeholder) => placeholder.includes('RÜCKMELDELEISTE'));
      expect(brackets).toHaveLength(1);
      expect(brackets[0]).toContain('Sie wird nach der Frist der Installation gelöscht, standardmäßig nach 30 Tagen.');
      expect(brackets[0]).toContain('Eine Notiz enthält kein Konto, keine E-Mail-Adresse und keine IP-Adresse.');
    });

    it('leaves no open placeholder for a value the installation knows', () => {
      const html = privacy();
      expect(html).not.toContain('{{');
      const open = findLegalPlaceholders(html);
      expect(open.filter((placeholder) => /TAGE?\]|DAUER|SICHERUNG|ANBIETERS DER KARTENBILDER/.test(placeholder))).toEqual([]);
    });
  });

  describe('section 7 follows the tile server', () => {
    const withMap = (tileUrl: string) => legalTemplate('privacy', { map: { tileUrl } });

    it('names FOSSGIS e.V. in Germany for tile.openstreetmap.de, without Fastly', () => {
      const html = withMap('https://tile.openstreetmap.de/{z}/{x}/{y}.png');
      expect(html).toContain('FOSSGIS e.V.');
      expect(html).toContain('mit Sitz in Deutschland');
      expect(html).toContain('Eine Übermittlung in ein Drittland findet nicht statt');
      expect(html).not.toContain('Fastly');
      expect(html).not.toContain('OpenStreetMap Foundation');
    });

    it('asserts no provider while the tile server is unknown — no FOSSGIS default', () => {
      // The page disables the insert until the config arrives; if it is
      // inserted anyway, the text leaves brackets instead of guessing.
      const html = legalTemplate('privacy');
      expect(html).toContain('[NAME DES ANBIETERS DER KARTENBILDER]');
      expect(html).not.toContain('FOSSGIS');
      expect(html).not.toContain('Fastly');
    });

    it('keeps the OSMF and Fastly passages for tile.openstreetmap.org', () => {
      const html = withMap('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png');
      expect(html).toContain('OpenStreetMap Foundation');
      expect(html).toContain('Fastly');
      expect(html).toContain(
        '[GARANTIE FÜR DIE ÜBERMITTLUNG IN DIE USA. PRÜFE VOR DER VERÖFFENTLICHUNG, OB FASTLY NACH DEM EU-U.S. DATA PRIVACY FRAMEWORK ZERTIFIZIERT IST, UND NENNE DAS ERGEBNIS HIER.]',
      );
      expect(html).toContain('https://osmfoundation.org/wiki/Privacy_Policy');
      expect(html).not.toContain('FOSSGIS');
    });

    it('gives bracket placeholders for name, seat and transfer for any other host', () => {
      const html = withMap('https://tiles.beispielstadt.de/karten/{z}/{x}/{y}.png');
      expect(html).toContain('[NAME DES ANBIETERS DER KARTENBILDER]');
      expect(html).toContain('[SITZ DES ANBIETERS DER KARTENBILDER]');
      expect(html).toContain('[OB DER ANBIETER DATEN IN EIN DRITTLAND ÜBERMITTELT');
      expect(html).not.toContain('Fastly');
      expect(html).not.toContain('FOSSGIS');
    });

    it('keeps the list view, the station address and the device map paragraphs', () => {
      const html = withMap('https://tiles.beispielstadt.de/karten/{z}/{x}/{y}.png');
      expect(html).toContain('Die Listenansicht der Stationen kommt ohne Karte aus.');
      expect(html).toContain('Wenn du auf die Adresse einer Station tippst, öffnet dein Gerät seine eigene Karten-App.');
      expect(html).toContain('Was die Karten-App danach verarbeitet, bestimmt ihr Anbieter.');
    });
  });

  describe('section 7 follows the address-search sources', () => {
    it('says the search runs on our server and asks nobody else for the place index alone', () => {
      const html = legalTemplate('privacy', { map: { sources: ['local'] } });
      expect(html).toContain('Diese Suche läuft über unseren Server und fragt keinen anderen Dienst.');
      expect(html).toContain('verlässt unseren Server nicht.');
      expect(html).not.toContain('Nominatim');
      expect(html).not.toContain('Photon');
    });

    it('offers no search to state when the installation has neither an index nor an outside service', () => {
      const html = legalTemplate('privacy', { map: { sources: [] } });
      expect(html).toContain('Das Suchfeld erscheint nicht');
      expect(html).not.toContain('fragt keinen anderen Dienst');
      expect(html).not.toContain('Nominatim');
      expect(html).not.toContain('Photon');
    });

    it('asserts no source while the address-search sources are unknown', () => {
      const html = legalTemplate('privacy');
      expect(html).toContain('[OB DIESE SUCHE EINEN DIENST AUSSERHALB UNSERES SERVERS FRAGT');
      expect(html).not.toContain('fragt keinen anderen Dienst');
      expect(html).not.toContain('Nominatim');
      expect(html).not.toContain('Photon');
    });

    it('names Nominatim when the installation asks it', () => {
      const html = legalTemplate('privacy', { map: { sources: ['local', 'nominatim'] } });
      expect(html).toContain('fragt er zusätzlich den Dienst Nominatim.');
      expect(html).toContain('Nominatim erfährt dabei den Suchtext oder den Ort auf der Karte');
      expect(html).not.toContain('Photon');
    });

    it('names Photon when the installation asks it', () => {
      const html = legalTemplate('privacy', { map: { sources: ['local', 'photon'] } });
      expect(html).toContain('fragt er zusätzlich den Dienst Photon.');
      expect(html).toContain('Photon erfährt dabei den Suchtext oder den Ort auf der Karte');
      expect(html).not.toContain('Nominatim');
    });
  });
});
