import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box } from '@mui/material';
import { NodeViewWrapper, type NodeViewProps } from '@tiptap/react';

/** The sizes of the contract, in percent of the text column. */
const WIDTHS = ['25', '50', '75', '100'] as const;
export type ResizeWidth = (typeof WIDTHS)[number];

/** The lowest share the live preview shows while dragging. */
const MIN_PREVIEW = 5;

/**
 * Snaps a share of the text column (in percent) to the nearest of 25, 50, 75
 * and 100. A share below 25 gives 25 and one above 100 gives 100. A share that
 * lies exactly between two sizes (37.5, 62.5, 87.5) gives the larger one.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const snapImageWidth = (percent: number): ResizeWidth => {
  let best: ResizeWidth = WIDTHS[0];
  let bestDistance = Infinity;
  for (const width of WIDTHS) {
    const distance = Math.abs(Number(width) - percent);
    if (distance <= bestDistance) {
      best = width;
      bestDistance = distance;
    }
  }
  return best;
};

interface Metrics {
  /** Width of the image (or figure) before the drag, in pixels. */
  frameWidth: number;
  /** Width of the text column, in pixels. */
  columnWidth: number;
}

interface RichTextImageResizeHandleProps {
  label: string;
  /** Reads the widths when a drag starts. */
  getMetrics: () => Metrics;
  /** The stored width, or null when the image shows at its own size. */
  width: string | null;
  /** How far the image grows for one pixel of pointer travel: 2 for a centred image, else 1. */
  factor: number;
  /** Shows the handle without hover, as on a selected image. */
  visible: boolean;
  /** Where the right edge of the image is, relative to the node view. */
  position: { left: number; top: number };
  /** The share shown while dragging, or null when the drag is over. */
  onPreview: (percent: number | null) => void;
  /** The width the drag ended on. Not called for a click or a cancelled drag. */
  onCommit: (width: ResizeWidth) => void;
}

interface Drag {
  pointerId: number;
  startX: number;
  startWidth: number;
  columnWidth: number;
  percent: number | null;
  target: HTMLElement;
}

/**
 * The handle at the right edge of an image. A mouse or a pen drags it; a touch
 * does nothing here (the image menu is the way for touch and keyboard). The
 * drag follows the pointer on the window, so it ends cleanly outside the editor.
 */
const RichTextImageResizeHandle: React.FC<RichTextImageResizeHandleProps> = ({
  label,
  getMetrics,
  width,
  factor,
  visible,
  position,
  onPreview,
  onCommit,
}) => {
  const dragRef = useRef<Drag | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  // The latest props, read by the window listeners of a drag that is running.
  const latest = useRef({ width, factor, onPreview, onCommit });
  useEffect(() => {
    latest.current = { width, factor, onPreview, onCommit };
  });

  useEffect(() => () => stopRef.current?.(), []);

  const handlePointerDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.pointerType === 'touch' || event.button !== 0 || dragRef.current) return;
    const { frameWidth, columnWidth } = getMetrics();
    if (!(columnWidth > 0) || !(frameWidth > 0)) return;
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget;
    try {
      target.setPointerCapture?.(event.pointerId);
    } catch {
      // The pointer is already gone; the window listeners still end the drag.
    }
    const drag: Drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: frameWidth,
      columnWidth,
      percent: null,
      target,
    };
    dragRef.current = drag;

    const move = (e: PointerEvent) => {
      if (e.pointerId !== drag.pointerId || e.clientX === drag.startX) return;
      const travel = (e.clientX - drag.startX) * latest.current.factor;
      const percent = ((drag.startWidth + travel) / drag.columnWidth) * 100;
      drag.percent = Math.min(100, Math.max(MIN_PREVIEW, percent));
      latest.current.onPreview(drag.percent);
    };
    const finish = (commit: boolean) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      try {
        drag.target.releasePointerCapture?.(drag.pointerId);
      } catch {
        // Capture was released already.
      }
      dragRef.current = null;
      stopRef.current = null;
      latest.current.onPreview(null);
      if (commit && drag.percent !== null) {
        const snapped = snapImageWidth(drag.percent);
        if (snapped !== latest.current.width) latest.current.onCommit(snapped);
      }
    };
    const up = (e: PointerEvent) => {
      if (e.pointerId === drag.pointerId) finish(true);
    };
    const cancel = (e: PointerEvent) => {
      if (e.pointerId === drag.pointerId) finish(false);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    stopRef.current = () => finish(false);
  };

  return (
    <Box
      component="button"
      type="button"
      tabIndex={-1}
      draggable={false}
      aria-label={label}
      title={label}
      data-image-resize-handle=""
      onPointerDown={handlePointerDown}
      onDragStart={(event: React.DragEvent) => event.preventDefault()}
      sx={{
        position: 'absolute',
        left: position.left,
        top: position.top,
        transform: 'translate(-50%, -50%)',
        width: 12,
        height: 40,
        p: 0,
        border: '2px solid',
        borderColor: 'background.paper',
        borderRadius: 1,
        bgcolor: 'primary.main',
        cursor: 'ew-resize',
        touchAction: 'none',
        opacity: visible ? 1 : 0,
        '[data-image-picture]:hover + &, &:hover, &:focus-visible': { opacity: 1 },
        // A touch screen has no use for it; the image menu sets the size there.
        '@media (pointer: coarse)': { display: 'none' },
      }}
    />
  );
};

