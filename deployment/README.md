# Deployment

This folder installs Clavis on a server. There are two layouts.

| Layout | Files | Use it when |
|---|---|---|
| Standalone server | `standalone/` | One server runs Clavis. The stack brings its own PostgreSQL, its own object store (Garage) and Caddy as the reverse proxy with automatic HTTPS. Start here |
| Shared services | `docker-compose.prod.yml`, `.env.example`, `scripts/` | A host already runs PostgreSQL, an S3-compatible store and a reverse proxy for several services |

To try Clavis on your own computer, run `./scripts/quickstart.sh` in the repository root instead.
The README in the repository root describes it.

The project publishes no container images. Both layouts build the images from the source of the
release you check out.

## Release

The commands of this guide install one release. Set it once in the shell you use for the install:

```sh
export CLAVIS_RELEASE=v0.9.3
```

Every command below that names the release reads this variable.

## Standalone server

### What you need

- A Linux server with at least 2 CPU cores, 4 GB of memory and 20 GB of disk, plus the space for
  photos and videos.
- Docker Engine 24 or later with the Compose plugin 2.20 or later, and git. The project tests with
  Docker Engine 27.5 with Compose 2.33, and Docker Engine 29.9 with Compose 5.7.
- A domain name, for example `rallye.example.org`. Its DNS record (A, and AAAA for IPv6) points at
  the server.
- Ports 80 and 443 open from the internet. Caddy needs both to get the certificate from Let's
  Encrypt.
- Optional, but needed to invite people: an SMTP account for the mail of the app.

### Install

```sh
git clone https://github.com/Clavis-Rallye/Clavis.git /opt/clavis
cd /opt/clavis
git checkout "$CLAVIS_RELEASE"
cd deployment/standalone
./init-env.sh rallye.example.org
docker compose up -d --build --wait
curl https://rallye.example.org/api/health
```

- `git checkout` selects the release of `CLAVIS_RELEASE`. The list of releases is on the GitHub page of the project.
- `init-env.sh` writes `.env` with your domain and six fresh secrets. Keep a copy of `.env` in a
  safe place apart from the server. A backup cannot be restored without these secrets.
- `docker compose up` builds the images and starts five containers: `db`, `garage`, `api`,
  `frontend` and `caddy`. The API migrates the database at every start.
- The health check answers `{"status":"ok","database":"connected","storage":"connected"}`.
- `garage` is the object store of the stack. At its first start it creates the bucket `clavis`
  (the stack fixes the name) and the default key from `S3_ACCESS_KEY` and `S3_SECRET_KEY` in `.env`. Before you delete the default key, recreate Garage
  without `--default-bucket`, or it will not start again.

Caddy gets the certificate at the first request to the domain and renews it on its own. If the
DNS record is not in place yet, the first requests fail with a TLS error, and Caddy tries again
later. `docker compose logs caddy` shows the progress.

The stack gives its proxy network two fixed subnets: `172.16.238.0/24` and the private IPv6 subnet
`fd00:172:16:238::/64`. If `docker compose up` stops with "invalid pool request: Pool overlaps with
other one on this address space", another network on this host holds one of them. Set another one
in `.env` with `CLAVIS_WEB_SUBNET` or `CLAVIS_WEB_SUBNET_V6`.

With Docker Engine 27 or later, an IPv6 visitor keeps its own address. On an older Docker, or with
ip6tables off, IPv6 visitors share one address for the limits per address. The stack starts in both
cases.

All commands below run in `deployment/standalone`. Settings live in `.env`. After a change to
`.env`, run `docker compose up -d` again. `standalone/.env.example` lists the common settings, and
`.env.example` in this folder lists every setting of the API.

### Behind a reverse proxy of your own

A server may already run a web server on ports 80 and 443, or a proxy in front of it may
terminate TLS. Then let Caddy serve plain HTTP on a local port. Add to `.env`:

```sh
CLAVIS_SITE=:80
CLAVIS_HTTP_PORT=127.0.0.1:8080
CLAVIS_HTTPS_PORT=127.0.0.1:8443
CLAVIS_TRUSTED_PROXIES=private_ranges
```

