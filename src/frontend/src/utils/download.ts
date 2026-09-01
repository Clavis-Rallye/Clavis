/** Triggers a browser download of `blob` named `fileName`. */
export function downloadBlob(fileName: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Downloads `value` as a pretty-printed JSON file named `fileName`. */
export function downloadJson(fileName: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
  downloadBlob(fileName, blob);
}

/**
 * A CSV file that Excel opens as UTF-8.
 *
 * Excel needs the byte order mark to read umlauts right. The API sends one,
 * but the browser drops a leading BOM when it decodes the response as text,
 * so the file gets it back here, exactly once.
 */
export function csvBlob(csv: string): Blob {
  const BOM = '﻿';
  return new Blob([csv.startsWith(BOM) ? csv : BOM + csv], { type: 'text/csv;charset=utf-8' });
}
