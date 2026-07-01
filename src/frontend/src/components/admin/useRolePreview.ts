import { useState } from 'react';
import type { RoleName } from './roleCapabilities';

/**
 * Lets the capability panel show a role *before* the admin chooses it.
 *
 * A MUI `Select` fires `onChange` only on commit. Without a preview the panel
 * would answer "what may this role do?" only after the role is assigned.
 * Hovering or focusing an option previews it. Leaving the list or closing the
 * menu falls back to the selection.
 */
export function useRolePreview(selected: RoleName) {
  const [preview, setPreview] = useState<RoleName | null>(null);

  return {
    /** Role the panel renders: the hovered candidate, else the committed one. */
    displayRole: preview ?? selected,
    /** Call on menu close and when the pointer leaves the list. */
    clearPreview: () => setPreview(null),
    /** Spread onto each MenuItem. */
    previewProps: (role: RoleName) => ({
      onMouseEnter: () => setPreview(role),
      onFocus: () => setPreview(role),
    }),
  };
}
