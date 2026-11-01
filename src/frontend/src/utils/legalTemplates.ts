import i18n from '../i18n';
import type { GeocodeSource } from './geocode';

export type LegalTemplateKind = 'imprint' | 'privacy' | 'accessibility';

/** The language the templates are written in. No other language has a source text. */
const LEGAL_TEMPLATE_LANGUAGE = 'de';

/**
 * The periods a template states. The app knows them, so the template takes
 * them from the settings and the admin does not type them.
 */
export interface LegalTemplateValues {
  /** Retention period in days per deletion class, for example `{ LK_A: 90 }`. */
  retentionDays?: Record<string, number>;
  /** Days a backup of the installation can still hold deleted data. */
  backupDays?: number;
  /** False while the operator has not stated the backup period. */
  backupDaysConfirmed?: boolean;
  /** Longest of the app, error and access log periods, in days. */
  technicalLogDays?: number;
  /** Security log retention period, in days. */
  auditLogDays?: number;
  /**
   * Days after its start, or after its creation when it never started, at
   * which the daily sweep removes the guests of a run that was never ended.
   */
  abandonedRunDays?: number;
  /** Days a published result link stays reachable, restarted on every re-publish. */
  resultLinkDays?: number;
  /**
   * The map services of the installation: the tile server the maps load from
   * and the sources the address search asks. Without a known value the
   * template asserts nothing and leaves bracket placeholders for the tile
   * provider and the search service.
   */
  map?: { tileUrl?: string; sources?: GeocodeSource[] };
}

/** The periods of an installation where nothing is configured. */
const DEFAULT_RETENTION_DAYS: Record<string, number> = { LK_A: 90, LK_B: 90, LK_C: 365, LK_H: 365 };

const BACKUP_PLACEHOLDER = '[DAUER, NACH DER EINE SICHERUNG ÜBERSCHRIEBEN ODER GELÖSCHT WIRD]';
const LOG_TECH_PLACEHOLDER = '[DAUER DER TECHNISCHEN PROTOKOLLE]';
const LOG_AUDIT_PLACEHOLDER = '[DAUER DES SICHERHEITSPROTOKOLLS]';
const ABANDONED_PLACEHOLDER = '[DAUER, NACH DER EIN NIE BEENDETER LAUF VERFÄLLT]';
const RESULT_LINK_PLACEHOLDER = '[DAUER DES ERGEBNIS-LINKS]';

const daysText = (days: number): string => (days === 1 ? '1 Tag' : `${days} Tage`);
const daysPlaceholder = (days: number): string => (days === 1 ? '[1 TAG]' : `[${days} TAGE]`);

/** The host of a tile URL, or '' when the URL is not one. A `{s}.` subdomain placeholder does not count. */
function tileHostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^\{s\}\./, '');
  } catch {
    return '';
  }
}

// The tiles paragraph for tile.openstreetmap.de: FOSSGIS e.V. serves from
// Germany, so no service outside the EU sees anything.
const TILES_FOSSGIS =
  '<p>Wenn du die Kartenansicht der Stationen öffnest, lädt dein Browser Kartenbilder direkt von den Servern des FOSSGIS e.V. mit Sitz in Deutschland. Dabei erfährt der Verein deine IP-Adresse, deinen Browser, die Adresse dieser App und den Kartenausschnitt, den du ansiehst. Deinen Namen, dein Team und deine Antworten erfährt er nicht. Die Kartenbilder werden aus Deutschland ausgeliefert. Eine Übermittlung in ein Drittland findet nicht statt.</p>';

