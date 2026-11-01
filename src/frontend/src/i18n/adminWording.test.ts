import { describe, expect, it } from 'vitest';
import deAdmin from './locales/de/admin.json';
import enAdmin from './locales/en/admin.json';
import deRuns from './locales/de/runs.json';
import enRuns from './locales/en/runs.json';

// The English settings page must not hold a German chip text inside an
// English sentence. The legal intro must name the text of the installation
// in both languages.
describe('admin wording, German and English in step', () => {
  it('the purge dialog quotes the playback text of its own language', () => {
    expect(deAdmin.org.media.dialogWhat).toContain(`„${deRuns.participant.challenge.mediaRemoved}“`);
    expect(enAdmin.org.media.dialogWhat).toContain(`“${enRuns.participant.challenge.mediaRemoved}”`);
    expect(enAdmin.org.media.dialogWhat).not.toContain('entfernt');
  });

  it('the legal intro names the text of the installation in both languages', () => {
    expect(deAdmin.org.legal.intro).toContain('Text der Installation');
    expect(enAdmin.org.legal.intro).toContain('text of the installation');
    expect(enAdmin.org.legal.intro).not.toContain('template');
  });
});
