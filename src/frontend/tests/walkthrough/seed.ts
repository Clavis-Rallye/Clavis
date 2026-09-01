import { request } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

/**
 * Walkthrough seed: builds one complete organisation through the API, so the
 * walks have realistic data to look at:
 *
 *   org walk-<timestamp> + org_admin (register)
 *   creator / coordinator / participant users (admin user endpoint)
 *   content languages de+en on the org (settings endpoint, best effort)
 *   "Walkthrough Rallye": 3 stations in Augsburg, 6 challenges (one per type a creator can choose)
 *   published, one started run "Walkthrough Run" with its join code
 *
 * Each run of the seed creates a new organisation and never reuses one. The
 * result goes to tests/walkthrough/out/seed.json. Request shapes come from
 * src/backend/app/routers/ (schemas.py and the routers).
 */

export interface SeedAccount {
  email: string;
  password: string;
  role: 'org_admin' | 'creator' | 'coordinator' | 'participant';
  displayName: string;
}

/** A team the participant walk built, recorded for the reviewer. */
export interface SeedTeam {
  id: string;
  /** Null while the team is still name-pending. */
  name: string | null;
  /** The code a second member types on /team/join. */
  shareCode: string | null;
  /** Which walk built it: the guest journey or the registered participant. */
  createdBy: 'guest' | 'participant';
}

export interface SeedData {
  orgSlug: string;
  accounts: Record<'org_admin' | 'creator' | 'coordinator' | 'participant', SeedAccount>;
  rallyeId: string;
  stationIds: string[];
  challengeIds: Record<string, string>;
  runId: string;
  joinCode: string;
  /**
   * Teams the participant walk appended, so a reviewer finds them on the
   * coordinator dashboard of the run. The seed itself creates none.
   */
  teams?: SeedTeam[];
}

export const WALK_PASSWORD = 'Walk-Pass-1234!';
export const RALLYE_TITLE = 'Walkthrough Rallye';
export const RUN_NAME = 'Walkthrough Run';

export function outDir(): string {
  // Resolved from this file, not the cwd, so the walk and the index builder
  // agree no matter who imports them.
  return path.resolve(path.dirname(new URL(import.meta.url).pathname), 'out');
}

/** Where the walks read seed.json from: WALK_SEED if set, else out/seed.json. */
export function seedPath(): string {
  if (process.env.WALK_SEED) {
    return path.resolve(process.cwd(), process.env.WALK_SEED);
  }
  return path.join(outDir(), 'seed.json');
}

type Json = Record<string, unknown>;

/** POST/PUT helper that treats anything but 2xx as a broken seed. */
async function call(
  ctx: Awaited<ReturnType<typeof request.newContext>>,
  method: 'post' | 'put',
  url: string,
  data?: Json,
): Promise<Json> {
  const resp = await ctx[method](url, { data });
  if (!resp.ok()) {
    throw new Error(`seed: ${method.toUpperCase()} ${url} -> ${resp.status()}: ${await resp.text()}`);
  }
  return (await resp.json()) as Json;
}

function id(v: unknown): string {
  if (typeof v === 'string' && v.length > 0) return v;
  throw new Error(`seed: expected an id, got ${JSON.stringify(v)}`);
}

const STATIONS = [
  {
    title: { de: 'Rathaus', en: 'City Hall' },
    description: {
      de: 'Das Augsburger Rathaus mit seinem Goldenen Saal, eines der bedeutendsten Renaissance-Bauten Deutschlands.',
      en: "Augsburg's city hall with its Golden Hall, one of Germany's most important Renaissance buildings.",
    },
    address: 'Rathausplatz 1, 86150 Augsburg',
    lat: 48.3698,
    lng: 10.8985,
  },
  {
    title: { de: 'Fuggerei', en: 'Fuggerei' },
    description: {
      de: 'Die älteste Sozialsiedlung der Welt, seit 1521 von den Fuggern gestiftet.',
      en: "The world's oldest social housing settlement, endowed by the Fuggers in 1521.",
    },
    address: 'Fuggerei 8, 86152 Augsburg',
    lat: 48.3715,
    lng: 10.902,
  },
  {
    title: { de: 'Dom Mariä Heimsuchung', en: 'Cathedral of the Visitation' },
    description: {
      de: 'Der Augsburger Dom mit den ältesten figürlichen Glasfenstern der Welt.',
      en: "Augsburg's cathedral with the oldest figurative stained-glass windows in the world.",
    },
    address: 'Hoher Weg 1, 86152 Augsburg',
    lat: 48.3733,
    lng: 10.8967,
  },
];