// The tiles paragraphs for tile.openstreetmap.org: the OpenStreetMap
// Foundation and its CDN Fastly, which sits in the USA and so needs a
// statement about the transfer guarantee.
const TILES_OSMF =
  '<p>Wenn du die Kartenansicht der Stationen öffnest, lädt dein Browser Kartenbilder direkt von den Servern der OpenStreetMap Foundation, St John\'s Innovation Centre, Cowley Road, Cambridge CB4 0WS, Vereinigtes Königreich. Dabei erfährt die Foundation deine IP-Adresse, deinen Browser, die Adresse dieser App und den Kartenausschnitt, den du ansiehst. Deinen Namen, dein Team und deine Antworten erfährt sie nicht. Für das Vereinigte Königreich gibt es einen Angemessenheitsbeschluss der EU-Kommission.</p>' +
  '<p>Die Foundation liefert die Kartenbilder über ein weltweites Verteilnetz aus. Zurzeit betreibt es das Unternehmen Fastly mit Sitz in den USA. Auch Fastly erfährt dabei die genannten Angaben. [GARANTIE FÜR DIE ÜBERMITTLUNG IN DIE USA. PRÜFE VOR DER VERÖFFENTLICHUNG, OB FASTLY NACH DEM EU-U.S. DATA PRIVACY FRAMEWORK ZERTIFIZIERT IST, UND NENNE DAS ERGEBNIS HIER.] Mehr dazu steht in der <a href="https://osmfoundation.org/wiki/Privacy_Policy" target="_blank" rel="noopener noreferrer">Datenschutzerklärung der OpenStreetMap Foundation</a>.</p>';

// The tiles paragraph for any other tile server: only the operator can know
// who runs it, so the text states the facts and leaves brackets to fill in.
const TILES_OTHER =
  '<p>Wenn du die Kartenansicht der Stationen öffnest, lädt dein Browser Kartenbilder direkt von den Servern von [NAME DES ANBIETERS DER KARTENBILDER] mit Sitz in [SITZ DES ANBIETERS DER KARTENBILDER]. Dabei erfährt der Anbieter deine IP-Adresse, deinen Browser, die Adresse dieser App und den Kartenausschnitt, den du ansiehst. Deinen Namen, dein Team und deine Antworten erfährt er nicht. [OB DER ANBIETER DATEN IN EIN DRITTLAND ÜBERMITTELT, UND WENN JA, MIT WELCHER GARANTIE.]</p>';

function mapTilesParagraph(tileUrl: string | undefined): string {
  // No URL means the installation did not answer: the unknown-host text with
  // its brackets, never a guess about who serves the tiles.
  const host = tileHostOf(tileUrl?.trim() ?? '');
  if (host === 'tile.openstreetmap.de') return TILES_FOSSGIS;
  if (host === 'tile.openstreetmap.org') return TILES_OSMF;
  return TILES_OTHER;
}

// The address-search paragraphs: the sources list decides whom the search asks.
const SEARCH_INTRO =
  'Wer für uns Rallyes erstellt, sieht beim Anlegen einer Station dieselbe Karte und kann eine Adresse suchen. ';

const SEARCH_LOCAL = `<p>${SEARCH_INTRO}Diese Suche läuft über unseren Server und fragt keinen anderen Dienst. Was die suchende Person eingibt, verlässt unseren Server nicht.</p>`;

// No place index and no outside service: the station editor shows no search
// field (MapPicker), so there is nothing to search with and nobody to ask.
const SEARCH_NONE =
  '<p>Wer für uns Rallyes erstellt, sieht beim Anlegen einer Station dieselbe Karte. Diese Installation bietet keine Adresssuche an: Das Suchfeld erscheint nicht, und die Person setzt den Ort der Station auf der Karte. Es geht keine Suche an einen anderen Dienst.</p>';

// The installation did not answer, so the sources are unknown. Only brackets,
// never the claim that the search asks nobody.
const SEARCH_UNKNOWN = `<p>${SEARCH_INTRO}[OB DIESE SUCHE EINEN DIENST AUSSERHALB UNSERES SERVERS FRAGT, UND WENN JA, WELCHER DIENST DAS IST: NAME, SITZ UND DIE ANGABEN, DIE ER ERHÄLT.]</p>`;

