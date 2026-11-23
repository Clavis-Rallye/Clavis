import React, { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Divider } from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { findParentNode } from '@tiptap/core';
import { TableMap } from '@tiptap/pm/tables';
import { useEditorState, type Editor } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import { TABLE_MAX_COLUMNS } from '../utils/richTextTables';

const ITEM = 'data-table-menu-item';
const MENU = 'data-table-menu';

/**
 * Moves the focus into the table menu that stands in `root` (the element that
 * holds the editor). Returns whether there was a menu to move into: the menu
 * exists in the page only while the cursor is in a table.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const focusTableMenu = (root: HTMLElement | null): boolean => {
  const menu = root?.querySelector<HTMLElement>(`[${MENU}]`);
  if (!menu) return false;
  const target =
    menu.querySelector<HTMLElement>(`[${ITEM}][tabindex="0"]:not(:disabled)`) ??
    menu.querySelector<HTMLElement>(`[${ITEM}]:not(:disabled)`);
  if (!target) return false;
  target.focus();
  return true;
};

const tableOf = (editor: Editor) => findParentNode((node) => node.type.name === 'table')(editor.state.selection);

interface TableState {
  rows: number;
  columns: number;
  headerRow: boolean;
}

const readTableState = (editor: Editor): TableState | null => {
  const table = tableOf(editor);
  if (!table) return null;
  const map = TableMap.get(table.node);
  const first = table.node.firstChild;
  let headerRow = first !== null && first.childCount > 0;
  first?.forEach((cell) => {
    if (cell.type.name !== 'tableHeader') headerRow = false;
  });
  return { rows: map.height, columns: map.width, headerRow };
};

const coarsePointer = '@media (pointer: coarse)';

type Action = 'rowAbove' | 'rowBelow' | 'columnLeft' | 'columnRight' | 'removeRow' | 'removeColumn' | 'headerRow' | 'remove';

const ACTIONS: { id: Action; labelKey: string; group: 'add' | 'remove' | 'table' }[] = [
  { id: 'rowAbove', labelKey: 'richTextEditor.tableRowAbove', group: 'add' },
  { id: 'rowBelow', labelKey: 'richTextEditor.tableRowBelow', group: 'add' },
  { id: 'columnLeft', labelKey: 'richTextEditor.tableColumnLeft', group: 'add' },
  { id: 'columnRight', labelKey: 'richTextEditor.tableColumnRight', group: 'add' },
  { id: 'removeRow', labelKey: 'richTextEditor.tableRemoveRow', group: 'remove' },
  { id: 'removeColumn', labelKey: 'richTextEditor.tableRemoveColumn', group: 'remove' },
  { id: 'headerRow', labelKey: 'richTextEditor.tableHeaderRow', group: 'table' },
  { id: 'remove', labelKey: 'richTextEditor.tableRemove', group: 'table' },
];

/**
 * The small menu next to a table while the cursor is in it: add and remove
 * rows and columns, the header row, remove the table. It takes no focus when
 * it shows. Alt+F10 (see the editor) or a click moves the focus in, the arrow
 * keys move inside, Escape returns the focus to the cell it came from.
 */
