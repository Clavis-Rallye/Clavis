// JSX types for the Material Web custom elements. React 19 supports custom
// elements natively, so only the types are needed here.
// The side-effect import that registers the element is in ClavisCircularProgress.tsx.

import type { MdCircularProgress } from '@material/web/progress/circular-progress.js';

type CustomElementAttrs<E extends HTMLElement> = React.DetailedHTMLProps<
  React.HTMLAttributes<E>,
  E
>;

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'md-circular-progress': CustomElementAttrs<MdCircularProgress> & {
        value?: number;
        max?: number;
        indeterminate?: boolean;
        'four-color'?: boolean;
      };
    }
  }
}
