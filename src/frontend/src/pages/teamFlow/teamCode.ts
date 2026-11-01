/** What a team code looks like: four to six letters or digits. The server
 *  refuses anything longer ("Höchstens 6 Zeichen erlaubt"). */
const TEAM_CODE = /^[A-Z0-9]{4,6}$/;

/**
 * The team code inside a scanned QR text, or null when the text holds none.
 *
 * A team's QR code is its join link with `?team=<code>`, and a bare code
 * also counts. Any other QR code, for example the one of a QR task or a web
 * address, is not a team code and is not written into the field.
 */
export function extractTeamCode(text: string): string | null {
  let candidate = text.trim();
  try {
    const teamParam = new URL(candidate).searchParams.get('team');
    if (teamParam === null) return null; // a link, but not a team's link
    candidate = teamParam;
  } catch {
    // not a URL — a bare code
  }
  candidate = candidate.trim().toUpperCase();
  return TEAM_CODE.test(candidate) ? candidate : null;
}