Point your proxy at `http://127.0.0.1:8080` for the whole domain, with WebSocket support. The
proxy must append the client's address to `X-Forwarded-For` or replace the header with it. It must
serve HTTPS, because the API sets its session cookies for HTTPS only. If your proxy runs on another
machine, add its address to `FORWARDED_ALLOW_IPS` in `.env`, after the default
`172.16.238.0/24,fd00:172:16:238::/64`.
Without it, every client counts as your proxy.

### First steps after the install

1. **Create the operator account.** The operator administers the installation above all
   organizations. Only this command creates one:

   ```sh
   docker compose exec -e INSTANCE_ADMIN_PASSWORD='Operator-Pass-2026!' api \
     python -m app.cli instance-admin create --email you@example.org
   ```

   The password needs at least 8 characters and three of these four: an upper-case letter, a
   lower-case letter, a digit, a special character. Without `-e INSTANCE_ADMIN_PASSWORD` the
   command asks for it.
2. **Sign in** at `https://<your domain>/instance/login`. Leave the TOTP code empty at the first
   sign-in. The console then shows a QR code: scan it with an authenticator app and confirm the
   code. From then on the sign-in needs the password and the code.
3. **Write the legal texts.** Operator console, page "Legal texts": the imprint, the privacy
   statement and the accessibility statement. They apply to every organization that has not
   written its own. Until then the legal pages show a placeholder that says the operator has not
   published the final text. Do not go live with the placeholder.
4. **Create the first organization.**
   - With mail set up: operator console, "Organizations", "New organization". Enter the name, the
     slug, the languages and the address of the first administrator. The console sends the
     invitation by mail.
   - Without mail: open `https://<your domain>/register/organization` and register the
     organization with its administrator. This page works only while no organization exists.

   The administrator of an organization sets up an authenticator app at the first sign-in.