const CHALLENGES: Array<{
  type: string;
  station: number;
  title: Json;
  instructions: Json;
  scoring_rules: Json;
  time_estimate_minutes: number;
  suggested_solution?: string;
}> = [
  {
    type: 'photo',
    station: 0,
    title: { de: 'Fotografiere die Rathaus-Fassade', en: 'Photograph the city hall facade' },
    instructions: {
      de: 'Stellt euch auf den Rathausplatz und fotografiert die komplette Fassade mit den drei Giebeln.',
      en: 'Stand on Rathausplatz and photograph the complete facade with its three gables.',
    },
    scoring_rules: { max_points: 100 },
    time_estimate_minutes: 5,
  },
  {
    type: 'text',
    station: 1,
    title: { de: 'Seit wann gibt es die Fuggerei?', en: 'Since when has the Fuggerei existed?' },
    instructions: {
      de: 'In welchem Jahr wurde die Fuggerei gestiftet? Schreibt das Jahr und einen Satz zur Jahresrente.',
      en: 'In which year was the Fuggerei endowed? Write the year and one sentence about the annual rent.',
    },
    scoring_rules: { max_points: 100 },
    time_estimate_minutes: 5,
    suggested_solution: '1521; die Jahresrente beträgt heute einen Rheinischen Gulden (0,88 Euro).',
  },
  {
    type: 'qr_scan',
    station: 2,
    title: { de: 'Scannt den Dom-Code', en: 'Scan the cathedral code' },
    instructions: {
      de: 'Findet den QR-Code am Informationsschild des Doms und scannt ihn.',
      en: 'Find the QR code on the cathedral information board and scan it.',
    },
    scoring_rules: { max_points: 100, auto_approve: true, expected_value: 'CLAVIS-DOM' },
    time_estimate_minutes: 3,
  },
  {
    type: 'quiz',
    station: 0,
    title: { de: 'Wer ließ den Goldenen Saal errichten?', en: 'Who commissioned the Golden Hall?' },
    instructions: {
      de: 'Welcher Baumeister vollendete 1624 den Goldenen Saal im Augsburger Rathaus?',
      en: 'Which master builder completed the Golden Hall in Augsburg\u2019s city hall in 1624?',
    },
    scoring_rules: {
      max_points: 100,
      auto_approve: true,
      options: ['Elias Holl', 'Jakob Fugger', 'Bertold von Hohenburg', 'Hans Holbein'],
      correct_option: 'Elias Holl',
    },
    time_estimate_minutes: 2,
    suggested_solution: 'Elias Holl.',
  },
  {
    type: 'audio',
    station: 2,
    title: { de: 'Nehmt die Dom-Glocken auf', en: 'Record the cathedral bells' },
    instructions: {
      de: 'Nehmt ein kurzes Audio auf, in dem ihr die Glocken des Doms beschreibt.',
      en: 'Record a short piece of audio in which you describe the cathedral bells.',
    },
    scoring_rules: { max_points: 100 },
    time_estimate_minutes: 5,
  },
  {
    type: 'video',
    station: 2,
    title: { de: 'Video-Statement am Dom', en: 'Video statement at the cathedral' },
    instructions: {
      de: 'Nehmt ein kurzes Video auf, in dem jedes Teammitglied seinen Lieblingsort am Dom nennt.',
      en: 'Record a short video in which every team member names their favourite spot at the cathedral.',
    },
    scoring_rules: { max_points: 100 },
    time_estimate_minutes: 10,
  },
];

