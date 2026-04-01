// Types of the vitest-axe matcher under Vitest 5. Upstream `vitest-axe/extend-expect`
// augments `Vi.Assertion` of Vitest 1.x, which Vitest 5 no longer has.
// Vitest 5 reads custom matchers from `Matchers<R, T>`, where `R` is the return
// type of the chain (sync or promise). `setup.ts` registers the matcher at run time.
declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface Matchers<R = unknown, T = unknown> {
    /** vitest-axe: the aXe results object reports no accessibility violations. */
    toHaveNoViolations: () => R;
  }
}

export {};
