import { describe, it, expect, afterEach } from 'vitest';
import { navigationHref } from './navigationHref';

const realUA = navigator.userAgent;

function setUserAgent(ua: string) {
  Object.defineProperty(navigator, 'userAgent', { value: ua, configurable: true });
}

afterEach(() => setUserAgent(realUA));

describe('navigationHref', () => {
  // The whole point of the platform branch: `geo:` is dead on iOS, and a dead
  // link is worse than no link because nothing signals the failure.
  it('hands iOS an Apple Maps URL, not a geo: URI', () => {
    setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15');
    const href = navigationHref(48.3705, 10.8978, 'Rathaus');
    expect(href).toMatch(/^https:\/\/maps\.apple\.com\//);
    expect(href).toContain('48.3705,10.8978');
    expect(href).not.toContain('geo:');
  });

  it('hands Android a geo: URI so the OS offers its navigation apps', () => {
    setUserAgent('Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/140');
    const href = navigationHref(48.3705, 10.8978, 'Rathaus');
    expect(href).toMatch(/^geo:48\.3705,10\.8978\?q=48\.3705,10\.8978/);
  });

  it('escapes the pin label — station titles contain spaces and parentheses', () => {
    setUserAgent('Mozilla/5.0 (Linux; Android 15)');
    expect(navigationHref(1, 2, 'Ende und Siegerehrung')).toContain(
      'Ende%20und%20Siegerehrung',
    );
  });

  it('omits the label when there is none', () => {
    setUserAgent('Mozilla/5.0 (Linux; Android 15)');
    expect(navigationHref(1, 2)).toBe('geo:1,2?q=1,2');
  });
});
