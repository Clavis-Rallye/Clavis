# Walkthrough harness
Bring the stack up from the repo root; run every command below from `src/frontend`. The repo root needs a `.env` with `JWT_SECRET_KEY` and `HMAC_AUDIT_KEY`, for example from `./scripts/quickstart.sh`.
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build --wait`
`--build` builds the development images, so an image that `quickstart.sh` built for production is not reused.
On a host where another Clavis stack runs, see [Try it on your computer](../../../../README.md#try-it-on-your-computer) in the main `README.md` to keep the two stacks apart.
Run `npm ci` in `src/frontend` once before the first walk.
Walk the staff screens in German on the desktop viewport.
`E2E_BASE_URL=http://localhost WALK_LANG=de npm run walkthrough -- --project=desktop --grep @staff`
Walk them again in English, reusing the organisation the first run seeded.
`WALK_LANG=en WALK_SEED=tests/walkthrough/out/seed.json npm run walkthrough -- --project=desktop --grep @staff`
Build the review page, then open `tests/walkthrough/out/index.html`.
`npm run walkthrough:index`
Evidence lands in `out/<project>/<lang>/<persona>/<NN>-<step>/`; `out/` is git-ignored.
