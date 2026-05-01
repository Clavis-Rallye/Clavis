import { useContext } from 'react';

import { WizardStateContext } from './WizardStateContext';

/**
 * Throws outside `<WizardStateProvider>`. `App.tsx` mounts the provider inside
 * `<AuthProvider>` and outside `<Routes>`.
 */
export function useWizardState() {
  const ctx = useContext(WizardStateContext);
  if (!ctx) {
    throw new Error(
      'useWizardState must be used within a <WizardStateProvider>',
    );
  }
  return ctx;
}
