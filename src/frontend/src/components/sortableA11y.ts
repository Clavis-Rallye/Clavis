import type { Announcements, ScreenReaderInstructions, UniqueIdentifier } from '@dnd-kit/core';
import type { TFunction } from 'i18next';

interface SortableItem {
  id: UniqueIdentifier;
  label: string;
}

/**
 * What a screen reader hears while a list is reordered by keyboard.
 *
 * dnd-kit's defaults are English and name items by their id, so the station
 * order read "Picked up draggable item 3f2c…" in the German interface. These
 * texts name the item and its position in the UI language.
 */
export function sortableA11y(
  items: SortableItem[],
  t: TFunction,
): { announcements: Announcements; screenReaderInstructions: ScreenReaderInstructions } {
  const total = items.length;
  const nameOf = (id: UniqueIdentifier) => items.find((item) => item.id === id)?.label ?? '';
  const positionOf = (id: UniqueIdentifier) => items.findIndex((item) => item.id === id) + 1;
  return {
    screenReaderInstructions: { draggable: t('content:sortable.instructions') },
    announcements: {
      onDragStart: ({ active }) =>
        t('content:sortable.pickedUp', { name: nameOf(active.id), position: positionOf(active.id), total }),
      onDragOver: ({ active, over }) =>
        over
          ? t('content:sortable.over', { name: nameOf(active.id), position: positionOf(over.id), total })
          : undefined,
      onDragEnd: ({ active, over }) =>
        over
          ? t('content:sortable.dropped', { name: nameOf(active.id), position: positionOf(over.id), total })
          : t('content:sortable.cancelled', { name: nameOf(active.id) }),
      onDragCancel: ({ active }) => t('content:sortable.cancelled', { name: nameOf(active.id) }),
    },
  };
}
