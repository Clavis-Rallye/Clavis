import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Editor } from '@tiptap/react';
import RichTextEditor from './RichTextEditor';
import { EDITOR_091, RICH_TEXT_091_CORPUS } from '../utils/richText091.fixtures';

// The editor maps h1 to h2, a pre block to paragraphs and code to plain text before it opens a
// document (toEditorHtml). Release 0.9.1 opened the h1 as a paragraph and kept the pre block and the
// code. The output of the key below changes by design; every other key stays as recorded.
const CHANGED_BY_THE_NEW_EDITOR: Record<string, string> = {
  legacyServerUnfiltered:
    '<h2>Alter Titel</h2><p>Zeile eins<br>Zeile zwei</p><p>Ein Aufruf wie ls -l im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>',
};

describe('RichTextEditor on the 0.9.1 corpus', () => {
  it.each(Object.keys(RICH_TEXT_091_CORPUS))(
    'opens %s as release 0.9.1 did, without calling onChange',
    async (key) => {
      const onChange = vi.fn();
      const { container } = render(
        <RichTextEditor
          value={RICH_TEXT_091_CORPUS[key]}
          onChange={onChange}
          ariaLabel="Text"
          orgSlug="stadt"
        />,
      );
      await screen.findByRole('textbox', { name: 'Text' });
      const element = container.querySelector('.ProseMirror') as HTMLElement & {
        editor: Editor;
      };
      expect(element.editor.getHTML()).toBe(CHANGED_BY_THE_NEW_EDITOR[key] ?? EDITOR_091[key]);
      expect(onChange).not.toHaveBeenCalled();
    },
  );
});
