import { describe, expect, it } from 'vitest'
import {
  collectPackageLicences,
  flagFontAttribution,
  packageDirOf,
  renderThirdPartyLicenses,
  type PackageFs,
} from './thirdPartyLicenses'

const NM = '/repo/node_modules'

/** Fake package folders: a map of file path to contents. */
function fakeFs(files: Record<string, string>): PackageFs {
  return {
    readFile: (filePath) => files[filePath],
    listFiles: (dir) =>
      Object.keys(files)
        .filter((file) => file.startsWith(`${dir}/`) && !file.slice(dir.length + 1).includes('/'))
        .map((file) => file.slice(dir.length + 1)),
  }
}

const FILES: Record<string, string> = {
  [`${NM}/zeta/package.json`]: JSON.stringify({ name: 'zeta', version: '2.0.0', license: 'ISC' }),
  [`${NM}/zeta/LICENSE`]: 'ISC licence text of zeta\n',
  [`${NM}/zeta/index.js`]: '',
  [`${NM}/@scope/alpha/package.json`]: JSON.stringify({
    name: '@scope/alpha',
    version: '1.2.3',
    license: 'Apache-2.0',
  }),
  [`${NM}/@scope/alpha/LICENSE.txt`]: 'Apache licence text\n',
  [`${NM}/@scope/alpha/NOTICE`]: 'Alpha NOTICE text\n',
  // A sub-folder package.json (ESM marker) must not be taken for the package.
  [`${NM}/@scope/alpha/esm/package.json`]: JSON.stringify({ type: 'module' }),
  [`${NM}/mid/package.json`]: JSON.stringify({
    name: 'mid',
    version: '0.1.0',
    licenses: [{ type: 'MIT' }],
  }),
  [`${NM}/mid/COPYING.md`]: 'MIT text of mid\n',
  [`${NM}/mid/node_modules/zeta/package.json`]: JSON.stringify({
    name: 'zeta',
    version: '1.0.0',
    license: { type: 'BSD-3-Clause' },
  }),
  [`${NM}/mid/node_modules/zeta/LICENCE`]: 'BSD text of nested zeta\n',
  [`${NM}/bare/package.json`]: JSON.stringify({ name: 'bare', version: '3.0.0' }),
  [`${NM}/country-flag-emoji-polyfill/package.json`]: JSON.stringify({
    name: 'country-flag-emoji-polyfill',
    version: '0.1.10',
    repository: 'https://github.com/talkjs/country-flag-emoji-polyfill',
    license: 'MIT',
  }),
}

const MODULES = [
  '/repo/src/main.tsx',
  '\0vite/modulepreload-polyfill.js',
  `${NM}/zeta/index.js`,
  `${NM}/zeta/lib/other.js?commonjs-es-import`,
  `${NM}/@scope/alpha/esm/index.js`,
  `${NM}/@scope/alpha/esm/util.js`,
  `${NM}/mid/index.js`,
  `${NM}/mid/node_modules/zeta/index.js`,
  `${NM}/bare/index.js`,
]

describe('packageDirOf', () => {
  it('maps module ids to their package folder', () => {
    expect(packageDirOf(`${NM}/zeta/lib/a.js`)).toBe(`${NM}/zeta`)
    expect(packageDirOf(`${NM}/@scope/alpha/esm/index.js?x`)).toBe(`${NM}/@scope/alpha`)
    expect(packageDirOf(`${NM}/mid/node_modules/zeta/index.js`)).toBe(`${NM}/mid/node_modules/zeta`)
    expect(packageDirOf('C:\\repo\\node_modules\\zeta\\index.js')).toBe('C:/repo/node_modules/zeta')
  })

  it('skips the app code and virtual modules', () => {
    expect(packageDirOf('/repo/src/main.tsx')).toBeUndefined()
    expect(packageDirOf(`\0${NM}/zeta/index.js`)).toBeUndefined()
  })
})

describe('collectPackageLicences', () => {
  it('lists each bundled package once, sorted by name, with licence and texts', () => {
    const entries = collectPackageLicences(MODULES, fakeFs(FILES))
    expect(entries.map((entry) => `${entry.name}@${entry.version}`)).toEqual([
      '@scope/alpha@1.2.3',
      'bare@3.0.0',
      'mid@0.1.0',
      'zeta@1.0.0',
      'zeta@2.0.0',
    ])
    const alpha = entries[0]
    expect(alpha.licence).toBe('Apache-2.0')
    expect(alpha.texts).toEqual(['Apache licence text', 'Alpha NOTICE text'])
    expect(entries[2].licence).toBe('MIT')
    expect(entries[2].texts).toEqual(['MIT text of mid'])
    expect(entries[3].licence).toBe('BSD-3-Clause')
    expect(entries[3].texts).toEqual(['BSD text of nested zeta'])
    expect(entries[1]).toMatchObject({ licence: 'UNKNOWN', texts: [] })
  })

  it('leaves out packages that are installed but not in the bundle', () => {
    const entries = collectPackageLicences([`${NM}/zeta/index.js`], fakeFs(FILES))
    expect(entries.map((entry) => entry.name)).toEqual(['zeta'])
  })
})

describe('renderThirdPartyLicenses', () => {
  it('writes name, version, licence and full text per package, then the flag font', () => {
    const text = renderThirdPartyLicenses(MODULES, NM, fakeFs(FILES))
    expect(text).toContain('@scope/alpha 1.2.3 — Apache-2.0')
    expect(text).toContain('Apache licence text')
    expect(text).toContain('Alpha NOTICE text')
    expect(text).toContain('zeta 2.0.0 — ISC')
    expect(text).toContain('ISC licence text of zeta')
    expect(text).toContain('bare 3.0.0 — UNKNOWN')
    expect(text).toContain('(no licence file shipped by this package)')
    expect(text.indexOf('@scope/alpha 1.2.3')).toBeLessThan(text.indexOf('zeta 2.0.0'))
    // Each package once.
    expect(text.split('zeta 2.0.0 — ISC').length).toBe(2)
    // The app's own code is not a third-party package.
    expect(text).not.toContain('main.tsx')
    // The flag font comes last, with its CC BY 4.0 attribution.
    const font = text.indexOf('TwemojiCountryFlags.woff2 — CC-BY-4.0')
    expect(font).toBeGreaterThan(text.indexOf('zeta 2.0.0'))
    expect(text).toContain('Delivered by: country-flag-emoji-polyfill 0.1.10')
    expect(text).toContain('https://creativecommons.org/licenses/by/4.0/')
    expect(text).toContain('Twitter, Inc and other contributors')
  })
})

describe('flagFontAttribution', () => {
  it('still names the origin when the delivering package is missing', () => {
    const text = flagFontAttribution(NM, fakeFs({}))
    expect(text).toContain('country-flag-emoji-polyfill')
    expect(text).toContain('CC BY 4.0')
  })
})
