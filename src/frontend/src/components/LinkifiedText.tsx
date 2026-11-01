import React, { useMemo } from 'react';
import { linkifyParts } from '../utils/linkify';

interface LinkifiedTextProps {
  /** A plain text. Markup in it stays text: React escapes every part. */
  text: string;
}

/**
 * A plain text with its web addresses as links (`utils/linkify.ts`).
 *
 * For stored texts without markup: legacy texts from before the editor and the
 * coordinator's notices. A link opens in a new tab, like a link from the
 * editor, so the page with the task stays open behind it. Only `http:` and
 * `https:` addresses become links. The stored text never changes.
 */
const LinkifiedText: React.FC<LinkifiedTextProps> = ({ text }) => {
  const parts = useMemo(() => linkifyParts(text), [text]);
  return (
    <>
      {parts.map((part, idx) =>
        part.kind === 'link' ? (
          <a key={idx} href={part.href} target="_blank" rel="noopener noreferrer">
            {part.text}
          </a>
        ) : (
          <React.Fragment key={idx}>{part.text}</React.Fragment>
        ),
      )}
    </>
  );
};

export default LinkifiedText;
