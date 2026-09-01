import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

/**
 * Walkthrough index builder: rewrites tests/walkthrough/out/index.html from
 * whatever the walks captured: one row per capture with persona, viewport,
 * language, step, the viewport.png thumbnail, axe violations by impact, the
 * small-target count, the overflow flag and the console error count.
 *
 * Static page, no framework, inline CSS only. Run via `npm run walkthrough:index`
 * (plain node: Node ≥ 23.6 strips the types on the fly, so tsx is not needed).
 */

interface Metrics {
  smallTargetCount?: number;
  horizontalOverflow?: boolean;
}

interface ConsoleJson {
  errors?: unknown[];
}

interface AxeViolation {
  impact?: string;
}

interface CaptureRow {
  project: string;
  lang: string;
  persona: string;
  step: string;
  relDir: string;
  metrics: Metrics;
  errorCount: number;
  violationsByImpact: Record<string, number>;
}

function outDir(): string {
  return path.resolve(path.dirname(new URL(import.meta.url).pathname), 'out');
}

async function readJson(file: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(file, 'utf8'));
  } catch {
    return undefined;
  }
}

/** All capture folders: out/<project>/<lang>/<persona>/<NN>-<step>/ */
async function collectCaptures(out: string): Promise<CaptureRow[]> {
  const rows: CaptureRow[] = [];
  for (const project of await readdir(out, { withFileTypes: true })) {
    if (!project.isDirectory()) continue;
    const projectDir = path.join(out, project.name);
    for (const lang of await readdir(projectDir, { withFileTypes: true })) {
      if (!lang.isDirectory()) continue;
      const langDir = path.join(projectDir, lang.name);
      for (const persona of await readdir(langDir, { withFileTypes: true })) {
        if (!persona.isDirectory()) continue;
        const personaDir = path.join(langDir, persona.name);
        for (const entry of await readdir(personaDir, { withFileTypes: true })) {
          if (!entry.isDirectory() || !/^\d{2}-/.test(entry.name)) continue;
          const dir = path.join(personaDir, entry.name);
          const metrics = ((await readJson(path.join(dir, 'metrics.json'))) ?? {}) as Metrics;
          const consoleJson = ((await readJson(path.join(dir, 'console.json'))) ?? {}) as ConsoleJson;
          const axe = (await readJson(path.join(dir, 'axe.json'))) as
            | { violations?: AxeViolation[] }
            | undefined;
          const violationsByImpact: Record<string, number> = {};
          for (const violation of axe?.violations ?? []) {
            const impact = violation.impact ?? 'unknown';
            violationsByImpact[impact] = (violationsByImpact[impact] ?? 0) + 1;
          }
          rows.push({
            project: project.name,
            lang: lang.name,
            persona: persona.name,
            step: entry.name,
            relDir: path.relative(out, dir),
            metrics,
            errorCount: consoleJson.errors?.length ?? 0,
            violationsByImpact,
          });
        }
      }
    }
  }
  rows.sort((a, b) =>
    [a.project, a.lang, a.persona, a.step].join('/').localeCompare([b.project, b.lang, b.persona, b.step].join('/')),
  );
  return rows;
}

function esc(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function render(rows: CaptureRow[], generatedAt: string): string {
  const body = rows
    .map((row) => {
      const impacts = Object.entries(row.violationsByImpact)
        .map(([impact, count]) => `${impact}: ${count}`)
        .join(', ');
      const thumbnail = path.join(row.relDir, 'viewport.png');
      return `      <tr>
        <td><a href="${esc(thumbnail)}"><img class="thumb" src="${esc(thumbnail)}" alt="${esc(row.step)}"></a></td>
        <td>${esc(row.persona)}</td>
        <td>${esc(row.project)}</td>
        <td>${esc(row.lang)}</td>
        <td>${esc(row.step)}</td>
        <td>${esc(impacts || '—')}</td>
        <td class="num">${row.metrics.smallTargetCount ?? '—'}</td>
        <td>${row.metrics.horizontalOverflow ? '⚠ yes' : 'no'}</td>
        <td class="num">${row.errorCount}</td>
      </tr>`;
    })
    .join('\n');
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clavis walkthrough — captures</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 1rem auto 3rem; max-width: 1200px; color: #1a1a1a; }
  h1 { font-size: 1.25rem; }
  p.meta { color: #666; font-size: 0.85rem; }
  table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
  th, td { border-bottom: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; vertical-align: top; }
  th { position: sticky; top: 0; background: #fff; border-bottom: 2px solid #999; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .thumb { width: 120px; display: block; border: 1px solid #ccc; border-radius: 4px; }
  tr:hover { background: #f6f8fa; }
</style>
</head>
<body>
<h1>Clavis walkthrough captures</h1>
<p class="meta">${rows.length} captures, generated ${esc(generatedAt)}.</p>
<table>
  <thead>
    <tr><th>viewport.png</th><th>persona</th><th>viewport</th><th>lang</th><th>step</th><th>axe violations (impact)</th><th>small targets</th><th>h-overflow</th><th>console errors</th></tr>
  </thead>
  <tbody>
${body}
  </tbody>
</table>
</body>
</html>
`;
}

async function main(): Promise<void> {
  const out = outDir();
  const rows = await collectCaptures(out);
  await writeFile(path.join(out, 'index.html'), render(rows, new Date().toISOString()), 'utf8');
  console.log(`[walkthrough] index written: ${path.join(out, 'index.html')} (${rows.length} captures)`);
}

await main();
