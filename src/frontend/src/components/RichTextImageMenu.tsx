import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Divider, IconButton, Tooltip } from '@mui/material';
import FormatAlignLeftIcon from '@mui/icons-material/FormatAlignLeft';
import FormatAlignCenterIcon from '@mui/icons-material/FormatAlignCenter';
import FormatAlignRightIcon from '@mui/icons-material/FormatAlignRight';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import DeleteIcon from '@mui/icons-material/Delete';
import { useEditorState, type Editor } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';

const ITEM = 'data-image-menu-item';
const MENU = 'data-image-menu';

const SIZES = [
  { id: '25', labelKey: 'richTextEditor.imageSizeSmall' },
  { id: '50', labelKey: 'richTextEditor.imageSizeMedium' },
  { id: '75', labelKey: 'richTextEditor.imageSizeLarge' },
  { id: '100', labelKey: 'richTextEditor.imageSizeFull' },
  { id: 'original', labelKey: 'richTextEditor.imageSizeOriginal' },
] as const;

const ALIGNS = [
  { id: 'left', labelKey: 'richTextEditor.imageAlignLeft', Icon: FormatAlignLeftIcon },
  { id: 'center', labelKey: 'richTextEditor.imageAlignCenter', Icon: FormatAlignCenterIcon },
  { id: 'right', labelKey: 'richTextEditor.imageAlignRight', Icon: FormatAlignRightIcon },
] as const;

/**
 * Moves the focus into the image menu that stands in `root` (the element that
 * holds the editor). Returns whether there was a menu to move into: the menu
 * exists in the page only while an image is selected.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const focusImageMenu = (root: HTMLElement | null): boolean => {
  const menu = root?.querySelector<HTMLElement>(`[${MENU}]`);
  if (!menu) return false;
  const target =
    menu.querySelector<HTMLElement>(`[${ITEM}][tabindex="0"]`) ?? menu.querySelector<HTMLElement>(`[${ITEM}]`);
  if (!target) return false;
  target.focus();
  return true;
};

interface RichTextImageMenuProps {
  editor: Editor;
  /** Opens the dialog for the description, the caption, the size and the position. */
  onOpenSettings: () => void;
}

const coarsePointer = '@media (pointer: coarse)';

/**
 * The small menu next to a selected image: size, position, description and
 * caption, remove. It takes no focus when it shows. Alt+F10 (see the editor) or
 * a click moves the focus in, the arrow keys move inside, Escape returns to the
 * editor with the image still selected.
 */
const RichTextImageMenu: React.FC<RichTextImageMenuProps> = ({ editor, onOpenSettings }) => {
  const { t } = useTranslation('common');
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  const attributes = useEditorState({
    editor,
    selector: ({ editor: current }) => {
      const { width, align } = current.getAttributes('image') as { width?: string | null; align?: string | null };
      return `${width ?? ''}|${align ?? ''}`;
    },
  });
  const [width = '', align = ''] = attributes.split('|');
  // An image without a size shows at its own size, one without a position on the left.
  const currentSize = width === '' ? 'original' : width;
  const currentAlign = align === '' ? 'left' : align;

  const setAttributes = (attrs: { width?: string | null; align?: string | null }) => {
    editor.chain().updateAttributes('image', attrs).run();
  };

  const itemIds = [...SIZES.map((s) => s.id), ...ALIGNS.map((a) => a.id), 'description', 'remove'] as string[];
  const tabStopId = activeId !== null && itemIds.includes(activeId) ? activeId : itemIds[0];

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const container = containerRef.current;
    if (!container) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      editor.view.focus();
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const items = Array.from(container.querySelectorAll<HTMLButtonElement>(`[${ITEM}]`)).filter(
      (item) => !item.disabled,
    );
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const rtl = getComputedStyle(container).direction === 'rtl';
    let next: number;
    switch (event.key) {
      case 'ArrowRight':
        next = (current + (rtl ? -1 : 1) + items.length) % items.length;
        break;
      case 'ArrowLeft':
        next = (current + (rtl ? 1 : -1) + items.length) % items.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = items.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    items[next].focus();
  };

  const itemProps = (id: string) => ({
    [ITEM]: id,
    tabIndex: tabStopId === id ? 0 : -1,
    onFocus: () => setActiveId(id),
  });

  const pressedSx = (pressed: boolean) => ({
    color: pressed ? 'primary.main' : 'text.secondary',
    bgcolor: pressed ? 'action.selected' : 'transparent',
  });

  return (
    <BubbleMenu
      editor={editor}
      updateDelay={0}
      shouldShow={({ editor: current }) => current.isEditable && current.isActive('image')}
      options={{ placement: 'top', offset: 8 }}
      style={{ zIndex: 3 }}
    >
      <Box
        ref={containerRef}
        {...{ [MENU]: '' }}
        role="toolbar"
        aria-orientation="horizontal"
        aria-label={t('richTextEditor.imageMenu')}
        onKeyDown={handleKeyDown}
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '4px',
          maxWidth: 'min(560px, calc(100vw - 32px))',
          p: 0.75,
          bgcolor: 'background.paper',
          border: 1,
          borderColor: 'divider',
          borderRadius: 1.5,
          boxShadow: 3,
        }}
      >
        <Box role="group" aria-label={t('richTextEditor.imageSize')} sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px' }}>
          {SIZES.map((size) => {
            const pressed = currentSize === size.id;
            return (
              <Button
                key={size.id}
                size="small"
                aria-pressed={pressed}
                {...itemProps(size.id)}
                onClick={() => setAttributes({ width: size.id === 'original' ? null : size.id })}
                sx={{
                  ...pressedSx(pressed),
                  textTransform: 'none',
                  fontWeight: pressed ? 600 : 400,
                  minWidth: 40,
                  minHeight: 36,
                  whiteSpace: 'nowrap',
                  [coarsePointer]: { minHeight: 40 },
                }}
              >
                {t(size.labelKey)}
              </Button>
            );
          })}
        </Box>
        <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 0.75 }} />
        <Box role="group" aria-label={t('richTextEditor.imageAlign')} sx={{ display: 'flex', gap: '2px' }}>
          {ALIGNS.map(({ id, labelKey, Icon }) => {
            const pressed = currentAlign === id;
            const label = t(labelKey);
            return (
              <Tooltip key={id} title={label}>
                <IconButton
                  aria-label={label}
                  aria-pressed={pressed}
                  {...itemProps(id)}
                  onClick={() => setAttributes({ align: id })}
                  sx={{ ...pressedSx(pressed), width: 36, height: 36, [coarsePointer]: { width: 40, height: 40 } }}
                >
                  <Icon fontSize="small" />
                </IconButton>
              </Tooltip>
            );
          })}
        </Box>
        <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 0.75 }} />
        <Button
          size="small"
          startIcon={<EditOutlinedIcon fontSize="small" />}
          {...itemProps('description')}
          onClick={onOpenSettings}
          sx={{ color: 'text.secondary', textTransform: 'none', minHeight: 36, [coarsePointer]: { minHeight: 40 } }}
        >
          {t('richTextEditor.imageDescription')}
        </Button>
        <Button
          size="small"
          color="error"
          startIcon={<DeleteIcon fontSize="small" />}
          {...itemProps('remove')}
          onClick={() => editor.chain().focus().deleteSelection().run()}
          sx={{ textTransform: 'none', minHeight: 36, [coarsePointer]: { minHeight: 40 } }}
        >
          {t('richTextEditor.imageRemove')}
        </Button>
      </Box>
    </BubbleMenu>
  );
};

export default RichTextImageMenu;
