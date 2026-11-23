import React, { useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Box, FormHelperText } from '@mui/material';
import { useEditor, useEditorState, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import { AuthContext } from '../auth/AuthContext';
import { onOrgMediaRefused } from '../api/media';
import { cleanPastedHtml } from '../utils/cleanPastedHtml';
import { toEditorHtml } from '../utils/richtext';
import contract from '../utils/richTextContract.json';
import RichTextImageDialog from './RichTextImageDialog';
import RichTextImageMenu, { focusImageMenu } from './RichTextImageMenu';
import RichTextSelectionMenu from './RichTextSelectionMenu';
import RichTextTableMenu, { focusTableMenu } from './RichTextTableMenu';
import RichTextImageSettingsDialog, { type ImageSettings } from './RichTextImageSettingsDialog';
import { richTextImage, richTextLink, richTextTableExtensions } from './richTextExtensions';
import RichTextLinkDialog from './RichTextLinkDialog';
import RichTextToolbar from './RichTextToolbar';

/** The longest rich text the API takes per language, from the contract file the API filter follows. */
const MAX_TEXT_LENGTH = contract.text.maxLength;

/** What the editor hands to the form: an empty document is an empty string. */
const handedHtml = (html: string): string => (html === '<p></p>' ? '' : html);

/** The length of the HTML of each document state, so a selection change does not serialise it again. */
const htmlLengths = new WeakMap<object, number>();

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
 *   undo · redo · text style · B · I · U · S · 1. ordered · • unordered · quote · 🔗 link · 🖼 image · divider · table
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
  const { t, i18n } = useTranslation('common');
  const tooLongId = `${useId()}-too-long`;
  const auth = useContext(AuthContext);
  const mediaOrg = allowImages ? orgSlug ?? auth?.user?.organization_slug : undefined;
  const [imageDialogOpen, setImageDialogOpen] = useState(false);
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [imageSettingsOpen, setImageSettingsOpen] = useState(false);
  // Why the server refused the last image, as an i18n key, and a counter that
  // gives the image dialog a fresh state after a refusal.
  const [imageRefusal, setImageRefusal] = useState<string | null>(null);
  const [imageDialogRound, setImageDialogRound] = useState(0);
  // Images the last paste left out. The paste cleaner runs inside ProseMirror's
  // paste, far from React state, so it writes the count to this ref and the
  // paste handler, which runs later in the same paste, moves it into state.
  const pastedImagesLeftOut = useRef(0);
  const [pasteImagesLeftOut, setPasteImagesLeftOut] = useState(0);

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

  const openImageSettings = useCallback(() => setImageSettingsOpen(true), []);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        // Code and code blocks are not part of the contract: no shortcut and no
        // typed shortcut (backticks) makes them.
        code: false,
        codeBlock: false,
        link: false,
        underline: false,
      }),
      Underline,
      richTextLink,
      richTextImage,
      ...richTextTableExtensions(),
    ],
    content: toEditorHtml(value),
    editable: !disabled,
    editorProps: {
      // Word, Google Docs and web pages put their markup on the clipboard. It is
      // cleaned to the contract before the editor reads it, so a paste shows what
      // will be stored. Plain text never passes here.
      transformPastedHTML: (html) => {
        const cleaned = cleanPastedHtml(html);
        pastedImagesLeftOut.current = cleaned.imagesLeftOut;
        return cleaned.html;
      },
      handleDOMEvents: {
        // A count left by a drop or by a paste that failed must not show up on
        // the next paste: every paste starts at zero.
        paste: () => {
          pastedImagesLeftOut.current = 0;
          return false;
        },
      },
      // Runs after the transform of the same paste, also for a paste of plain
      // text (count 0), which closes the note of the paste before.
      handlePaste: () => {
        setPasteImagesLeftOut(pastedImagesLeftOut.current);
        pastedImagesLeftOut.current = 0;
        return false;
      },
      // Alt+F10 moves the focus into the menu of the selected image, or of the
      // table the cursor is in. Without a menu the key keeps its meaning.
      handleKeyDown: (view, event) => {
        if (event.key !== 'F10' || !event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
        if (!focusImageMenu(view.dom.parentElement) && !focusTableMenu(view.dom.parentElement)) return false;
        event.preventDefault();
        return true;
      },
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
      const html = handedHtml(e.getHTML());
      onChange(html);
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

  // Length of the HTML the editor hands to the form, which the API holds to a limit.
  const htmlLength =
    useEditorState({
      editor,
      selector: ({ editor: current }) => {
        if (!current || current.isDestroyed) return 0;
        const doc = current.state.doc;
        let length = htmlLengths.get(doc);
        if (length === undefined) {
          length = handedHtml(current.getHTML()).length;
          htmlLengths.set(doc, length);
        }
        return length;
      },
    }) ?? 0;
  const tooLong = htmlLength > MAX_TEXT_LENGTH;

  // ProseMirror writes only its own attributes and leaves these two alone.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    const dom = editor.view.dom;
    if (tooLong) {
      dom.setAttribute('aria-invalid', 'true');
      dom.setAttribute('aria-describedby', tooLongId);
    } else {
      dom.removeAttribute('aria-invalid');
      dom.removeAttribute('aria-describedby');
    }
  }, [editor, tooLong, tooLongId]);

  if (!editor) return null;

  const numberFormat = new Intl.NumberFormat(i18n.resolvedLanguage ?? i18n.language);

  return (
    <>
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
          onOpenImageSettings={openImageSettings}
          editorLabel={ariaLabel}
        />
        {imageRefusal && (
          <Alert severity="error" sx={{ borderRadius: 0 }}>
            {t(imageRefusal)}
          </Alert>
        )}
        {pasteImagesLeftOut > 0 && (
          <Alert severity="info" sx={{ borderRadius: 0 }} onClose={() => setPasteImagesLeftOut(0)}>
            {t('richTextEditor.pasteImagesLeftOut', { count: pasteImagesLeftOut })}
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
              '& blockquote': {
                margin: '0 0 0.75em',
                paddingLeft: '1em',
                borderLeft: '3px solid',
                borderLeftColor: 'divider',
                color: 'text.secondary',
              },
              // A simple table: thin lines, a bold header row, no fixed widths.
              '& table': {
                borderCollapse: 'collapse',
                width: '100%',
                margin: '0.75em 0',
              },
              '& th, & td': {
                border: '1px solid',
                borderColor: 'divider',
                padding: '0.4em 0.6em',
                verticalAlign: 'top',
                textAlign: 'left',
                minWidth: '3em',
              },
              '& th': { fontWeight: 600, bgcolor: 'action.hover' },
              '& th > p, & td > p': { margin: 0 },
              '& .selectedCell': { bgcolor: 'action.selected' },
              '& hr': {
                border: 'none',
                borderTop: '1px solid',
                borderTopColor: 'divider',
                margin: '1em 0',
              },
              '& hr.ProseMirror-selectednode': {
                borderTopColor: 'primary.main',
              },
              '& a': { color: 'primary.main', textDecoration: 'underline' },
              // An image is a block of its own. `data-width` is a share of the text
              // column, `data-align` the side it stands on (left when not set). With
              // a caption the figure carries both and the picture fills it.
              '& img, & figure': {
                display: 'block',
                maxWidth: '100%',
                margin: '0.75em auto 0.75em 0',
              },
              '& img': {
                height: 'auto',
                borderRadius: 1,
              },
              '& figure > img': { width: '100%', margin: 0 },
              '& figure': { width: 'fit-content' },
              '& figcaption': {
                marginTop: '0.25em',
                fontSize: '0.875em',
                color: 'text.secondary',
              },
              '& [data-width="25"]': { width: '25%' },
              '& [data-width="50"]': { width: '50%' },
              '& [data-width="75"]': { width: '75%' },
              '& [data-width="100"]': { width: '100%' },
              '& [data-align="center"]': { marginLeft: 'auto', marginRight: 'auto' },
              '& [data-align="right"]': { marginLeft: 'auto', marginRight: 0 },
              '& img.ProseMirror-selectednode, & figure.ProseMirror-selectednode': {
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
          <RichTextImageMenu editor={editor} onOpenSettings={openImageSettings} />
          <RichTextTableMenu editor={editor} />
          <RichTextSelectionMenu editor={editor} onOpenLink={openLinkDialog} />
        </Box>
        {mediaOrg && (
          <RichTextImageDialog
            key={imageDialogRound}
            open={imageDialogOpen}
            orgSlug={mediaOrg}
            onClose={() => setImageDialogOpen(false)}
            onInsert={({ src, alt, caption }) => {
              editor
                .chain()
                .focus()
                .insertContent({ type: 'image', attrs: caption ? { src, alt, caption } : { src, alt } })
                .run();
            }}
          />
        )}
        <RichTextImageSettingsDialog
          open={imageSettingsOpen}
          image={{
            alt: (editor.getAttributes('image').alt as string | null | undefined) ?? null,
            caption: (editor.getAttributes('image').caption as string | null | undefined) ?? null,
            width: (editor.getAttributes('image').width as string | null | undefined) ?? null,
            align: (editor.getAttributes('image').align as string | null | undefined) ?? null,
          }}
          onClose={() => setImageSettingsOpen(false)}
          onSave={(settings: ImageSettings) => {
            setImageSettingsOpen(false);
            editor.chain().focus().updateAttributes('image', settings).run();
          }}
        />
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
      {tooLong && (
        <FormHelperText id={tooLongId} error>
          {t('richTextEditor.tooLong', {
            count: numberFormat.format(htmlLength),
            max: numberFormat.format(MAX_TEXT_LENGTH),
          })}
        </FormHelperText>
      )}
    </>
  );
};

export default RichTextEditor;