export default RichTextImageResizeHandle;

/**
 * The editor's view of an image: the picture (or the figure with its caption)
 * as the editor styles it, with the resize handle at its right edge. The
 * stored HTML is written by `renderHTML` of the node and never contains the
 * handle.
 */
export const RichTextImageNodeView: React.FC<NodeViewProps> = ({ node, selected, updateAttributes, extension }) => {
  const { t } = useTranslation('common');
  const wrapperRef = useRef<HTMLDivElement>(null);
  const pictureRef = useRef<HTMLElement>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  const attrs = node.attrs as {
    src: string;
    alt?: string | null;
    title?: string | null;
    width?: string | null;
    align?: string | null;
    caption?: string | null;
  };
  const width = attrs.width ?? null;
  const caption = typeof attrs.caption === 'string' ? attrs.caption.trim() : '';
  const htmlAttributes = (extension.options as { HTMLAttributes?: Record<string, string> }).HTMLAttributes ?? {};

  // The handle stands at the right edge of the picture, wherever the styles put it.
  const place = () => {
    const picture = pictureRef.current;
    if (!picture) return;
    const left = picture.offsetLeft + picture.offsetWidth;
    const top = picture.offsetTop + picture.offsetHeight / 2;
    setPosition((current) => (current.left === left && current.top === top ? current : { left, top }));
  };
  useLayoutEffect(place);
  useEffect(() => {
    const picture = pictureRef.current;
    if (!picture || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(place);
    observer.observe(picture);
    if (picture.parentElement) observer.observe(picture.parentElement);
    return () => observer.disconnect();
  }, [caption]);

  const getMetrics = (): Metrics => ({
    frameWidth: pictureRef.current?.getBoundingClientRect().width ?? 0,
    columnWidth: wrapperRef.current?.getBoundingClientRect().width ?? 0,
  });

  const common = {
    'data-image-picture': '',
    'data-width': width ?? undefined,
    'data-align': attrs.align ?? undefined,
    className: selected ? 'ProseMirror-selectednode' : undefined,
    style: preview !== null ? { width: `${preview}%` } : undefined,
  };
  const imageAttributes = {
    ...htmlAttributes,
    src: attrs.src,
    alt: attrs.alt ?? '',
    title: attrs.title ?? undefined,
    draggable: false,
  };

  return (
    <NodeViewWrapper ref={wrapperRef} style={{ position: 'relative' }}>
      {caption === '' ? (
        <img {...imageAttributes} {...common} ref={pictureRef as React.RefObject<HTMLImageElement>} />
      ) : (
        <figure {...common} ref={pictureRef as React.RefObject<HTMLElement>}>
          <img {...imageAttributes} />
          <figcaption>{caption}</figcaption>
        </figure>
      )}
      <RichTextImageResizeHandle
        label={t('richTextEditor.imageResize')}
        getMetrics={getMetrics}
        width={width}
        factor={attrs.align === 'center' ? 2 : 1}
        visible={selected}
        position={position}
        onPreview={setPreview}
        onCommit={(next) => updateAttributes({ width: next })}
      />
    </NodeViewWrapper>
  );
};