function addressSearchParagraph(sources: GeocodeSource[] | undefined): string {
  if (sources === undefined) return SEARCH_UNKNOWN;
  const service = sources.includes('nominatim') ? 'Nominatim' : sources.includes('photon') ? 'Photon' : null;
  if (!service) return sources.includes('local') ? SEARCH_LOCAL : SEARCH_NONE;
  // The server asks the service when its own index gives fewer than eight hits
  // (routers/geocode.py), and every time when it has no index. Photon also
  // answers while the person types, from three characters on.
  const when = sources.includes('local')
    ? `Findet sein eigenes Ortsverzeichnis weniger als acht Treffer, fragt er zusätzlich den Dienst ${service}. `
    : `Er hat kein eigenes Ortsverzeichnis und fragt den Dienst ${service} bei jeder Suche. ` +
      (service === 'Photon' ? 'Schon beim Tippen fragt er Photon, sobald drei Zeichen stehen. ' : '');
  return (
    `<p>${SEARCH_INTRO}Diese Suche läuft über unseren Server. ${when}` +
    `${service} erfährt dabei den Suchtext oder den Ort auf der Karte, die Sprache der App und die Adresse unseres Servers. ` +
    'Die IP-Adresse und den Browser der Person, die sucht, erfährt er nicht. ' +
    'Der Server nennt sich dem Dienst mit dem Namen der Software, ihrer Version und der Adresse des Projekts. ' +
    '[NUR WENN DER BETREIBER DER INSTALLATION EINEN KONTAKT EINGESTELLT HAT: Dazu schickt er auch diesen Kontakt: KONTAKT DES BETREIBERS.] ' +
    'Betreiber des Dienstes ist [NAME UND SITZ DES BETREIBERS DES DIENSTES].</p>'
  );
}

/**
 * Fills the tokens of a template. The period tokens are `{{LK_A}}`,
 * `{{LK_B}}`, `{{LK_C}}`, `{{LK_H}}`, `{{BACKUP}}`, `{{LOG_TECH}}`,
 * `{{LOG_AUDIT}}`, `{{ABANDONED}}` and `{{RESULT_LINK}}`. The map tokens of
 * section 7 of the privacy template are `{{MAP_TILES}}` and `{{ADDRESS_SEARCH}}`.
 *
 * A known value is written as plain text ("90 Tage"). An unknown value is
 * written as a placeholder in square brackets ("[90 TAGE]"), which the editor
 * reports as open. The backup period is also written as a placeholder while
 * the operator has not stated it: the default is a guess about a backup job
 * that the app does not run. The map tokens state the installation's tile
 * server and address-search sources. While neither is known, they leave
 * bracket placeholders and name no provider.
 */
export function fillLegalTemplate(html: string, values: LegalTemplateValues = {}): string {
  return html.replace(
    /\{\{(LK_[A-Z]|BACKUP|LOG_TECH|LOG_AUDIT|ABANDONED|RESULT_LINK|MAP_TILES|ADDRESS_SEARCH)\}\}/g,
    (_token, name: string) => {
      if (name === 'MAP_TILES') return mapTilesParagraph(values.map?.tileUrl);
      if (name === 'ADDRESS_SEARCH') return addressSearchParagraph(values.map?.sources);
    if (name === 'BACKUP') {
      if (values.backupDays === undefined) return BACKUP_PLACEHOLDER;
      return values.backupDaysConfirmed ? daysText(values.backupDays) : daysPlaceholder(values.backupDays);
    }
    if (name === 'LOG_TECH') {
      return values.technicalLogDays === undefined ? LOG_TECH_PLACEHOLDER : daysText(values.technicalLogDays);
    }
    if (name === 'ABANDONED') {
      return values.abandonedRunDays === undefined ? ABANDONED_PLACEHOLDER : daysText(values.abandonedRunDays);
    }
    if (name === 'RESULT_LINK') {
      return values.resultLinkDays === undefined ? RESULT_LINK_PLACEHOLDER : daysText(values.resultLinkDays);
    }
    if (name === 'LOG_AUDIT') {
      return values.auditLogDays === undefined ? LOG_AUDIT_PLACEHOLDER : daysText(values.auditLogDays);
    }
    const known = values.retentionDays?.[name];
    if (known !== undefined) return daysText(known);
    const fallback = DEFAULT_RETENTION_DAYS[name];
    return fallback !== undefined ? daysPlaceholder(fallback) : `[${name}]`;
  });
}

/**
 * The template for one legal text, as the HTML the editor stores. It is read
 * from the German resources whatever the UI language is, so the English UI
 * offers the German template and no invented translation. `values` fills the
 * periods the app knows.
 */
export function legalTemplate(kind: LegalTemplateKind, values?: LegalTemplateValues): string {
  const value: unknown = i18n.getResource(LEGAL_TEMPLATE_LANGUAGE, 'legalTemplates', kind);
  return typeof value === 'string' ? fillLegalTemplate(value, values) : '';
}