const RichTextTableMenu: React.FC<{ editor: Editor }> = ({ editor }) => {
  const { t } = useTranslation('common');
  const containerRef = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const limitNoteId = useId();

  const state = useEditorState({
    editor,
    selector: ({ editor: current }) => readTableState(current),
    equalityFn: (a, b) =>
      a === b || (a !== null && b !== null && a.rows === b.rows && a.columns === b.columns && a.headerRow === b.headerRow),
  });

  const atColumnLimit = state !== null && state.columns >= TABLE_MAX_COLUMNS;

  const run = (id: Action) => {
    // A table has at most TABLE_MAX_COLUMNS columns, the button is disabled at
    // the limit and this is the second guard.
    if (atColumnLimit && (id === 'columnLeft' || id === 'columnRight')) return;
    const chain = editor.chain().focus();
    switch (id) {
      case 'rowAbove':
        chain.addRowBefore().run();
        break;
      case 'rowBelow':
        chain.addRowAfter().run();
        break;
      case 'columnLeft':
        chain.addColumnBefore().run();
        break;
      case 'columnRight':
        chain.addColumnAfter().run();
        break;
      case 'removeRow':
        chain.deleteRow().run();
        break;
      case 'removeColumn':
        chain.deleteColumn().run();
        break;
      case 'headerRow':
        chain.toggleHeaderRow().run();
        break;
      case 'remove':
        chain.deleteTable().run();
        break;
    }
  };

  const isDisabled = (id: Action): boolean => {
    if (!state) return true;
    if (id === 'removeRow') return state.rows <= 1;
    if (id === 'removeColumn') return state.columns <= 1;
    if (id === 'columnLeft' || id === 'columnRight') return state.columns >= TABLE_MAX_COLUMNS;
    return false;
  };

  const ids = ACTIONS.map((a) => a.id) as string[];
  const enabledIds = ACTIONS.filter((a) => !isDisabled(a.id)).map((a) => a.id) as string[];
  // The one tab stop sits on the last item that had the focus, or on the first
  // item that can act.
  const tabStopId = activeId !== null && enabledIds.includes(activeId) ? activeId : (enabledIds[0] ?? ids[0]);

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

  const itemProps = (id: Action) => ({
    [ITEM]: id,
    tabIndex: tabStopId === id ? 0 : -1,
    onFocus: () => setActiveId(id),
  });

  return (
    <BubbleMenu
      editor={editor}
      pluginKey="tableMenu"
      updateDelay={0}
      // The image menu wins: an image selected in a cell shows the image menu
      // only. The menu shows while the editor or the menu has the focus, so a
      // document that starts with a table does not open with a menu.
      shouldShow={({ editor: current, view, element }) =>
        current.isEditable &&
        current.isActive('table') &&
        !current.isActive('image') &&
        (view.hasFocus() || element.contains(document.activeElement))
      }
      getReferencedVirtualElement={() => {
        const table = tableOf(editor);
        const dom = table ? editor.view.nodeDOM(table.pos) : null;
        return dom instanceof HTMLElement ? { getBoundingClientRect: () => dom.getBoundingClientRect() } : null;
      }}
      options={{ placement: 'top-start', offset: 8 }}
      style={{ zIndex: 3 }}
    >
      <Box
        ref={containerRef}
        {...{ [MENU]: '' }}
        role="toolbar"
        aria-orientation="horizontal"
        aria-label={t('richTextEditor.tableMenu')}
        onKeyDown={handleKeyDown}
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '4px',
          maxWidth: 'min(640px, calc(100vw - 32px))',
          p: 0.75,
          bgcolor: 'background.paper',
          border: 1,
          borderColor: 'divider',
          borderRadius: 1.5,
          boxShadow: 3,
        }}
      >
        {ACTIONS.map(({ id, labelKey, group }, index) => {
          const previous = ACTIONS[index - 1];
          const pressed = id === 'headerRow' ? (state?.headerRow ?? false) : undefined;
          const label = t(labelKey);
          const limited = atColumnLimit && (id === 'columnLeft' || id === 'columnRight');
          return (
            <React.Fragment key={id}>
              {previous && previous.group !== group && (
                <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 0.75 }} />
              )}
              <Button
                size="small"
                color={id === 'remove' ? 'error' : 'inherit'}
                startIcon={id === 'remove' ? <DeleteIcon fontSize="small" /> : undefined}
                disabled={isDisabled(id)}
                {...(limited ? { 'aria-describedby': limitNoteId } : {})}
                {...(pressed === undefined ? {} : { 'aria-pressed': pressed })}
                {...itemProps(id)}
                onClick={() => run(id)}
                sx={{
                  textTransform: 'none',
                  whiteSpace: 'nowrap',
                  minHeight: 36,
                  ...(id === 'remove'
                    ? {}
                    : {
                        color: pressed ? 'primary.main' : 'text.secondary',
                        bgcolor: pressed ? 'action.selected' : 'transparent',
                        fontWeight: pressed ? 600 : 400,
                      }),
                  [coarsePointer]: { minHeight: 40 },
                }}
              >
                {label}
              </Button>
              {atColumnLimit && id === 'columnRight' && (
                <Box
                  component="span"
                  id={limitNoteId}
                  sx={{ color: 'text.secondary', fontSize: '0.8125rem', lineHeight: 1.75, px: 0.5 }}
                >
                  {t('richTextEditor.tableColumnLimit')}
                </Box>
              )}
            </React.Fragment>
          );
        })}
      </Box>
    </BubbleMenu>
  );
};

export default RichTextTableMenu;
