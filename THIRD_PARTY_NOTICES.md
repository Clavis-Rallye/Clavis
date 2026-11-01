# Third-party notices

Clavis contains and uses third-party components. Each keeps its own licence. The EUPL-1.2 of
Clavis does not change these licences. The lockfiles `src/frontend/package-lock.json` and
`src/backend/uv.lock` list every package with its exact version.

The builds collect the full notices of what they ship. The frontend build writes
`third-party-licenses.txt` beside the bundle, and every installation serves it at
`/third-party-licenses.txt`. The backend image holds `/app/THIRD_PARTY_LICENSES.txt`.

## Frontend bundle

The built frontend contains npm packages under the MIT, ISC, BSD-2-Clause, BSD-3-Clause, 0BSD and
Apache-2.0 licences. The main ones:

| Component | Licence | Copyright |
|---|---|---|
| React, React DOM, React Router | MIT | Meta Platforms, Inc. and affiliates, and the React Router authors |
| MUI (`@mui/material`, `@mui/icons-material`) | MIT | Call-Em-All. The icon shapes derive from Google Material Icons, Apache-2.0 |
| MUI X Charts | MIT, includes parts of d3 under ISC | Material-UI SAS |
| Material Web (`@material/web`) | Apache-2.0 | Google LLC |
| Leaflet | BSD-2-Clause | Volodymyr Agafonkin, CloudMade |
| html5-qrcode | Apache-2.0, includes ZXing-js under Apache-2.0 | the html5-qrcode authors |
| DOMPurify | used under Apache-2.0 (dual MPL-2.0 or Apache-2.0) | Cure53 and other contributors |
| Workbox | MIT | Google LLC |
| Emotion, dnd kit, Tiptap, i18next, axios, qrcode.react and others | MIT | their authors |

Fonts and icons below are bundled as well.

## Fonts

| Font | Licence | Copyright |
|---|---|---|
| Noto Sans | SIL Open Font License 1.1 | The Noto Project Authors |
| Roboto | SIL Open Font License 1.1 | The Roboto Project Authors |
| Roboto Mono | SIL Open Font License 1.1 | The Roboto Mono Project Authors |
| Twemoji Country Flags (`src/frontend/public/TwemojiCountryFlags.woff2`) | font build MIT (country-flag-emoji-polyfill, TalkJS). Flag graphics CC BY 4.0 | Flag graphics: Twitter, Inc. and other contributors |

The fonts are delivered through Fontsource. The OFL text is part of each Fontsource package. The
flag graphics are licensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/

## Icons

Material Symbols in `src/frontend/src/assets/icons/`: Google LLC, Apache-2.0.

## Design and artwork

The fox mascot and the original visuals were created by Antonia Hauf in the first phase of the
project. The City of Augsburg provided them as the design of the application. Further
illustrations were derived from them in the project. The logo, the mascot and the illustrations
lie in
`src/frontend/public/` (the logo and icon files), `src/frontend/src/assets/logos/` and
`src/frontend/src/assets/onboarding/`.

They ship as part of Clavis and are licensed under the EUPL-1.2, like the rest of the software.
Whoever runs, changes or passes on Clavis may use them under the same terms.

## Backend image

The backend contains Python packages under the MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC,
PSF-2.0, MIT-0, MIT-CMU, Unlicense and MPL-2.0 licences, and parts in the public domain. Named because of their terms:

| Component | Licence | Note |
|---|---|---|
| certifi | MPL-2.0 | Used unmodified. Source: https://github.com/certifi/python-certifi |
| Pillow | MIT-CMU | Its wheels include libjpeg-turbo, libpng, libtiff, libwebp, libavif, OpenJPEG, HarfBuzz, Little CMS, zlib, zstd, brotli, liblzma, libxcb and FreeType |

Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights
reserved.

## API documentation pages

The repository holds two bundles for the pages `/api/docs` and `/api/redoc`, in
`src/backend/app/static/api-docs/`. `VERSIONS.txt` in that folder names their versions.

| Component | Licence | Copyright |
|---|---|---|
| Swagger UI (`swagger-ui-dist`) | Apache-2.0. `LICENSE` and `NOTICE` lie beside the bundle | SmartBear Software Inc. |
| ReDoc (`redoc`) | MIT. `LICENSE` lies beside the bundle | Rebilly, Inc. |

## Development tools

These tools build and test Clavis. They are not part of the built software. Examples: Playwright
(Apache-2.0), axe-core (MPL-2.0), lightningcss (MPL-2.0), caniuse-lite (CC BY 4.0), Vite (MIT),
Vitest (MIT), ruff (MIT), mypy (MIT), pytest (MIT).

## Container images

The images build on `python:3.12-slim` (Debian) and `nginx:1.31-alpine` (Alpine). They contain
operating-system packages under their own licences, some under GPL-2.0 or LGPL. Sources:
https://sources.debian.org and https://gitlab.alpinelinux.org/alpine/aports

The compose files pull `postgres` (PostgreSQL License), `caddy` (Apache-2.0) and `dxflrs/garage`
(AGPL-3.0) from their publishers. These are not part of Clavis. Garage runs as a separate service
over the S3 API. Any S3-compatible store can replace it.

## Map data and services

Map data © OpenStreetMap contributors, available under the Open Database Licence (ODbL). The map
must keep the attribution visible.

The default configuration loads map tiles from the OpenStreetMap Foundation and can send address
searches to Nominatim. Both services have usage policies:

- https://operations.osmfoundation.org/policies/tiles/
- https://operations.osmfoundation.org/policies/nominatim/

An installation with more than light use must configure its own tile and geocoding service. An
operator who uses Nominatim must set `GEOCODER_CONTACT`.
