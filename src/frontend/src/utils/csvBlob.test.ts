import { describe, it, expect } from 'vitest';
import { csvBlob } from './download';

/**
 * Excel reads UTF-8 only with a byte order mark.
 * The browser drops the server's BOM when it decodes the response, so the
 * download puts it back, exactly once.
 */
describe('csvBlob', () => {
  const firstBytes = async (blob: Blob) => new Uint8Array(await blob.arrayBuffer()).slice(0, 4);

  it('starts the file with the UTF-8 byte order mark', async () => {
    const bytes = await firstBytes(csvBlob('Team;Punkte\r\n'));
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(bytes[3]).toBe('T'.charCodeAt(0));
  });

  it('never writes a second mark when the text already has one', async () => {
    const bytes = await firstBytes(csvBlob('﻿Team;Punkte\r\n'));
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(bytes[3]).toBe('T'.charCodeAt(0));
  });
});
