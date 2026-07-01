// A link that hands a coordinate to the device's navigation app.
//
// `geo:` is the standard, and Android opens its app chooser for it. iOS does not
// register it, so Safari leaves the link dead without a word. That is the worst
// outcome, because the link looks fine until someone taps it in the street.
// Apple devices get an Apple Maps https URL instead, which opens the native app.
//
// Callers keep a copy-to-clipboard control beside the link. It is the only thing
// that works on desktop and in locked-down browsers.

export function navigationHref(lat: number, lng: number, label?: string): string {
  const isApple =
    typeof navigator !== 'undefined' &&
    /iPad|iPhone|iPod|Macintosh/.test(navigator.userAgent);

  if (isApple) {
    const q = label ? `&q=${encodeURIComponent(label)}` : '';
    return `https://maps.apple.com/?ll=${lat},${lng}${q}`;
  }

  const pin = label ? `(${encodeURIComponent(label)})` : '';
  return `geo:${lat},${lng}?q=${lat},${lng}${pin}`;
}
