import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, IconButton, useMediaQuery } from '@mui/material';
import FormatBoldIcon from '@mui/icons-material/FormatBold';
import FormatItalicIcon from '@mui/icons-material/FormatItalic';
import FormatUnderlinedIcon from '@mui/icons-material/FormatUnderlined';
import StrikethroughSIcon from '@mui/icons-material/StrikethroughS';
import LinkIcon from '@mui/icons-material/Link';
import LinkOffIcon from '@mui/icons-material/LinkOff';
import { useEditorState, type Editor } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import { NodeSelection } from '@tiptap/pm/state';

const PLUGIN_KEY = 'selectionMenu';
const coarsePointer = '@media (pointer: coarse)';

/** Bottom edge, in the viewport, of the toolbar the editor renders above its content. */
const toolbarBottom = (editor: Editor): number => {
  if (editor.isDestroyed) return 0;
  for (let node = editor.view.dom.parentElement; node; node = node.parentElement) {
    const bar = node.querySelector(':scope > [role="toolbar"]');
    if (bar) return Math.max(0, bar.getBoundingClientRect().bottom);
  }
  return 0;
};

/**
 * The flip check of the menu above the selection. Its top padding is the bottom
 * edge of the editor's toolbar, so a menu that would reach under the toolbar
 * flips below the selection. The viewport alone is the boundary, so the padding
 * is a viewport coordinate; Floating UI reads the options anew on every move.
 */
const clearOfToolbar = (editor: Editor) => () => ({
  boundary: [],
  rootBoundary: 'viewport' as const,
  padding: { top: toolbarBottom(editor) },
});

interface SelectionMenuProps {
  editor: Editor;
  /** Opens the same link dialog as the toolbar. */
  onOpenLink: () => void;
}

interface MarkState {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  link: boolean;
}

type Tool = 'bold' | 'italic' | 'underline' | 'strike' | 'link';

/**
 * The small menu on selected text: bold, italic, underline, strike-through and
 * link, so the toolbar is not the only way to reach them. It never takes the
 * focus (a press keeps the selection in the editor), Escape hides it and keeps
 * the selection. With a mouse it sits above the selection, or below it where it would cover the
 * editor's toolbar; on a touch screen it sits below, where it leaves the
 * system's own selection menu usable.
 */
const RichTextSelectionMenu: React.FC<SelectionMenuProps> = ({ editor, onOpenLink }) => {
  const { t } = useTranslation('common');
  const coarse = useMediaQuery('(pointer: coarse)');
  // The selection the person dismissed with Escape, as `from:to`. A new
  // selection shows the menu again.
  const dismissedRef = useRef<string | null>(null);
  const shownRef = useRef(false);

  const marks = useEditorState({
    editor,
    selector: ({ editor: current }): MarkState => ({
      bold: current.isActive('bold'),
      italic: current.isActive('italic'),
      underline: current.isActive('underline'),
      strike: current.isActive('strike'),
      link: current.isActive('link'),
    }),
    equalityFn: (a, b) =>
      a === b ||
      (a !== null &&
        b !== null &&
        a.bold === b.bold &&
        a.italic === b.italic &&
        a.underline === b.underline &&
        a.strike === b.strike &&
        a.link === b.link),
  });

  // Escape in the editor hides the menu and leaves the selection as it is. The
  // key is taken only while the menu shows, so Escape keeps its other meanings.
  useEffect(() => {
    // On the document, in the capture phase: the view of the editor is not
    // there yet when this effect first runs.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !shownRef.current || editor.isDestroyed) return;
      if (!(event.target instanceof Node) || !editor.view.dom.contains(event.target)) return;
      const { from, to } = editor.state.selection;
      dismissedRef.current = `${from}:${to}`;
      shownRef.current = false;
      event.stopPropagation();
      editor.view.dispatch(editor.state.tr.setMeta(PLUGIN_KEY, 'hide'));
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [editor]);

  const run = (tool: Tool) => {
    switch (tool) {
      case 'bold':
        editor.chain().focus().toggleBold().run();
        break;
      case 'italic':
        editor.chain().focus().toggleItalic().run();
        break;
      case 'underline':
        editor.chain().focus().toggleUnderline().run();
        break;
      case 'strike':
        editor.chain().focus().toggleStrike().run();
        break;
      case 'link':
        if (editor.isActive('link')) {
          editor.chain().focus().unsetLink().run();
        } else {
          onOpenLink();
        }
        break;
    }
  };

  const tools: { id: Tool; label: string; icon: React.ReactNode }[] = [
    { id: 'bold', label: t('richTextEditor.bold'), icon: <FormatBoldIcon fontSize="small" /> },
    { id: 'italic', label: t('richTextEditor.italic'), icon: <FormatItalicIcon fontSize="small" /> },
    { id: 'underline', label: t('richTextEditor.underline'), icon: <FormatUnderlinedIcon fontSize="small" /> },
    { id: 'strike', label: t('richTextEditor.strike'), icon: <StrikethroughSIcon fontSize="small" /> },
    {
      id: 'link',
      label: marks.link ? t('richTextEditor.removeLink') : t('richTextEditor.addLink'),
      icon: marks.link ? <LinkOffIcon fontSize="small" /> : <LinkIcon fontSize="small" />,
    },
  ];

  return (
    <BubbleMenu
      editor={editor}
      pluginKey={PLUGIN_KEY}
      updateDelay={0}
      // Text only: an image or a divider that is selected has its own menu, a
      // selection in a table shows the table menu, a disabled editor shows none.
      shouldShow={({ editor: current, view, state }) => {
        const { selection } = state;
        const key = `${selection.from}:${selection.to}`;
        if (dismissedRef.current !== null && dismissedRef.current !== key) dismissedRef.current = null;
        const show =
          current.isEditable &&
          view.hasFocus() &&
          !selection.empty &&
          !(selection instanceof NodeSelection) &&
          !current.isActive('image') &&
          !current.isActive('table') &&
          dismissedRef.current === null;
        shownRef.current = show;
        return show;
      }}
      options={
        coarse
          ? { placement: 'bottom', offset: 8 }
          : { placement: 'top', offset: 8, flip: clearOfToolbar(editor) }
      }
      style={{ zIndex: 3 }}
    >
      <Box
        role="toolbar"
        aria-orientation="horizontal"
        aria-label={t('richTextEditor.selectionMenu')}
        // A press on the menu must not move the focus out of the editor.
        onMouseDown={(event) => event.preventDefault()}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: '2px',
          p: 0.5,
          bgcolor: 'background.paper',
          border: 1,
          borderColor: 'divider',
          borderRadius: 1.5,
          boxShadow: 3,
        }}
      >
        {tools.map(({ id, label, icon }) => {
          const pressed = marks[id];
          return (
            <IconButton
              key={id}
              size="small"
              aria-label={label}
              aria-pressed={pressed}
              tabIndex={-1}
              onClick={() => run(id)}
              sx={{
                borderRadius: 1,
                color: pressed ? 'primary.main' : 'text.secondary',
                bgcolor: pressed ? 'action.selected' : 'transparent',
                [coarsePointer]: { width: 40, height: 40 },
              }}
            >
              {icon}
            </IconButton>
          );
        })}
      </Box>
    </BubbleMenu>
  );
};

export default RichTextSelectionMenu;
