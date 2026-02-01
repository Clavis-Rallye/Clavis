# Design System — App Implementation

This document holds how the App implements a screen: where the design tokens live, the steps to build a screen and the rules every screen follows. It is not the design language itself. The design language is not part of this repository: its colour roles, typography, iconography, spacing and components. The tokens in `src/frontend/src/theme.ts` are what the App builds against.

## Design sources

The screens and the design tokens were exported from the design tool. The export is not part of this repository. The colour, type and spacing tokens that the App uses are defined in `src/frontend/src/theme.ts`, and the rules below are the reference for building a screen.

## Building a screen

1. Take the measurements, colours and type styles from the tokens of the frontend theme — never eyeball them from a screenshot.
2. Implement with Material 3 components first; custom components only where M3 is insufficient.
3. Check the result against the implementation rules below.

## Implementation rules

1. Tokens are the source of truth — never hardcode hex values
2. Use semantic token names, not raw values
3. M3 components first, custom only when necessary
4. Accessibility: WCAG contrast ratios, icon + text combinations, clear hierarchy
