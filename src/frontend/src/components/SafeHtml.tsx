import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import type { SxProps, Theme } from '@mui/material/styles';
import { isHtml } from '../utils/richtext';
import { sanitizeRichText } from '../utils/sanitizeRichText';
import LinkifiedText from './LinkifiedText';

interface SafeHtmlProps {
  /** HTML string from the rich-text editor. Sanitised before render. */
  html: string;
  /** Writing direction of the content, for RTL locales. */
  dir?: 'ltr' | 'rtl';
  sx?: SxProps<Theme>;
}

/**
 * Renders every long text a creator writes in RichTextEditor: descriptions,
 * instructions, legal texts. DOMPurify cleans it first with a tight allow-list:
 * no scripts, no event handlers, no inline styles, and images only from the
 * organisation media route.
 *
 * A text without markup is a legacy plain text from before the editor. It
 * renders as pre-wrapped text, and its web addresses become links at render
 * time (`LinkifiedText`). The stored text stays as it is.
 */
const SafeHtml: React.FC<SafeHtmlProps> = ({ html, dir, sx }) => {
  const rich = isHtml(html);

  const clean = useMemo(() => (rich ? sanitizeRichText(html) : ''), [html, rich]);

  if (!rich) {
    return (
      <Box
        component="div"
        dir={dir}
        sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', '& a': { color: 'primary.main' }, ...sx }}
      >
        <LinkifiedText text={html} />
      </Box>
    );
  }

  return (
    <Box
      dir={dir}
      sx={{
        wordBreak: 'break-word',
        '& > :first-of-type': { mt: 0 },
        '& > :last-child': { mb: 0 },
        '& p': { mb: 1.5 },
        // Content headings sit below the page title, which is often an h5-sized h1.
        '& h2': { mt: 2.5, mb: 1, fontSize: '1.2em', fontWeight: 600, lineHeight: 1.25 },
        '& h3': { mt: 2, mb: 0.75, fontSize: '1.05em', fontWeight: 600, lineHeight: 1.3 },
        '& ul, & ol': { pl: 3, mb: 1.5 },
        '& li': { mb: 0.5 },
        '& a': { color: 'primary.main' },
        '& img': { display: 'block', maxWidth: '100%', height: 'auto', borderRadius: 1.5, my: 1.5 },
        ...sx,
      }}
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
};

export default SafeHtml;
