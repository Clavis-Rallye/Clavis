/**
 * Third-party licence notices for the production bundle.
 *
 * MIT, BSD, ISC and Apache-2.0 ask for their notice in every copy, and the
 * built frontend is one. In a production build this plugin maps each bundled
 * module to its package and emits `third-party-licenses.txt`: each package
 * once, sorted by name, with its version, its licence from package.json and
 * the full text of its licence files. A fixed entry covers the flag font in
 * public/, which the bundler never sees as a module.
 *
 * The core ({@link renderThirdPartyLicenses}) is pure over a module list and a
 * small file-system interface, so the test drives it with fake package folders.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

/** The two reads the core needs; the plugin passes node:fs, the test a map. */
export interface PackageFs {
  /** File contents, or undefined when there is no such file. */
  readFile(filePath: string): string | undefined
  /** File names (not paths) directly inside the folder, or [] when absent. */
  listFiles(dirPath: string): string[]
}

interface PackageLicence {
  name: string
  version: string
  licence: string
  /** Full text of every licence/notice file in the package root, by file name. */
  texts: string[]
}

const LICENCE_ASSET_NAME = 'third-party-licenses.txt'

const NODE_MODULES = '/node_modules/'

// LICENSE, LICENCE, COPYING, NOTICE — with or without an extension or a
// suffix such as LICENSE-MIT, any case.
const LICENCE_FILE = /^(?:licen[cs]e|copying|notice)(?:[-._].*)?$/i

/**
 * The package folder a bundled module belongs to, or undefined for the app's
 * own code and virtual modules. The last node_modules segment wins, so a
 * nested copy maps to the nested package.
 */
export function packageDirOf(moduleId: string): string | undefined {
  // Virtual modules carry a NUL prefix; loaders append ?query to real paths.
  const id = moduleId.replace(/\\/g, '/').split('?')[0]
  if (id.startsWith('\0')) return undefined
  const at = id.lastIndexOf(NODE_MODULES)
  if (at < 0) return undefined
  const rest = id.slice(at + NODE_MODULES.length).split('/')
  const segments = rest[0]?.startsWith('@') ? 2 : 1
  if (rest.length <= segments || rest.slice(0, segments).some((part) => !part)) return undefined
  return id.slice(0, at + NODE_MODULES.length) + rest.slice(0, segments).join('/')
}

interface PackageJson {
  name?: unknown
  version?: unknown
  license?: unknown
  licenses?: unknown
}

function licenceIdentifier(pkg: PackageJson): string {
  const typeOf = (value: unknown): string | undefined => {
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (value && typeof value === 'object' && 'type' in value) {
      const type = (value as { type: unknown }).type
      if (typeof type === 'string' && type.trim()) return type.trim()
    }
    return undefined
  }
  const single = typeOf(pkg.license)
  if (single) return single
  // The deprecated `licenses` array of old packages.
  if (Array.isArray(pkg.licenses)) {
    const all = pkg.licenses.map(typeOf).filter((type): type is string => Boolean(type))
    if (all.length) return all.length === 1 ? all[0] : `(${all.join(' OR ')})`
  }
  return 'UNKNOWN'
}

/** Name, version, licence and licence texts of one package folder. */
function readPackageLicence(dir: string, fs: PackageFs): PackageLicence | undefined {
  const raw = fs.readFile(`${dir}/package.json`)
  if (raw === undefined) return undefined
  let pkg: PackageJson
  try {
    pkg = JSON.parse(raw) as PackageJson
  } catch {
    return undefined
  }
  const name = typeof pkg.name === 'string' && pkg.name ? pkg.name : path.posix.basename(dir)
  const version = typeof pkg.version === 'string' && pkg.version ? pkg.version : 'unknown'
  const texts = fs
    .listFiles(dir)
    .filter((file) => LICENCE_FILE.test(file))
    .sort()
    .map((file) => fs.readFile(`${dir}/${file}`)?.trim())
    .filter((text): text is string => Boolean(text))
  return { name, version, licence: licenceIdentifier(pkg), texts }
}

/** Every package behind the given module ids, each once, sorted by name. */
export function collectPackageLicences(moduleIds: Iterable<string>, fs: PackageFs): PackageLicence[] {
  const dirs = new Set<string>()
  for (const id of moduleIds) {
    const dir = packageDirOf(id)
    if (dir) dirs.add(dir)
  }
  const byKey = new Map<string, PackageLicence>()
  for (const dir of dirs) {
    const entry = readPackageLicence(dir, fs)
    if (!entry) continue
    // Two folders can hold the same name@version (hoisting leftovers); one entry.
    const key = `${entry.name}@${entry.version}`
    if (!byKey.has(key)) byKey.set(key, entry)
  }
  return [...byKey.values()].sort(
    (a, b) => compare(a.name, b.name) || compare(a.version, b.version),
  )
}

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** The package that ships the flag font in public/ (see src/main.tsx). */
const FLAG_FONT_PACKAGE = 'country-flag-emoji-polyfill'
const FLAG_FONT_FILE = 'TwemojiCountryFlags.woff2'

