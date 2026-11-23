import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import type { SxProps, Theme } from '@mui/material/styles';
import { isHtml } from '../utils/richtext';
import { useTranslation } from 'react-i18next';
import { sanitizeRichText, wrapTablesForScroll } from '../utils/sanitizeRichText';
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
  const { t } = useTranslation('common');
  const rich = isHtml(html);
  const tableLabel = t('safeHtml.richTextTableScroll');

  const clean = useMemo(
    () => (rich ? wrapTablesForScroll(sanitizeRichText(html), tableLabel) : ''),
    [html, rich, tableLabel],
  );

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
        // Size and position of images and figures (editor contract: data-width, data-align).
        '& figure': { display: 'block', width: 'fit-content', maxWidth: '100%', mx: 0, my: 1.5 },
        '& figure img': { width: '100%', my: 0 },
        '& figure[data-width="25"], & img[data-width="25"]': { width: '25%' },
        '& figure[data-width="50"], & img[data-width="50"]': { width: '50%' },
        '& figure[data-width="75"], & img[data-width="75"]': { width: '75%' },
        '& figure[data-width="100"], & img[data-width="100"]': { width: '100%' },
        '& figure img[data-width]': { width: '100%' },
        '& figure[data-align="center"], & img[data-align="center"]': { ml: 'auto', mr: 'auto' },
        '& figure[data-align="right"], & img[data-align="right"]': { ml: 'auto', mr: 0 },
        '& figcaption': { mt: 0.75, fontSize: '0.85em', lineHeight: 1.4, color: 'text.secondary' },
        '& blockquote': {
          mx: 0,
          my: 1.5,
          ps: 2,
          borderInlineStart: '4px solid',
          borderColor: 'divider',
          color: 'text.secondary',
        },
        '& hr': { border: 0, borderTop: '1px solid', borderColor: 'divider', my: 2.5 },
        // Tables scroll inside their own region, so a wide table never widens the page.
        '& div[role="region"]': { maxWidth: '100%', overflowX: 'auto', my: 1.5 },
        // Separate borders with no spacing: the table never stands wider than its region
        // because of its own border. Top and left lines sit on the table, right and bottom on the cells.
        '& table': {
          borderCollapse: 'separate',
          borderSpacing: 0,
          width: '100%',
          borderTop: '1px solid',
          borderLeft: '1px solid',
          borderColor: 'divider',
        },
        '& th, & td': {
          borderRight: '1px solid',
          borderBottom: '1px solid',
          borderColor: 'divider',
          px: 0.75,
          py: 0.5,
          verticalAlign: 'top',
          textAlign: 'start',
          overflowWrap: 'anywhere',
          hyphens: 'auto',
        },
        '& th': { fontWeight: 700 },
        ...sx,
      }}
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
};

export default SafeHtml;