5. **Load the demo content**, if you want a ready rallye to show:

   ```sh
   docker compose exec api python -m app.cli demo-content load --org-slug <slug> --enable-languages
   ```

   The demo is in German and English. The flag `--enable-languages` adds the languages of the
   demo that the organization does not have yet, so both show without a step in the settings.
   Leave the flag out to keep the languages of the organization as they are. Section
   [Demo organization](#demo-organization) describes the content.
6. **Import the place index** for the address search of the station form:

   ```sh
   docker compose exec api python -m app.cli geo import --bbox <south>,<west>,<north>,<east>
   ```

   `--bbox` is the region in degrees, at most one square degree, for example
   `48.22,10.72,48.48,11.03` for Augsburg and its neighbour towns. This standalone setup sets
   `GEOCODER_UPSTREAM=none`: no search text leaves the server, and the search answers from the
   index only. Section [Address search](#address-search) explains the other choices.
7. **Set up mail.** Fill in the mail block of `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS_MODE`,
   `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM_ADDRESS`, `MAIL_REPLY_TO`), then run
   `docker compose up -d`. Without `SMTP_HOST` the app writes each mail to its log instead of
   sending it, so invitations and password resets reach nobody.
8. **Set up the backup**, see the next section.

### Backup and restore

`./backup.sh` writes three files into `backups/`, all with the same time stamp:

| File | Content |
|---|---|
| `clavis-db-<stamp>.dump` | The database, taken with `pg_dump` while the app runs |
| `clavis-objects-<stamp>.tar.gz` | The object store: photos, videos, audio and images. Garage stops for the seconds of the copy |
| `clavis-logs-<stamp>.tar.gz` | The log files of the API, with the audit log |

Run it every night with cron, and copy the files to another machine. A backup on the same disk
does not survive the loss of that disk.

```sh
(crontab -l 2>/dev/null; echo "15 3 * * * /opt/clavis/deployment/standalone/backup.sh >> /var/log/clavis-backup.log 2>&1") | crontab -
```

The script deletes backups older than 30 days (`BACKUP_KEEP_DAYS`). Set `BACKUP_ROTATION_DAYS` in
`.env` to the same number: the privacy template states how long a backup keeps deleted data.

To restore, the release and the `.env` must be the ones of the backup. On a new server, clone the
repository, check out the same release and put the saved `.env` into `deployment/standalone`. Then,
with `<stamp>` from the file names:

```sh
docker compose up -d --wait db garage
docker compose stop caddy frontend api
docker compose rm -sf garage
docker volume rm clavis_garage_meta clavis_garage_data
docker compose create garage
docker run --rm --volumes-from "$(docker compose ps -aq garage)" -v "$PWD/backups:/backup:ro" \
  alpine:3 tar xzf /backup/clavis-objects-<stamp>.tar.gz -C /
docker compose exec -T db pg_restore -U clavis -d clavis --clean --if-exists --no-owner \
  < backups/clavis-db-<stamp>.dump
docker compose up -d --build --wait
```

The volume names start with the Compose project name, `clavis`. `docker volume ls` shows them.

### Update to a new release

On the standalone server, in `deployment/standalone`:

```sh
./backup.sh
git fetch --tags
git checkout <the new release tag>
docker compose build
docker compose down --remove-orphans
docker compose up -d --wait
```

The build runs while the old version still serves. `down` removes the containers and the networks,
so that `up` creates them as the new release describes them. The order with `down` is required on
every tested version of Compose, because `up` alone does not rejoin a changed network correctly.
Compose 2.33 stops with "network ... not found". Compose 5.7 exits without an error, but a
container that it only restarts loses its name on the new network, so Caddy answers 502.
`--remove-orphans` also removes the container of a service that the new release dropped. `down`
never carries `-v`, because `-v` deletes the volumes with the database, the stored media and the
logs. The site is away from `down` to the end of `up`: 54 seconds on a test machine, for a release
without a database migration. A migration adds its own time.

Read the release notes first. They are on the
[releases page](https://github.com/Clavis-Rallye/Clavis/releases) of this repository. They name
every step that a release needs beyond this. The API
migrates the database when it starts. If the migration fails, the API does not start, and
`docker compose logs api` shows why. To go back, check out the earlier release and restore the
backup of the same day. A checkout of the earlier release alone is not a way back across a
database migration: the older image answers "Can't locate revision" on the migrated database. The
way back is the earlier release plus the database dump taken before the update.

#### Optional: remove the metadata of images stored by an earlier release

Every image upload is stored without metadata. An image stored by a release before that keeps
what the camera wrote into it: the GPS position, the device, the time. An update never changes
stored content by itself, so this step is yours to take, after the update and after a backup:

```sh
docker compose exec api python -m app.cli image-metadata clean            # dry run, changes nothing
docker compose exec api python -m app.cli image-metadata clean --apply    # writes
```

Start with the dry run. It goes through the photo answers with their preview variants, the images
in texts and the station title images, lists each image that carries metadata and prints the
counts. With `--apply`, each listed image is stored again without its metadata, under the same key
and content type, so every text, station and answer keeps showing it; as for a new upload, a JPEG
is compressed again at quality 85 and an image above 4096 px on its long side is scaled down to
that. Nothing else in the object store changes, and the database is not touched.

An image the server cannot decode is named, counted apart and left as it is. HEIC photos are among
these. An image without metadata is not written again, so a second run lists nothing.

## Shared services (docker-compose.prod.yml)

This layout runs only the API, a one-shot migration and the frontend. The host provides the rest.
Use it only if your host already provides the following.

This stack file starts no image older than 0.9.0. With an older image the empty settings
`LK_A_DAYS`, `LK_B_DAYS` and `LK_C_DAYS` fail the settings parse, and the migration service exits
with code 1.

| The host provides | What the file expects |
|---|---|
| PostgreSQL | A database and a user. `DATABASE_URL` in `.env` |
| An S3-compatible store | A bucket and a key pair with read and write access. The API reaches the store at `S3_ENDPOINT`, by default `clavis-garage:3900` on `backend_network`. `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` and `S3_REGION` in `.env` |
| A reverse proxy that terminates TLS | It routes `/api/` with WebSocket upgrades to `clavis-api:8000` and everything else to `clavis-web:80`, and it replaces `X-Forwarded-For` with the address of the client it sees |
| Two Docker networks | `backend_network` for the database and the store. `proxy_network` for the proxy |

The file pins the two containers to the addresses `172.18.0.30` and `172.18.0.31` on
`proxy_network`. A proxy such as Nginx Proxy Manager caches the address of a container, and
the fixed address keeps it valid after a redeploy. Create the networks like this:

```sh
docker network create --subnet 172.18.0.0/16 proxy_network
docker network create backend_network
```

Create `proxy_network` first. A network created without a subnet takes the next free range of
Docker, and on a fresh host that is `172.18.0.0/16`.

If that subnet is taken on your host, pick a free one and change the two `ipv4_address` lines. A
proxy that resolves container names at each request, such as Caddy or Traefik, does not need the
fixed addresses: remove the two `ipv4_address` lines.

Build the images from the release you checked out, then start the stack:

```sh
git checkout "$CLAVIS_RELEASE"
docker build -t ghcr.io/clavis-rallye/clavis-api:"${CLAVIS_RELEASE#v}" src/backend
docker build -t ghcr.io/clavis-rallye/clavis-frontend:"${CLAVIS_RELEASE#v}" src/frontend
cd deployment
cp .env.example .env      # fill it in, with IMAGE_TAG=${CLAVIS_RELEASE#v}
docker compose -f docker-compose.prod.yml --env-file .env up -d --wait
```

`IMAGE_TAG` is the release without the `v`, the same tag the two `docker build` commands set.

The first steps are the same as for the standalone server. The commands take the prefix
`docker compose -f docker-compose.prod.yml --env-file .env`, run in `deployment/`.

Before an update, back up the database and the object store with the tools of your host. A way
back across a database migration is not a change of `IMAGE_TAG` or a checkout of the earlier
release alone: the older image answers "Can't locate revision" on the migrated database. The way
back is the earlier release with its own stack file plus the database dump taken before the update.

## Operator account commands

The whole account lifecycle of an operator runs on the server. No HTTP route creates, disables or
resets an operator. In the standalone layout:

```sh
docker compose exec api python -m app.cli instance-admin list                  # address, TOTP status, disabled, last sign-in
docker compose exec api python -m app.cli instance-admin disable --email <e>   # blocks the sign-in, ends an open session
docker compose exec api python -m app.cli instance-admin enable --email <e>    # reverses disable
docker compose exec api python -m app.cli instance-admin reset-totp --email <e>  # the next sign-in shows a new QR code; also ends the open sessions
docker compose exec api python -m app.cli instance-admin set-password --email <e>  # via INSTANCE_ADMIN_PASSWORD or a prompt
```

A disabled operator gets 403 `INSTANCE_ADMIN_DISABLED` at the sign-in. An operator changes the own
password in the console, with the current password. `set-password` is for a lost password and
needs no current one.

## Demo organization

A demo organization shows the product to other cities and institutions. It holds a ready rallye
with every challenge type, and a presenter starts it as a run in a few clicks.

1. Create the organization, see the first steps above.
2. Load the content. On the standalone server, in `deployment/standalone`, run
   `docker compose exec api python -m app.cli demo-content load --org-slug <slug> --enable-languages`.
   With shared services, run it in `deployment/` with the prefix
   `docker compose -f docker-compose.prod.yml --env-file .env`.
   With `--enable-languages` the command adds German and English to the organization, in the same
   transaction as the content, and the audit event names the added languages. Without the flag the
   languages stay as they are, and you enable them in the settings of the organization. The flag
   exists on the command line only: no HTTP route adds languages without a full session, and the
   administrator still sets up an authenticator app before the first full session. The command
   refuses with exit code 2, and the reason on stderr, when the slug does not exist, the
   organization is archived or the demo rallye is loaded already. On success it prints the
   counts and records the audit event `demo_content.loaded`.

The content is one rallye, *Clavis-Demo: Augsburger Altstadt* / *Clavis demo: Augsburg old town*.
It is a walk of about 45 minutes through the old town of Augsburg. It has three stations
(Rathausplatz, Perlachturm, Fuggerei) and seven challenges: two photo, one text, three quiz and
one QR challenge. The quiz and QR challenges score themselves. The photo and text challenges wait
for grading.

A second load without `--replace` changes nothing. With `--replace`, the earlier demo stations,
challenges and rallye are soft-deleted and the content is loaded fresh. Existing runs keep working
on their frozen snapshots.

Challenge 7, *Ziel erreicht* / *Finish reached*, is a QR scan that expects the text
`CLAVIS-DEMO-ZIEL`. Print a QR code with that text, from any QR generator, and show it at the
finish.

## Address search

The station form searches addresses through the API (`/api/geocode`), never
from the browser. Two parts answer, in this order.

1. **The regional place index.** It holds the named places, streets and house
   addresses of one region from OpenStreetMap, in the table `geo_places`. It
   finds the beginning of a word and tolerates typing errors, so it answers
   while the creator types. It is empty until the operator imports a region.
   On the standalone server, in `deployment/standalone`:

   ```sh
   docker compose exec api python -m app.cli geo import --bbox 48.22,10.72,48.48,11.03
   docker compose exec api python -m app.cli geo status
   ```

   With shared services, in `deployment/`, the two commands take the prefix of
   that section: `docker compose -f docker-compose.prod.yml --env-file .env exec api …`.

   `--bbox` is `south,west,north,east` in degrees, at most one square degree: a
   city and its surroundings. The command downloads the region once from the
   Overpass API (`overpass-api.de`, run by FOSSGIS e.V. in Germany) and replaces
   the whole index in one transaction. A failed import leaves the index as it
   was. The public instance is often busy. When it answers 429, 502, 503 or 504,
   or not in time, the command tries again after 20, 40 and 80 s, four times in
   all, and prints one line per attempt. `GEOCODER_OVERPASS_URL` points the
   import at another Overpass instance, for example your own. `--overpass-url`
   does the same for one run. Run it again a few times a year to take over map
   changes. For a server
   without access to the internet, save the Overpass answer elsewhere and pass
   it with `--file`. Augsburg with its neighbour towns is about 115,000 rows
   and 50 MB in the database, and the import needs about 350 MB of memory for
   70 to 80 seconds.

2. **The outside service**, set with `GEOCODER_UPSTREAM`. It is asked only for
   a search the creator sent with Enter, and only when the index leaves room.

   | Value | What happens | Who sees the search text |
   |---|---|---|
   | `none` (default) | No request leaves the server. The search needs an imported index | nobody |
   | `nominatim` | The public Nominatim instance, or `GEOCODER_UPSTREAM_URL`. Its policy forbids suggestions while typing, so it is never asked for them | OpenStreetMap Foundation, with the address of this server |
   | `photon` | A Photon instance: the public one of komoot, or a self-hosted one at `GEOCODER_UPSTREAM_URL`. It also answers while the creator types when no index is imported. The public instance is fair use only and blocks a client that sends many requests, so use a self-hosted one for typing | the operator of that instance, with the address of this server |

   The default is `none`, so a fresh install sends no search text to a third
   party. Import a region, or choose a service here.

   Set `GEOCODER_CONTACT` to a mail address or URL. The public services ask
   every application to name itself and a contact in the User-Agent.

Suggestions while typing are on when the index holds rows, or when the outside
service is `photon`. With `nominatim` and no index, the field searches on Enter.
With `none` and no index, the search finds nothing, and the creator sets the
pin on the map.

The map of the station form starts at `GEOCODER_MAP_CENTRE` (`lat,lng`, for
example `48.3705,10.8978`) until the creator sets a pin. Without the setting it
starts at the middle of the imported index, and without an index at Augsburg.
A value the map cannot show stops the API at start. Until a pin exists, the
same point steers the search: hits near it rank first, and the outside service
is asked within a box around it before anywhere else.

The map itself loads its tiles in the browser from `tile.openstreetmap.de`, the FOSSGIS
server in Germany. That is the default in `src/backend/app/config.py`. `MAP_TILE_URL` names another
tile server. The privacy text of the installation must name the tile server in use.

## Configuration

`BRAND_NAME` (default `Clavis`) is the product name of the whole installation. The frontend reads
it once at start from `GET /api/public/config`: the app shell, the page title and the headlines
show it, and the mail signature uses it. There is one value per installation, never one per
organization.

`BRAND_NAME` also names the installed app. The API builds the web manifest from it and serves it at
`GET /api/public/manifest.webmanifest`. A phone that has the app installed takes a new name when
its browser next checks the manifest. The browser decides when that is.

The legal texts of the installation (imprint, privacy statement, accessibility statement) are not
settings. The operator writes them in the operator console or with `PUT /api/instance/legal`, and
the database stores them. They show wherever no organization context exists. Inside an
organization, the organization's own text wins. See
[docs/development/api-structure.md](../docs/development/api-structure.md) and
[docs/development/security-and-compliance.md](../docs/development/security-and-compliance.md).

`FORWARDED_ALLOW_IPS` names the addresses of the reverse proxy that talks to the API. The API takes
the client address from `X-Forwarded-For` only for a request from one of them. It walks the header
from the right and takes the first address that is not a trusted proxy. The per-address limits key
on that address, for example the sign-in with 10 attempts per minute. Each stack file sets the
network of its own proxy as the default. Never set `*`. The API would then take the first entry of
the header, which the client writes itself, and every client would pass every limit per address.

The setting per layout:

| Layout | Default of `FORWARDED_ALLOW_IPS` | What the API takes as the client | What the operator sets |
|---|---|---|---|
| Standalone server, Caddy with its own TLS | `172.16.238.0/24,fd00:172:16:238::/64`, the `web` network | The address Caddy sees. Caddy replaces the header | Nothing |
| Standalone server behind a proxy of your own on the same host | The same | The address that your proxy appended. A forged entry to the left of it is ignored | Nothing. Your proxy must append or replace, never pass the client's header through alone |
| Standalone server behind a proxy on another machine | The same | Your proxy's own address, so every client counts as the proxy | `FORWARDED_ALLOW_IPS=172.16.238.0/24,fd00:172:16:238::/64,<the address of your proxy>` |
| Shared services | `172.18.0.0/16`, the `proxy_network` of this README | The address your proxy sets. Your proxy must replace the header, in nginx with `proxy_set_header X-Forwarded-For $remote_addr;` | The address of your proxy, if it is not in that subnet |
| Quickstart on your own computer | `172.16.239.0/24,fd00:172:16:239::/64`, the `frontend` network | The address Caddy sees | Nothing |

### A notice of the operator

The operator can show a short notice with logos, a text and links. A funding programme, for
example, may ask for its logo and a sentence on every page. The notice belongs to the
installation, never to one organization. Without it, nothing shows.

The notice is a folder on the host. The frontend container serves it at `/operator/`, from
`/usr/share/nginx/html/operator`. Both stack files mount the folder that `OPERATOR_NOTICE_DIR`
names, read-only:

| Layout | Where `OPERATOR_NOTICE_DIR` goes | Unset |
|---|---|---|
| Standalone server | `deployment/standalone/.env` | The empty volume `clavis_operator_notice` |
| Shared services | `deployment/.env` | The empty volume `clavis-operator-notice` |

Use an absolute path, for example `OPERATOR_NOTICE_DIR=/srv/clavis-operator`. Then run
`docker compose up -d` (shared services: with the prefix of that section) once, so the frontend
container mounts the folder. Without the variable the stack starts as before.

The file `notice.json` in the folder describes the notice. Version 1 of its contract:

| Field | Rule |
|---|---|
| `version` | The number 1. Any other value means no notice |
| `logos` | Required list, 0 to 8 entries. The order of the file is the order on the page |
| `logos[].src` | Required. A file name inside the folder: it matches `^[A-Za-z0-9][A-Za-z0-9._-]*\.(svg\|png\|jpg\|jpeg\|webp)$`, without regard to the case of the ending. No slash, no scheme, no `..` |
| `logos[].src_dark` | Optional. The file for the dark theme. The same rule as `src` |
| `logos[].alt` | Required object, language code to text. At least one text is not empty |
| `logos[].height` | Required integer from 16 to 200, in CSS pixels |
| `text` | Required object, language code to plain text. At least one text is not empty. Each text has at most 2000 characters |
| `text_lang` | Optional language code. When set, `text` must hold that language |
| `links` | Optional list, 0 to 8 entries |
| `links[].label` | Required, 1 to 200 characters |
| `links[].url` | Required. An absolute URL with the scheme `https` or `http` |

A language code matches `^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`, for example `de` or `en`. An unknown
field is ignored. One broken rule anywhere makes the whole file invalid, and an invalid file shows
no notice.

Which text shows:

- With `text_lang`, the text of that language shows in every language of the interface. Use it for
  a statement that must stay in one language.
- Without `text_lang`, the text of the interface language shows, else `en`, else `de`, else the
  first entry. An `alt` text follows the same rule.

An example with two files, `programme.svg` and `programme-negative.svg`, in the folder:

```json
{
  "version": 1,
  "logos": [
    {"src": "programme.svg", "src_dark": "programme-negative.svg",
     "alt": {"de": "Logo des Programms", "en": "Logo of the programme"}, "height": 48}
  ],
  "text": {"de": "Dieses Angebot wird gefoerdert.", "en": "This service is funded."},
  "text_lang": "de",
  "links": [{"label": "example.org/programme", "url": "https://example.org/programme"}]
}
```

The notice shows on the entry pages before a rally, on the legal pages, on the public results, in
the operator console and at the bottom of the app shell. In a running rally it is one entry in the
menu, so it takes no room on the screen of a team.

A change of a file shows after one reload of the page. No restart is needed: the container sends
the files with `Cache-Control: no-cache`, and the app asks the network first. A phone without a
network shows the last notice it loaded.

When the notice does not show, check the file the browser gets:

```sh
curl -s https://<your domain>/operator/notice.json
```

A 404 means the folder is not mounted or the file is missing. A file that the command shows but the
app does not is invalid: check it against the table above.

Every file in the folder is public. Put nothing there that is not meant for every visitor. The app
shows the logos in the order of the file and at the heights the file names. The operator answers
for that order, for the sizes and for the wording, for example toward the funding programme and
its rules on logos.

## Secrets

`.env` holds every secret of the installation: the database password, the two keys of the API,
the keys of the object store and the SMTP password. Never commit it. Keep a copy apart from the
server, because a restore needs the same secrets. Generate a new secret with
`openssl rand -hex 32`.

## Log retention

Logs live in `/var/log/clavis/` inside the API container. The Docker volume is `clavis_api_logs`
on the standalone server and `clavis-logs` with shared services.

| Stream | File | Rotation | Retention variable | Default | Gzip after |
|--------|------|----------|--------------------|---------|------------|
| Audit | `audit.log` | Daily, midnight UTC | `AUDIT_LOG_RETENTION_DAYS` | 365 days | 7 days |
| Application | `application.log` | Daily, midnight UTC | `APP_LOG_RETENTION_DAYS` | 90 days | 3 days |
| Error | `error.log` | Daily, midnight UTC | `ERROR_LOG_RETENTION_DAYS` | 90 days | 3 days |
| Access | `access.log` | Daily, midnight UTC | `ACCESS_LOG_RETENTION_DAYS` | 90 days | 3 days |

Set a retention variable in `.env`. Two places read it:

- The API rotates in-process (Python `TimedRotatingFileHandler`). It keeps as
  many daily files per stream as the variable says and deletes the oldest
  plain file beyond that. A day without an entry leaves no file, so a quiet
  stream keeps its files longer than that number of days.
- `scripts/log-cleanup.sh` compresses rotated files and deletes every file,
  plain or compressed, that is older than the variable in days. It reads
  the variable from its environment, then from the file `ENV_FILE`, then takes
  the default. `ENV_FILE` is `deployment/.env` unless it is set.
  `AUDIT_LOG_COMPRESS_AFTER_DAYS` (7) and `LOG_COMPRESS_AFTER_DAYS` (3) set
  the compression delays.

The log views of the App read compressed files too.

Install the script once as a daily cron on the server. It runs on the host,
so `LOG_DIR` must name the volume's directory on the host. On the standalone
server, `ENV_FILE` names its `.env`:

```sh
(crontab -l 2>/dev/null; echo '0 4 * * * LOG_DIR="$(docker volume inspect clavis_api_logs --format "{{ .Mountpoint }}")" ENV_FILE=/opt/clavis/deployment/standalone/.env /opt/clavis/deployment/scripts/log-cleanup.sh >> /var/log/clavis-log-cleanup.log 2>&1') | crontab -
```

With shared services:

```sh
(crontab -l 2>/dev/null; echo '0 4 * * * LOG_DIR="$(docker volume inspect clavis-logs --format "{{ .Mountpoint }}")" /opt/clavis/deployment/scripts/log-cleanup.sh >> /var/log/clavis-log-cleanup.log 2>&1') | crontab -
```