/**
 * The fixed entry for public/TwemojiCountryFlags.woff2. The font is a subset
 * of Twemoji Mozilla made by country-flag-emoji-polyfill; its art is Twemoji's,
 * under CC BY 4.0, which asks for creator, copyright, licence link and a note
 * of changes. The origin is read from the delivering package when present.
 */
export function flagFontAttribution(nodeModulesDir: string, fs: PackageFs): string {
  let origin = `${FLAG_FONT_PACKAGE} (https://github.com/talkjs/country-flag-emoji-polyfill)`
  const raw = fs.readFile(`${nodeModulesDir}/${FLAG_FONT_PACKAGE}/package.json`)
  if (raw !== undefined) {
    try {
      const pkg = JSON.parse(raw) as { name?: unknown; version?: unknown; repository?: unknown }
      const repo = pkg.repository
      const repository =
        typeof repo === 'string'
          ? repo
          : repo && typeof repo === 'object' && 'url' in repo && typeof repo.url === 'string'
            ? repo.url
            : 'https://github.com/talkjs/country-flag-emoji-polyfill'
      const name = typeof pkg.name === 'string' ? pkg.name : FLAG_FONT_PACKAGE
      const version = typeof pkg.version === 'string' ? ` ${pkg.version}` : ''
      origin = `${name}${version} (${repository})`
    } catch {
      // keep the known origin
    }
  }
  return [
    `File: ${FLAG_FONT_FILE} (font "Twemoji Country Flags")`,
    `Delivered by: ${origin}`,
    'Licence: CC-BY-4.0',
    '',
    'Graphics: Twemoji, Copyright 2019 Twitter, Inc and other contributors',
    '  https://github.com/twitter/twemoji',
    'Licensed under the Creative Commons Attribution 4.0 International licence',
    '(CC BY 4.0): https://creativecommons.org/licenses/by/4.0/',
    'Changes: the font is a subset of Twemoji Mozilla',
    '(https://github.com/mozilla/twemoji-colr) reduced to the country flags,',
    `made by ${FLAG_FONT_PACKAGE}. The graphics are provided as is, without`,
    'warranties of any kind.',
  ].join('\n')
}

const RULE = '-'.repeat(79)

const HEADER = `Third-party licences of the Clavis web app
==========================================

Every third-party package in this build is listed with its name, version,
licence from its package.json, and the full text of the licence and notice
files it ships. The flag font follows at the end.`

/** The whole notice document: the bundle's packages, then the flag font. */
export function renderThirdPartyLicenses(
  moduleIds: Iterable<string>,
  nodeModulesDir: string,
  fs: PackageFs,
): string {
  const sections = collectPackageLicences(moduleIds, fs).map((entry) =>
    [
      RULE,
      `${entry.name} ${entry.version} — ${entry.licence}`,
      RULE,
      '',
      entry.texts.length ? entry.texts.join('\n\n') : '(no licence file shipped by this package)',
    ].join('\n'),
  )
  const font = [RULE, `${FLAG_FONT_FILE} — CC-BY-4.0`, RULE, '', flagFontAttribution(nodeModulesDir, fs)].join(
    '\n',
  )
  return [HEADER, ...sections, font].join('\n\n') + '\n'
}

/** node:fs behind the {@link PackageFs} interface. */
const nodePackageFs: PackageFs = {
  readFile(filePath) {
    try {
      return readFileSync(filePath, 'utf8')
    } catch {
      return undefined
    }
  },
  listFiles(dirPath) {
    if (!existsSync(dirPath)) return []
    try {
      return readdirSync(dirPath).filter((name) => {
        try {
          return statSync(`${dirPath}/${name}`).isFile()
        } catch {
          return false
        }
      })
    } catch {
      return []
    }
  },
}

/** Emits third-party-licenses.txt in the production build. */
export function thirdPartyLicensesPlugin(): Plugin {
  let root = process.cwd()
  return {
    name: 'clavis:third-party-licenses',
    apply: 'build',
    configResolved(config) {
      root = config.root
    },
    generateBundle(_options, bundle) {
      const moduleIds: string[] = []
      for (const output of Object.values(bundle)) {
        if (output.type === 'chunk') moduleIds.push(...output.moduleIds)
      }
      const nodeModulesDir = path.resolve(root, 'node_modules').replace(/\\/g, '/')
      this.emitFile({
        type: 'asset',
        fileName: LICENCE_ASSET_NAME,
        source: renderThirdPartyLicenses(moduleIds, nodeModulesDir, nodePackageFs),
      })
    },
  }
}
