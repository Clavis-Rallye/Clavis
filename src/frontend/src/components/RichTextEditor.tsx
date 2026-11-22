import React, { useCallback, useContext, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Box } from '@mui/material';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import { AuthContext } from '../auth/AuthContext';
import { onOrgMediaRefused } from '../api/media';
import { toEditorHtml } from '../utils/richtext';
import RichTextImageDialog from './RichTextImageDialog';
import RichTextLinkDialog from './RichTextLinkDialog';
import RichTextToolbar from './RichTextToolbar';

interface RichTextEditorProps {
  /** Current document as HTML. Legacy plain text is converted on the way in. */
  value: string;
  onChange: (html: string) => void;
  ariaLabel?: string;
  /** Placeholder shown when the editor is empty. Plain text only. */
  placeholder?: string;
  disabled?: boolean;
  /** Writing direction of the content, for RTL locales. */
  dir?: 'ltr' | 'rtl';
  /** Marks the field as invalid (red border), the field owner explains why. */
  error?: boolean;
  /** Id on the editable element so a label can point at it. */
  id?: string;
  /**
   * Organisation whose media store receives inline images. Defaults to the
   * signed-in user's organisation. Without one the image tool is hidden.
   */
  orgSlug?: string;
  /**
   * Hides the image button and dialog outright, regardless of `orgSlug` or
   * an organisation session in `AuthContext`. Used where the surface has no
   * media store to upload into (e.g. the instance legal texts).
   */
  allowImages?: boolean;
}

/**
 * TipTap-based rich-text editor. The toolbar is `RichTextToolbar`:
 *   B · I · U · H · 1. ordered · • unordered · 🔗 link · 🖼 image
 *
 * Output is HTML. Render the same HTML on the consumer side via
 * the `<SafeHtml />` component (DOMPurify-sanitised) — never with
 * dangerouslySetInnerHTML directly. Inline images live in the organisation's
 * media store and are referenced by their `/api/orgs/{slug}/media/…` route.
 */
