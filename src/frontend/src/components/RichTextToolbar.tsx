import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Box, Divider, IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from '@mui/material';
import FormatBoldIcon from '@mui/icons-material/FormatBold';
import FormatItalicIcon from '@mui/icons-material/FormatItalic';
import FormatUnderlinedIcon from '@mui/icons-material/FormatUnderlined';
import TitleIcon from '@mui/icons-material/Title';
import FormatListNumberedIcon from '@mui/icons-material/FormatListNumbered';
import FormatListBulletedIcon from '@mui/icons-material/FormatListBulleted';
import LinkIcon from '@mui/icons-material/Link';
import LinkOffIcon from '@mui/icons-material/LinkOff';
import ImageIcon from '@mui/icons-material/Image';
import MoreHorizIcon from '@mui/icons-material/MoreHoriz';
import { useEditorState, type Editor } from '@tiptap/react';

/**
 * One tool of the toolbar. The toolbar renders its controls, and the entries of
 * its "more" menu, from an ordered list of these. A new tool is a new entry in
 * `buildToolbarTools`: no other place has to know it.
 */
export interface ToolDefinition {
  id: string;
  /** Tools of one group stand together, a divider separates the groups. */
  group: number;
  /** Tooltip, accessible name and menu text. May depend on the selection. */
  label: (editor: Editor) => string;
  icon: (editor: Editor) => React.ReactNode;
  /**
   * Whether the tool is switched on at the cursor. A tool with this function is
   * a toggle (`aria-pressed`, or `menuitemcheckbox` in the menu). Without it
   * the tool is a plain action.
   */
  isActive?: (editor: Editor) => boolean;
  /** Extra reason to disable the tool. The toolbar's own `disabled` always wins. */
  isDisabled?: (editor: Editor) => boolean;
  run: (editor: Editor) => void;
}

interface ToolHandlers {
  t: TFunction;
  /** Whether the image tool exists at all (the surface has a media store). */
  allowImages: boolean;
  onOpenLink: () => void;
  onOpenImage: () => void;
}

/** The tools of the editor, in the order of the toolbar. */
const buildToolbarTools = ({ t, allowImages, onOpenLink, onOpenImage }: ToolHandlers): ToolDefinition[] => {
  const tools: ToolDefinition[] = [
    {
      id: 'bold',
      group: 0,
      label: () => t('richTextEditor.bold'),
      icon: () => <FormatBoldIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('bold'),
      run: (editor) => editor.chain().focus().toggleBold().run(),
    },
    {
      id: 'italic',
      group: 0,
      label: () => t('richTextEditor.italic'),
      icon: () => <FormatItalicIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('italic'),
      run: (editor) => editor.chain().focus().toggleItalic().run(),
    },
    {
      id: 'underline',
      group: 0,
      label: () => t('richTextEditor.underline'),
      icon: () => <FormatUnderlinedIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('underline'),
      run: (editor) => editor.chain().focus().toggleUnderline().run(),
    },
    {
      id: 'heading',
      group: 1,
      label: () => t('richTextEditor.heading'),
      icon: () => <TitleIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('heading', { level: 2 }),
      run: (editor) => editor.chain().focus().toggleHeading({ level: 2 }).run(),
    },
    {
      id: 'orderedList',
      group: 1,
      label: () => t('richTextEditor.numberedList'),
      icon: () => <FormatListNumberedIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('orderedList'),
      run: (editor) => editor.chain().focus().toggleOrderedList().run(),
    },
    {
      id: 'bulletList',
      group: 1,
      label: () => t('richTextEditor.bulletList'),
      icon: () => <FormatListBulletedIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('bulletList'),
      run: (editor) => editor.chain().focus().toggleBulletList().run(),
    },
    {
      id: 'link',
      group: 2,
      label: (editor) => (editor.isActive('link') ? t('richTextEditor.removeLink') : t('richTextEditor.addLink')),
      icon: (editor) =>
        editor.isActive('link') ? <LinkOffIcon fontSize="small" /> : <LinkIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('link'),
      run: (editor) => {
        if (editor.isActive('link')) {
          editor.chain().focus().unsetLink().run();
          return;
        }
        // The editor's own dialog asks for the address and checks it.
        onOpenLink();
      },
    },
  ];
  if (allowImages) {
    tools.push({
      id: 'image',
      group: 2,
      label: () => t('richTextEditor.image'),
      icon: () => <ImageIcon fontSize="small" />,
      isActive: (editor) => editor.isActive('image'),
      run: () => onOpenImage(),
    });
  }
  return tools;
};

