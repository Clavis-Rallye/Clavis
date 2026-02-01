# Domain Model — App Implementation

> **Scope.** The conceptual model is the one the code implements. It has the two-layer architecture of stable content and run-time data, copy-on-run snapshots, the role model, the run lifecycle and multilingual handling.

This file documents the **App-specific implementation detail**: the terms of the interface and the code, and the media pipeline.

## Terms

The table maps each model of `src/backend/app/models/` to the words of the interface. The German and English words are those of the locale files in `src/frontend/src/i18n/locales/`. Where the interface uses a second word for the same concept, the row names it with its count in the English interface.

| Code name (class, table) | German | English | What it is |
| --- | --- | --- | --- |
| `Organization`, `organizations` | Organisation | organization | A tenant. It owns its users, rallyes, runs and content. |
| `User`, `users` | Person | user (also: person) | An account or a guest of one organization, with one role. |
| `InstanceAdmin`, `instance_admins` | Betreiber | operator | An account for the whole installation, above all organizations. The command line names it `instance-admin`. |
| `UserInvitation`, `user_invitations` | Einladung | invitation | A pending invitation to set up an account. |
| `Rallye`, `rallyes` | Rallye | rallye (also: rally, 27 times in the interface) | The stable template of a game. It lists stations in a fixed order. |
| `Station`, `stations` | Station | station | A place in the library of the organization. Many rallyes can use it. |
| `RallyeStation`, `rallye_stations` | none | none | The link of a library station into a rallye. It holds the order. |
| `Challenge`, `challenges` | Aufgabe | challenge (also: task, 42 times in the interface) | A task in the library, of one type: text, quiz, photo, video, audio or QR code. The code keeps the retired type `creative`. |
| `RallyeStationChallenge`, `rallye_station_challenges` | none | none | The link of a challenge to a station inside one rallye. It holds the order. |
| `Run`, `runs` | Lauf | run | One play of a rallye, with the states configured, scheduled, active, paused and ended. |
| `RunStation`, `run_stations` | Station | station | The frozen copy of a station, made when the run starts. |
| `RunChallenge`, `run_challenges` | Aufgabe | challenge | The frozen copy of a challenge, made when the run starts. |
| `Team`, `teams` | Team | team | A group that plays one run. |
| `TeamMember`, `team_members` | Mitglied | member | A person in a team, registered or a guest. |
| `RunDeparture`, `run_departures` | none | none | The record that a guest left a team. It blocks a return to the same run. |
| `Submission`, `submissions` | Antwort (also: Einreichung) | answer (also: submission, 21 times in the interface) | What a team sends for one challenge. There is one live answer per team and challenge. |
| `Score`, `scores` | Punkte | points | The points a coordinator gives to an answer, with an optional comment. |
| `ChatMessage`, `chat_messages` | Nachricht im Teamchat | message in the team chat | A message of a team during a run. |
| `FeedEvent`, `feed_events` | Mitteilung | notice (also: announcement, 3 times, and feed, once) | An entry in the notices of a run, from the system or from the coordination. |
| `Feedback`, `feedback` | Feedback | feedback | Anonymous feedback after a run. It has no user. |
| `RunResultLink`, `run_result_links` | Öffentliches Ergebnis | public result | The public link to the final standing of a run. A run has at most one live link. |

The role `coordinator` of `User` shows as Spielleitung in German. In English the interface says coordination 18 times and coordinator 15 times. The documents say coordinator, as the code and the API do.

Four tables are technical and have no interface word: `password_reset_tokens`, `instance_settings`, `geo_places` and `qa_notes` with `qa_state`. They hold password reset tokens, the legal texts of the installation, the place index of the address search and the notes of the test feedback module.

## Exports

The code has two exports. The personal data export of a registered participant is the route `/export` in `src/backend/app/routers/me.py`. The feedback notes of a test session have their own export. The App has no export or import of a rallye.

## Media Processing Pipeline

Uploaded files go through server-side processing. It lives in `src/backend/app/services/image_variants.py` and `src/backend/app/services/media_metadata.py`.

### Limits

The size limit of an upload comes from the config. `MAX_FILE_SIZE_MB` (default 10) applies to every file except video. `MAX_VIDEO_FILE_SIZE_MB` (default 100) applies to files of type `video/*`.

### Images

An image upload is stored in this form:

- **Original**: re-encoded in its own format, without any metadata. The orientation is applied to the pixels and the GPS data goes. JPEG is written at quality 85. The long side is scaled down to at most 4096 px. A smaller image is never scaled up.
- **Thumb**: a WebP image with a long side of at most 400 px.
- **Display**: a WebP image with a long side of at most 1,600 px.

The variants are stored beside the original as `<key>.thumb.webp` and `<key>.display.webp`. Garage is never exposed to the browser. The API streams the files from `GET /api/files/{key}`. The query `?variant=thumb` or `?variant=display` serves a variant. The original is served when the variant does not exist.

Two cases differ. A HEIC or HEIF photo sent as an answer cannot be decoded, so it is stored as uploaded and has no variants. Any other image that cannot be decoded is refused. An image above 50 million pixels is refused as too large.

### Video and Audio

Video and audio are not transcoded and have no variants. The upload route rewrites the container and removes its metadata, such as the place, the device, the software and the time. Picture and sound stay as they are. A file whose container cannot be read is refused.
