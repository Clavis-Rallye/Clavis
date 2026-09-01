import { describe, it, expect, vi, beforeEach } from 'vitest';
import { downloadBlob, downloadJson } from './download';

describe('download', () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });
  });

  describe('downloadBlob', () => {
    it('clicks a temporary anchor named after the file and revokes the URL', () => {
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      const appendSpy = vi.spyOn(document.body, 'appendChild');
      const removeSpy = vi.spyOn(HTMLAnchorElement.prototype, 'remove');

      const blob = new Blob(['x'], { type: 'text/csv' });
      downloadBlob('report.csv', blob);

      expect(createObjectURL).toHaveBeenCalledWith(blob);
      const anchor = appendSpy.mock.calls[0][0] as HTMLAnchorElement;
      expect(anchor.tagName).toBe('A');
      expect(anchor.download).toBe('report.csv');
      expect(anchor.href).toContain('blob:mock-url');
      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(removeSpy).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');

      clickSpy.mockRestore();
      appendSpy.mockRestore();
      removeSpy.mockRestore();
    });
  });

  describe('downloadJson', () => {
    it('downloads a pretty-printed application/json blob of the value', async () => {
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

      downloadJson('data.json', { b: 2, a: 1 });

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      const blobArg = createObjectURL.mock.calls[0][0] as Blob;
      expect(blobArg.type).toBe('application/json');
      const text = await blobArg.text();
      expect(text).toBe(JSON.stringify({ b: 2, a: 1 }, null, 2));

      vi.restoreAllMocks();
    });
  });
});