// Widths the fit is computed with. A control is 40 px on a touch screen and a
// little less with a mouse: the larger value keeps the estimate on the safe side.
const SLOT_WIDTH = 42;
const DIVIDER_WIDTH = 10;
const MAX_ROWS = 2;
const MORE_ID = '__more';

/** How many tools stand in the bar when `width` is the toolbar's content width. */
const countVisibleTools = (tools: ToolDefinition[], width: number | null): number => {
  if (width === null) return tools.length;
  const fits = (count: number, withMore: boolean): boolean => {
    let rows = 1;
    let used = 0;
    const place = (needed: number) => {
      if (used > 0 && used + needed > width) {
        rows += 1;
        used = 0;
      }
      used += needed;
    };
    for (let i = 0; i < count; i += 1) {
      const startsGroup = i > 0 && tools[i].group !== tools[i - 1].group;
      place(SLOT_WIDTH + (startsGroup ? DIVIDER_WIDTH : 0));
    }
    if (withMore) place(SLOT_WIDTH);
    return rows <= MAX_ROWS;
  };
  if (fits(tools.length, false)) return tools.length;
  for (let count = tools.length - 1; count > 0; count -= 1) {
    if (fits(count, true)) return count;
  }
  return 0;
};

interface RichTextToolbarProps {
  editor: Editor;
  disabled?: boolean;
  /** Whether the image tool exists (the surface has a media store to upload to). */
  allowImages: boolean;
  onOpenLink: () => void;
  onOpenImage: () => void;
  /** The editor's own label, put in front of the toolbar's label. */
  editorLabel?: string;
  /** Replaces the tool list. For tests of the toolbar behaviour. */
  tools?: ToolDefinition[];
}

const coarsePointer = '@media (pointer: coarse)';