const RichTextEditor: React.FC<RichTextEditorProps> = ({
  value,
  onChange,
  ariaLabel,
  placeholder,
  disabled = false,
  dir,
  error = false,
  id,
  orgSlug,
  allowImages = true,
}) => {
  const { t } = useTranslation('common');
  const auth = useContext(AuthContext);
  const mediaOrg = allowImages ? orgSlug ?? auth?.user?.organization_slug : undefined;
  const [imageDialogOpen, setImageDialogOpen] = useState(false);
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  // Why the server refused the last image, as an i18n key, and a counter that
  // gives the image dialog a fresh state after a refusal.
  const [imageRefusal, setImageRefusal] = useState<string | null>(null);
  const [imageDialogRound, setImageDialogRound] = useState(0);

  // The image dialog shows one generic message for every failed upload. A coded
  // refusal (no readable picture, too large) is no reason to try the same file
  // again, so the editor closes the dialog and says the reason itself. Only the
  // editor whose dialog is open listens: a page holds several editors.
  useEffect(() => {
    if (!imageDialogOpen) return undefined;
    return onOrgMediaRefused((messageKey) => {
      setImageRefusal(messageKey);
      setImageDialogOpen(false);
      setImageDialogRound((round) => round + 1);
    });
  }, [imageDialogOpen]);

  const openLinkDialog = useCallback(() => setLinkDialogOpen(true), []);
  const openImageDialog = useCallback(() => {
    setImageRefusal(null);
    setImageDialogOpen(true);
  }, []);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        link: false,
        underline: false,
      }),
      Underline,
      Link.configure({
        openOnClick: false,
        autolink: true,
        HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' },
      }),
      Image.configure({
        inline: false,
        allowBase64: false,
        HTMLAttributes: { loading: 'lazy' },
      }),
    ],
    content: toEditorHtml(value),
    editable: !disabled,
    editorProps: {
      attributes: {
        'aria-label': ariaLabel ?? 'Editor',
        role: 'textbox',
        'aria-multiline': 'true',
        ...(id ? { id } : {}),
        ...(dir ? { dir } : {}),
        ...(placeholder ? { 'data-placeholder': placeholder } : {}),
      },
    },
    onUpdate: ({ editor: e }) => {
      const html = e.getHTML();
      onChange(html === '<p></p>' ? '' : html);
    },
  });

  // Sync external value changes (e.g. after API load) without breaking
  // selection — only if the incoming HTML actually differs.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    const current = editor.getHTML();
    const next = toEditorHtml(value) || '<p></p>';
    if (current !== next && current !== `<p></p>${value}`) {
      editor.commands.setContent(next, { emitUpdate: false });
    }
  }, [editor, value]);

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    // No update event here: it would report the untouched document as a change.
    editor.setEditable(!disabled, false);
  }, [editor, disabled]);

  if (!editor) return null;

  return (
    <Box
      sx={{
        border: 1,
        borderColor: error ? 'error.main' : 'divider',
        borderRadius: 1.5,
        // `clip` and not `hidden`: the rounded corners still clip, but the frame
        // is no scroll container, so the toolbar can stay in view (sticky).
        overflow: 'clip',
        bgcolor: disabled ? 'action.disabledBackground' : 'background.paper',
        opacity: disabled ? 0.6 : 1,
        // The focus frame matches the other outlined fields (theme.ts: #1D47A4,
        // 2px). The error frame keeps its red. The second pixel is an inset
        // shadow, so the border box keeps its size and nothing shifts.
        '&:focus-within': {
          borderColor: error ? 'error.main' : '#1D47A4',
          boxShadow: `inset 0 0 0 1px ${error ? '#B3261E' : '#1D47A4'}`,
        },
      }}
    >
      <RichTextToolbar
        editor={editor}
        disabled={disabled}
        allowImages={Boolean(mediaOrg)}
        onOpenLink={openLinkDialog}
        onOpenImage={openImageDialog}
        editorLabel={ariaLabel}
      />
      {imageRefusal && (
        <Alert severity="error" sx={{ borderRadius: 0 }}>
          {t(imageRefusal)}
        </Alert>
      )}
      <Box
        sx={{
          px: 2,
          py: 1.25,
          minHeight: 110,
          '& .ProseMirror': {
            outline: 'none',
            minHeight: 80,
            '& p': { margin: '0 0 0.75em' },
            '& h2': { margin: '0.5em 0 0.25em', fontSize: '1.4em' },
            '& h3': { margin: '0.5em 0 0.25em', fontSize: '1.2em' },
            '& ul, & ol': { paddingLeft: '1.5em', margin: '0 0 0.75em' },
            '& a': { color: 'primary.main', textDecoration: 'underline' },
            '& img': {
              display: 'block',
              maxWidth: '100%',
              height: 'auto',
              borderRadius: 1,
              margin: '0.75em 0',
            },
            '& img.ProseMirror-selectednode': {
              outline: '3px solid',
              outlineColor: 'primary.main',
            },
          },
          '& .ProseMirror p.is-editor-empty:first-of-type::before': {
            content: 'attr(data-placeholder)',
            color: 'text.disabled',
            float: 'left',
            pointerEvents: 'none',
            height: 0,
          },
        }}
      >
        <EditorContent editor={editor} />
      </Box>
      {mediaOrg && (
        <RichTextImageDialog
          key={imageDialogRound}
          open={imageDialogOpen}
          orgSlug={mediaOrg}
          onClose={() => setImageDialogOpen(false)}
          onInsert={({ src, alt }) => {
            editor.chain().focus().setImage({ src, alt }).run();
          }}
        />
      )}
      <RichTextLinkDialog
        open={linkDialogOpen}
        onClose={() => setLinkDialogOpen(false)}
        onInsert={(href) => {
          if (editor.state.selection.empty && !editor.isActive('link')) {
            // Nothing is selected: the address itself becomes the linked text.
            // A mark without text was dropped by the next click.
            editor
              .chain()
              .focus()
              .insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href } }] })
              .run();
            return;
          }
          editor.chain().focus().extendMarkRange('link').setLink({ href }).run();
        }}
      />
    </Box>
  );
};

export default RichTextEditor;
