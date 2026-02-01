# Clavis - Frontend

React + TypeScript + Vite frontend for the Clavis platform.

## Tech Stack

- **React 19** - UI framework
- **TypeScript** - Type safety (strict mode)
- **Vite** - Build tool and dev server
- **MUI (Material UI)** - Component library (Material Design 3)
- **React Router** - Client-side routing
- **Axios** - HTTP client
- **react-i18next / i18next** - Internationalisation (DE default, EN fallback)
- **qrcode.react** - QR code rendering for TOTP provisioning + run join codes
- **html5-qrcode** - QR scanning for participant QR-scan challenges (camera input)
- **@dnd-kit** - Accessible drag-and-drop (station reordering in content management)
- **PWA** - Progressive Web App support (disabled in dev to avoid SW caching hijack)

## Prerequisites

Node.js 22.22.2 or a later 22 release, 24.15.0 or a later 24 release, or 26 or later. The project builds and tests with Node 24. The range comes from the `engines` fields of react-router and jsdom in `package-lock.json`.

## Development

```bash
# Install dependencies
npm install

# Start dev server (http://localhost:3000), pages only, see below
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Lint code
npm run lint

# Type check, the first step of npm run build
npx tsc -b
```

### Against an API

`npm run dev` outside Docker serves the pages, but its `/api` proxy targets `http://api:8000`, a name that exists only inside the Docker network (`vite.config.ts`). The supported way to run the frontend against an API is the Docker development stack, from the repository root:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Accessibility

Targets EN 301 549 with WCAG 2.1 AA. Every page/component test runs an
`axe-core` assertion via `vitest-axe`. `npx vitest run` enforces 0 axe
violations.

Conventions enforced in code:
- Skip-to-main link in both `AppShell` and `RunShell` (`.skip-link` in `index.css`).
- All MUI `<Table>`s wrapped in `<TableContainer role="region" tabIndex={0}>` with `<caption>` + `scope="col"`.
- Real-time regions (chat, feed, run-state) use `aria-live` on a `<Box>` wrapper, not on `<ul>`.
- All MUI icon imports use direct paths (`from '@mui/icons-material/X'`) to avoid Windows EMFILE during vitest.

## Docker

```bash
# Build image
docker build -t clavis-frontend .

# Run container
docker run -p 3000:80 clavis-frontend
```

## Environment Variables

The Docker development stack serves the frontend and the API from the same origin, so the frontend needs no API address of its own. `VITE_API_URL` is empty or the origin of the stack, see `docker-compose.dev.yml`.

## Project Structure

The folders under `src/`, with what each holds:

```
src/
├── api/        # Typed calls of the API, with and without a signed-in user, and the types that match the backend schemas
├── assets/     # Icons, logos and onboarding images
├── auth/       # Authentication context, route protection and login errors
├── components/ # Shared components, with feature subfolders
├── config/     # Instance configuration, read at runtime from /api/public/config
├── contexts/   # The organisation context
├── hooks/      # Shared React hooks
├── i18n/       # Internationalisation setup and the German and English locale files
├── layouts/    # The page shells of the admin and participant areas
├── pages/      # One folder per feature area, plus the entry and account pages
├── qa/         # The optional feedback module for test sessions
├── routes/     # Route guards, redirects and page titles
├── services/   # Calls to /api/public/ of the public pages: legal texts, organisations, published results and runs by their code
├── styles/     # Design tokens and shared style helpers
├── test/       # Shared test setup and cross-page accessibility tests
├── utils/      # Plain helper functions
└── wizard/     # State of the multi-step onboarding wizard
```

The other React contexts live next to what they serve: the session context in `auth/`, the wizard's in `wizard/`, and the contexts of single components in `components/`.

## PWA Features

The app is configured as a Progressive Web App with:
- Service worker for offline support
- Web manifest for installability
- Auto-update strategy
- API response caching

## Building for Production

The production build creates an optimized bundle with:
- Code splitting
- Tree shaking
- Minification
- Source maps
- PWA service worker

The multi-stage Dockerfile builds the app and serves it with nginx.