const RichTextToolbar: React.FC<RichTextToolbarProps> = ({
  editor,
  disabled = false,
  allowImages,
  onOpenLink,
  onOpenImage,
  editorLabel,
  tools: toolsOverride,
}) => {
  const { t } = useTranslation('common');
  const containerRef = useRef<HTMLDivElement>(null);
  const [moreButton, setMoreButton] = useState<HTMLButtonElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const tools = useMemo(
    () => toolsOverride ?? buildToolbarTools({ t, allowImages, onOpenLink, onOpenImage }),
    [toolsOverride, t, allowImages, onOpenLink, onOpenImage],
  );

  // The editor does not re-render its parent on every transaction. The toolbar
  // subscribes itself, so a pressed state follows the cursor. The selector's
  // result is a plain string: it changes only when a label, a state or a
  // disabled flag of a tool does.
  useEditorState({
    editor,
    selector: ({ editor: current }) =>
      tools
        .map((tool) =>
          [tool.id, tool.label(current), tool.isActive?.(current), tool.isDisabled?.(current)].join(':'),
        )
        .join('|'),
  });

  // The fit follows the toolbar's own width, not the window's: the editor sits
  // in narrow columns, too.
  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (!entry) return;
      const next = Math.floor(entry.contentRect.width);
      setWidth((current) => (current === next ? current : next));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const visibleCount = countVisibleTools(tools, width);
  const visibleTools = tools.slice(0, visibleCount);
  const menuTools = tools.slice(visibleCount);
  const hasMenu = menuTools.length > 0;

  const isToolDisabled = useCallback(
    (tool: ToolDefinition) => disabled || (tool.isDisabled?.(editor) ?? false),
    [disabled, editor],
  );

  // One tab stop: the control that last had the focus, or the first one that can.
  const enabledIds = [
    ...visibleTools.filter((tool) => !isToolDisabled(tool)).map((tool) => tool.id),
    ...(hasMenu && !disabled ? [MORE_ID] : []),
  ];
  const tabStopId = activeId !== null && enabledIds.includes(activeId) ? activeId : (enabledIds[0] ?? null);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const container = containerRef.current;
    if (!container || !(event.target instanceof Node) || !container.contains(event.target)) return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const items = Array.from(container.querySelectorAll<HTMLButtonElement>('[data-toolbar-item]')).filter(
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

  const toolbarLabel = editorLabel
    ? `${editorLabel}: ${t('richTextEditor.toolbar')}`
    : t('richTextEditor.toolbar');

  return (
    <Box
      ref={containerRef}
      role="toolbar"
      aria-orientation="horizontal"
      aria-label={toolbarLabel}
      onKeyDown={handleKeyDown}
      sx={{
        // Stays in view below the fixed header of the app shell (AppShell.tsx:
        // 64 px, 72 px from the `sm` breakpoint) while a long text scrolls.
        position: 'sticky',
        top: { xs: 64, sm: 72 },
        zIndex: 2,
        bgcolor: 'background.paper',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '2px',
        px: 1,
        py: 0.5,
        borderBottom: 1,
        borderColor: 'divider',
        [`& .MuiIconButton-root`]: {
          [coarsePointer]: { width: 40, height: 40 },
        },
      }}
    >
      {visibleTools.map((tool, index) => {
        const startsGroup = index > 0 && tool.group !== visibleTools[index - 1].group;
        const label = tool.label(editor);
        const toggle = tool.isActive !== undefined;
        const active = toggle ? tool.isActive!(editor) : false;
        return (
          <Box key={tool.id} sx={{ display: 'flex', alignItems: 'center' }}>
            {startsGroup && <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 0.75 }} />}
            <Tooltip title={label}>
              <span>
                <IconButton
                  size="small"
                  data-toolbar-item={tool.id}
                  aria-label={label}
                  aria-pressed={toggle ? active : undefined}
                  disabled={isToolDisabled(tool)}
                  tabIndex={tabStopId === tool.id ? 0 : -1}
                  onFocus={() => setActiveId(tool.id)}
                  onClick={() => tool.run(editor)}
                  sx={{
                    color: active ? 'primary.main' : 'text.secondary',
                    bgcolor: active ? 'action.selected' : 'transparent',
                  }}
                >
                  {tool.icon(editor)}
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        );
      })}
      {hasMenu && (
        <>
          <Tooltip title={t('richTextEditor.moreTools')}>
            <span>
              <IconButton
                ref={setMoreButton}
                size="small"
                data-toolbar-item={MORE_ID}
                aria-label={t('richTextEditor.moreTools')}
                aria-haspopup="menu"
                aria-expanded={menuOpen ? 'true' : undefined}
                disabled={disabled}
                tabIndex={tabStopId === MORE_ID ? 0 : -1}
                onFocus={() => setActiveId(MORE_ID)}
                onClick={() => setMenuOpen(true)}
                sx={{ color: 'text.secondary' }}
              >
                <MoreHorizIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Menu
            open={menuOpen}
            anchorEl={moreButton}
            onClose={() => {
              setMenuOpen(false);
              moreButton?.focus();
            }}
            // A chosen tool sends the focus to the text. Closing without a
            // choice returns it to the "more" control (onClose above).
            disableRestoreFocus
            slotProps={{ list: { 'aria-label': t('richTextEditor.moreTools') } }}
          >
            {menuTools.map((tool) => {
              const toggle = tool.isActive !== undefined;
              const active = toggle ? tool.isActive!(editor) : false;
              return (
                <MenuItem
                  key={tool.id}
                  role={toggle ? 'menuitemcheckbox' : 'menuitem'}
                  aria-checked={toggle ? active : undefined}
                  selected={active}
                  disabled={isToolDisabled(tool)}
                  onClick={() => {
                    setMenuOpen(false);
                    tool.run(editor);
                  }}
                  sx={{ [coarsePointer]: { minHeight: 40 } }}
                >
                  <ListItemIcon>{tool.icon(editor)}</ListItemIcon>
                  <ListItemText>{tool.label(editor)}</ListItemText>
                </MenuItem>
              );
            })}
          </Menu>
        </>
      )}
    </Box>
  );
};

export default RichTextToolbar;