/** Seed one organisation end to end and return the recorded data. */
export async function seed(baseURL: string): Promise<SeedData> {
  const ts = Date.now();
  const slug = `walk-${ts}`;
  const accounts: SeedData['accounts'] = {
    org_admin: {
      email: `walk-admin-${ts}@example.com`,
      password: WALK_PASSWORD,
      role: 'org_admin',
      displayName: `Walk Admin ${ts}`,
    },
    creator: {
      email: `walk-creator-${ts}@example.com`,
      password: WALK_PASSWORD,
      role: 'creator',
      displayName: `Walk Creator ${ts}`,
    },
    coordinator: {
      email: `walk-coordinator-${ts}@example.com`,
      password: WALK_PASSWORD,
      role: 'coordinator',
      displayName: `Walk Coordinator ${ts}`,
    },
    participant: {
      email: `walk-participant-${ts}@example.com`,
      password: WALK_PASSWORD,
      role: 'participant',
      displayName: `Walk Participant ${ts}`,
    },
  };

  const admin = await request.newContext({ baseURL });

  // 1. Organisation + its org_admin (bootstrap register; sets access_token).
  await call(admin, 'post', '/api/auth/register', {
    organization_name: `Walkthrough ${ts}`,
    organization_slug: slug,
    email: accounts.org_admin.email,
    display_name: accounts.org_admin.displayName,
    password: accounts.org_admin.password,
  });

  // 2. One user per remaining role.
  for (const key of ['creator', 'coordinator', 'participant'] as const) {
    const account = accounts[key];
    await call(admin, 'post', `/api/orgs/${slug}/users/`, {
      email: account.email,
      display_name: account.displayName,
      password: account.password,
      role: account.role,
    });
  }

  // 3. Content languages de+en — best effort, the org works without it.
  try {
    await call(admin, 'put', `/api/orgs/${slug}`, { languages: ['de', 'en'] });
  } catch {
    // Settings endpoint declined the update; the walk still works.
  }

  // 4. Rallye with DE+EN title/description.
  const rallye = await call(admin, 'post', `/api/orgs/${slug}/rallyes/`, {
    title: { de: RALLYE_TITLE, en: 'Walkthrough Rallye (EN)' },
    description: {
      de: 'Eine Demonstrations-Rallye durch die Augsburger Innenstadt für den Walkthrough.',
      en: 'A demonstration rallye through Augsburg\u2019s old town for the walkthrough.',
    },
  });
  const rallyeId = id(rallye.id);

  // 5. Stations (address + coordinates in Augsburg + tips popup), linked into
  //    the rallye in order. The challenges below attach to the link id of the import.
  const linkIds: string[] = [];
  const stationIds: string[] = [];
  for (const [index, station] of STATIONS.entries()) {
    const created = await call(admin, 'post', `/api/orgs/${slug}/stations/`, {
      title: station.title,
      description: station.description,
      address: station.address,
      location_lat: station.lat,
      location_lng: station.lng,
      tips: {
        header: { de: `Tipps zu: ${station.title.de}`, en: `Tips for: ${station.title.en}` },
        links: [
          {
            label: { de: 'Integreat-Artikel', en: 'Integreat article' },
            url: 'https://integreat.app/augsburg/de',
          },
          {
            label: { de: 'Stadt Augsburg', en: 'City of Augsburg' },
            url: 'https://www.augsburg.de',
          },
        ],
      },
    });
    stationIds.push(id(created.id));
    const linked = await call(admin, 'post', `/api/orgs/${slug}/rallyes/${rallyeId}/stations/import`, {
      station_id: created.id,
      sort_order: index,
    });
    linkIds.push(id(linked.link_id));
  }

  // 6. One library challenge per type, attached to its station.
  const challengeIds: Record<string, string> = {};
  for (const challenge of CHALLENGES) {
    const created = await call(admin, 'post', `/api/orgs/${slug}/challenges/`, {
      challenge_type: challenge.type,
      title: challenge.title,
      instructions: challenge.instructions,
      scoring_rules: challenge.scoring_rules,
      time_estimate_minutes: challenge.time_estimate_minutes,
      ...(challenge.suggested_solution ? { suggested_solution: challenge.suggested_solution } : {}),
    });
    challengeIds[challenge.type] = id(created.id);
    await call(
      admin,
      'post',
      `/api/orgs/${slug}/rallyes/${rallyeId}/stations/${linkIds[challenge.station]}/challenges/import`,
      { challenge_id: created.id },
    );
  }

  // 7. Publish.
  await call(admin, 'put', `/api/orgs/${slug}/rallyes/${rallyeId}`, { is_published: true });

  // 8. Run as the coordinator: login (own context for the cookie), create,
  //    start. The join code is on the run response.
  const coordinator = await request.newContext({ baseURL });
  const login = await call(coordinator, 'post', '/api/auth/login', {
    email: accounts.coordinator.email,
    password: accounts.coordinator.password,
  });
  if (login.status !== 'success') {
    throw new Error(`seed: coordinator login returned status ${JSON.stringify(login.status)}`);
  }
  const run = await call(coordinator, 'post', `/api/orgs/${slug}/runs/`, {
    rallye_id: rallyeId,
    name: RUN_NAME,
  });
  const runId = id(run.id);
  const joinCode = id(run.join_code);
  await call(coordinator, 'post', `/api/orgs/${slug}/runs/${runId}/start`);

  await admin.dispose();
  await coordinator.dispose();

  return { orgSlug: slug, accounts, rallyeId, stationIds, challengeIds, runId, joinCode };
}

/**
 * Record the teams a walk built into the seed file the walks read.
 *
 * Read-modify-write: global-setup wrote the file before the walk started, and
 * a reused WALK_SEED file may already carry teams from an earlier language
 * run. A team is matched by id, so a second run over the same seed updates
 * its entry (the name arrives one step after the id) instead of doubling it.
 */
export async function recordTeams(teams: SeedTeam[]): Promise<void> {
  const file = seedPath();
  const data = JSON.parse(await readFile(file, 'utf8')) as SeedData;
  const merged = [...(data.teams ?? [])];
  for (const team of teams) {
    const at = merged.findIndex((known) => known.id === team.id);
    if (at >= 0) merged[at] = team;
    else merged.push(team);
  }
  await writeFile(file, `${JSON.stringify({ ...data, teams: merged }, null, 2)}\n`, 'utf8');
}

/** Seed and write out/seed.json, never the WALK_SEED path. Returns the path written. */
export async function seedToFile(baseURL: string): Promise<string> {
  const data = await seed(baseURL);
  await mkdir(outDir(), { recursive: true });
  const target = path.join(outDir(), 'seed.json');
  await writeFile(target, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
  return target;
}
