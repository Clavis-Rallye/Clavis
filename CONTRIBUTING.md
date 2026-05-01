# Contributing to Clavis

Thank you for your interest in Clavis. This file explains how to propose a change. Questions about
a bug or a feature go into an issue. Security problems go through [SECURITY.md](SECURITY.md).

## Set up

Follow [docs/development/getting-started.md](docs/development/getting-started.md). You need
Docker with Compose, Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and Node.js 24.15 or
later in the 24 line, 22.22.2 or later in the 22 line, or 26 and later. CI and the container
images use Node 24.

```sh
uv sync --directory src/backend --extra dev
npm ci --prefix src/frontend
./scripts/preflight.sh
```

## Make a change

1. Open an issue first for anything larger than a small fix. Say what you want to change and why.
2. Create a branch from `main`. Name it `<type>/<topic>`, for example `fix/login-rate-limit`.
   The types are `feat`, `fix`, `chore` and `docs`.
3. Keep one topic per branch. If the scope grows, split it.
4. Run `./scripts/preflight.sh` before every push. It runs the same gates as CI: ruff, mypy,
   eslint, the frontend build and the frontend unit tests.
5. For backend changes, also run the affected test files, for example
   `cd src/backend && uv run pytest tests/test_auth.py`. The full suites run with
   `make test-backend` and `make test-frontend`. They take a long time.
6. For a visible change, start the local stack and check the change in the browser.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) with the types above:

```
fix(auth): limit login attempts per address

Explain why, if the summary does not say it.

Signed-off-by: Your Name <you@example.org>
```

## Pull requests

- Target `main`. The repo merges by squash.
- The title is the summary of the change, in the commit style, under 70 characters.
- The body says what changed, why, and how you tested it. Link the issue with `Closes #N`.
- CI must pass. A maintainer reviews every pull request from outside the core team.
- Changes to authentication, sessions, security headers and workflows need the code owner's
  review. See `.github/CODEOWNERS`.

## Licence of your contribution

Clavis is licensed under the [EUPL-1.2](LICENSE). By opening a pull request you agree that your
contribution is licensed under the EUPL-1.2 as well.

Every commit of a pull request ends with one line:

```
Signed-off-by: Your Name <you@example.org>
```

Use the name and email of the commit's author (`git config user.name` and `user.email`). With that
line you certify the [Developer Certificate of Origin](https://developercertificate.org/), version
1.1: you wrote the change, or you have the right to submit it under the EUPL-1.2. `git commit -s`
adds the line.

A check on every pull request fails when a commit lacks the line or when the line names another
person. Merge commits are not checked. A co-author needs no line of their own. The commits that
Dependabot makes in its own pull requests are not checked, because GitHub signs them. The check
reads at most 250 commits of a pull request. A longer pull request fails and must be split.

To fix a pull request, add the line to the last commit with `git commit --amend --no-edit -s`. Add
it to every commit of the branch with `git rebase --signoff origin/main`, then
`git push --force-with-lease`.

You keep the copyright to your contribution. Add your name to [AUTHORS.md](AUTHORS.md) in your
first pull request if you want to be listed.

Do not add a dependency whose licence is not compatible with the EUPL-1.2. Permissive licences
(MIT, BSD, ISC, Apache-2.0) are fine. Ask in the issue before you add anything else.

## Issues

Use one of the issue forms: bug, enhancement, feature, documentation or tech debt. Each form sets
the label `unconfirmed`. A maintainer removes it when the issue is triaged. Blank issues are off.

Labels are few on purpose:

| Label | Meaning |
|---|---|
| `unconfirmed` | Not triaged yet |
| `small`, `medium`, `large` | Effort estimate, at most one |
| `documentation` | Docs work |
| `dependencies` | Set by Dependabot |
| `good first issue`, `help wanted` | A good place to start |

Type, priority, duplicates and blocked work use the GitHub features for these, not labels.

## Language

Code, comments, commit messages and developer docs are in English. User-facing text lives in the
locale files under `src/frontend/src/i18n/locales/`, in German and English. A change to a string
changes both languages.

## Conduct

Everyone in this project follows the [code of conduct](CODE_OF_CONDUCT.md).
