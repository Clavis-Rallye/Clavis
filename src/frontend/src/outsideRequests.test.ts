/**
 * Which foreign hosts a browser may contact while it shows Clavis.
 *
 * The privacy text names every outside service. This test pins the list to
 * the two places that decide it: the Content Security Policy of the frontend
 * server and the standalone network check page. A new host here must first
 * get its passage in the privacy text templates
 * (`i18n/locales/de/legalTemplates.json`).
 */
import { describe, expect, it } from 'vitest';
import nginxConf from '../nginx.conf?raw';
import netcheckPage from '../public/netcheck.html?raw';

// The map tiles. The address search runs on the server.
const NAMED_IN_THE_PRIVACY_TEXT = ['*.tile.openstreetmap.org'];

function foreignHosts(text: string): string[] {
  return [...text.matchAll(/https?:\/\/([A-Za-z0-9.*-]+)/g)].map((match) => match[1]);
}

function policies(conf: string): string[] {
  return [...conf.matchAll(/"(default-src [^"]+)"/g)].map((match) => match[1]);
}

describe('outside requests of the frontend', () => {
  it('finds the app policy and the policy of the network check page', () => {
    expect(policies(nginxConf)).toHaveLength(2);
  });

  it('allows no foreign host that the privacy text does not name', () => {
    const hosts = policies(nginxConf).flatMap(foreignHosts);

    expect(hosts.filter((host) => !NAMED_IN_THE_PRIVACY_TEXT.includes(host))).toEqual([]);
  });

  it('lets the network check page contact its own server only', () => {
    const netcheckPolicy = policies(nginxConf).find((policy) => policy.startsWith("default-src 'none'"));

    expect(netcheckPolicy).toBeDefined();
    expect(foreignHosts(netcheckPolicy ?? '')).toEqual([]);
    expect(netcheckPolicy).toContain("connect-src 'self';");
  });

  it('has no foreign address in the network check page', () => {
    expect(foreignHosts(netcheckPage)).toEqual([]);
    // Every request of the page goes through probe(), which starts at the page's own origin.
    expect(netcheckPage.match(/fetch\(/g)).toHaveLength(1);
    expect(netcheckPage).toContain('var url = location.origin + path');
  });
});
