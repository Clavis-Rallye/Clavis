# Clavis

Clavis is open-source software for city rallies. Teams walk a route of stations and solve
challenges on their phones. Coordinators follow the teams live, talk to them and score their
answers. Organizations host Clavis themselves.

Clavis was built for the City of Augsburg, Office for Social Integration, in the DIWA 4.0
programme.

Clavis 0.9.1 is the first public release. Its history is condensed. Files are grouped by the month
in which they first appeared and by their main author. Each file stands in its released form.

## What Clavis does

- **Rallyes and stations.** Creators build a rallye from stations on a map. Each station has
  challenges of six types: text, quiz, photo, video, audio and a QR code at the station. Runs
  started before keep the retired creative type.
- **Runs.** A coordinator starts a run of a rallye. Participants join with the join link or its QR
  code, as guests or with an account. Participants need no app, only a browser.
- **Live coordination.** A feed for announcements, a chat per team, live scores and a scoreboard.
- **Review and scoring.** Automatic scoring where possible, manual review of photos and texts.
- **Organizations.** One installation serves several organizations. Each has its own users, roles,
  content and legal texts.
- **Languages and accessibility.** German and English user interface, multilingual content, built
  for screen readers and keyboard use.
- **Privacy.** No live tracking of participants.

## Try it on your computer

You need git, and Docker with Compose 2.20 or later (Docker Desktop on Windows and macOS). The
project tests with Compose 2.20, 2.33 and 5.7. Give Docker about 4 GB of memory.

```sh
git clone https://github.com/Clavis-Rallye/Clavis.git
cd Clavis
./scripts/quickstart.sh
```

The script writes `.env` with fresh keys, builds and starts the stack, creates two accounts and
loads a demo rallye. The first run builds the images and takes several minutes. At the end the
script prints the address and the two sign-ins:

- `http://localhost/login`: the administrator of the organization "Demo", with the demo rallye.
- `http://localhost/instance/login`: the operator console above all organizations.

Both accounts set up an authenticator app at the first sign-in. The script keeps the sign-ins in
`.quickstart-accounts`. If port 80 is taken, start it as `CLAVIS_HTTP_PORT=8080 ./scripts/quickstart.sh`,
and the stack answers on `http://localhost:8080`. On Windows, run the script in WSL 2 or Git Bash.

The quickstart gives its proxy network two fixed subnets: `172.16.239.0/24` and the private IPv6
subnet `fd00:172:16:239::/64`. If `docker compose up` stops with "invalid pool request: Pool
overlaps with other one on this address space", another network on this host holds one of them. Set
another one with `CLAVIS_FRONTEND_SUBNET` or `CLAVIS_FRONTEND_SUBNET_V6` in `.env`.

The same steps by hand:

```sh
cp .env.example .env    # then set JWT_SECRET_KEY and HMAC_AUDIT_KEY, each from: openssl rand -hex 32
docker compose up -d --build --wait
curl http://localhost/api/health
docker compose exec -e INSTANCE_ADMIN_PASSWORD='Operator-Pass-2026!' api \
  python -m app.cli instance-admin create --email you@example.org
```

The password needs at least 8 characters and at least 3 of 4 kinds: uppercase letter, lowercase
letter, digit and special character. Choose your own for a real installation.

The health check answers `{"status":"ok","database":"connected","storage":"connected"}`. Register
the first organization at `http://localhost/register/organization`, then load the demo rallye into
it with `docker compose exec api python -m app.cli demo-content load --org-slug <slug> --enable-languages`
(the flag adds German and English to the organization).

Stop the stack with `docker compose stop`. Remove it with all its data with
`docker compose down -v`.

> **Warning:** `docker compose down -v` deletes the database, the stored photos, videos and audio,
> and the logs. The volumes are `clavis-postgres-data`, `clavis-garage-meta`,
> `clavis-garage-data`, `clavis-api-logs`, `clavis-caddy-data` and `clavis-caddy-config`. Their
> names are the same on every machine.

To update a running quickstart to a new version, run this in the repository root:

```sh
git pull
./scripts/quickstart.sh
```

The script builds the new images while the old stack still serves. Then it removes the containers
and the networks of the old stack with `docker compose down --remove-orphans`, never with `-v`, and
starts the new stack. It keeps `.env`, `.quickstart-accounts` and the data. The script does not use
`up` alone, because `up` alone does not rejoin a changed network correctly.

A second stack beside this one needs five settings in the `.env` of the second checkout:
`COMPOSE_PROJECT_NAME` (another project name, for example `clavis-second`), `STACK_SUFFIX` (for
example `-second`), `CLAVIS_HTTP_PORT` (another port, for example `8080`), `CLAVIS_FRONTEND_SUBNET`
(another subnet, for example `172.16.240.0/24`) and `CLAVIS_FRONTEND_SUBNET_V6` (another IPv6
subnet, for example `fd00:172:16:240::/64`). Without its own project name, the second stack
replaces the containers of the first. `STACK_SUFFIX` appends the suffix to the names of the
containers, the networks and the volumes, so the two stacks do not share them. The proxy network of
every stack has the fixed subnets `172.16.239.0/24` and `fd00:172:16:239::/64`, so two stacks on one
host need two pairs of subnets.

The developer guide is [docs/development/getting-started.md](docs/development/getting-started.md).

## Run it on a server

[deployment/README.md](deployment/README.md) installs Clavis on one Linux server with a domain. The
stack in `deployment/standalone/` brings PostgreSQL, the object store and Caddy, which gets the
TLS certificate from Let's Encrypt:

```sh
cd deployment/standalone
./init-env.sh rallye.example.org
docker compose up -d --build --wait
```

The guide also covers the first steps (operator account, legal texts, first organization, place
index for the address search, mail), backups, updates, and a layout for a host that already runs
PostgreSQL, an S3 store and a reverse proxy. The images are built from the source of the release.

The map loads its tiles from OpenStreetMap. The address search asks no outside service until the
operator chooses one. Both services have usage policies that a busy installation must respect. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The operator handbook is at [docs.clavis-rallye.de](https://docs.clavis-rallye.de).

## How it is built

| Part | Technology |
|---|---|
| Frontend | React, Vite, TypeScript, MUI |
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Database | PostgreSQL |
| Realtime | WebSockets with PostgreSQL LISTEN and NOTIFY |
| Media | Any S3-compatible store. The compose files use Garage |
| Maps | Leaflet with OpenStreetMap data |
| Mail | SMTP, optional |
| Deployment | Docker Compose behind Caddy or another reverse proxy |

Developer documentation:

- [docs/development/](docs/development/): architecture, API, data layer, realtime, security, mail
- [docs/domain/](docs/domain/): domain model, roles and permissions, the run lifecycle
- [docs/design/](docs/design/): the design system

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. Report a security
problem as described in [SECURITY.md](SECURITY.md), never in a public issue. Everyone in this
project follows the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

Copyright 2025-2026 Aaron Kokal and the Clavis contributors. See [AUTHORS.md](AUTHORS.md).

Clavis is licensed under the European Union Public Licence 1.2 (EUPL-1.2). The full text is in
[LICENSE](LICENSE). All official language versions of the EUPL are equally valid. The EUPL-1.2
does not cover the logo, the fox mascot and the illustrations; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Third-party components keep their own licences.
Map data © OpenStreetMap contributors, available under the Open Database Licence.

## Funding

Clavis was developed on behalf of the City of Augsburg, Office for Social Integration, in the
DIWA 4.0 programme. The programme is co-funded by the European Union through the Asylum, Migration
and Integration Fund (AMIF).
