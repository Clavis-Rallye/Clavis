import { access } from 'node:fs/promises';
import { seedToFile, seedPath } from './seed';

/**
 * Walkthrough global setup: runs the seed once per `playwright test`
 * invocation. With WALK_SEED=<path> pointing at an existing seed.json, the
 * organisation is reused instead (for example the second language run:
 * WALK_LANG=en WALK_SEED=tests/walkthrough/out/seed.json).
 */
export default async function globalSetup(): Promise<void> {
  const reuse = process.env.WALK_SEED;
  if (reuse) {
    const file = seedPath();
    try {
      await access(file);
    } catch {
      throw new Error(`global-setup: WALK_SEED points at ${file}, which does not exist`);
    }
    console.log(`[walkthrough] reusing seed ${file}`);
    return;
  }

  const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost';
  const file = await seedToFile(baseURL);
  console.log(`[walkthrough] seeded new organisation -> ${file}`);
}
