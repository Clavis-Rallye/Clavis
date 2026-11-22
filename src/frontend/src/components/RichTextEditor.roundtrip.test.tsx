import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Editor } from '@tiptap/react';
import RichTextEditor from './RichTextEditor';
import { EDITOR_091, RICH_TEXT_091_CORPUS } from '../utils/richText091.fixtures';

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
      expect(element.editor.getHTML()).toBe(EDITOR_091[key]);
      expect(onChange).not.toHaveBeenCalled();
    },
  );
});
