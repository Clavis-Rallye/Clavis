# Security policy

## Report a vulnerability

Do not open a public issue for a security problem. Report it privately through GitHub:
[Report a vulnerability](https://github.com/Clavis-Rallye/Clavis/security/advisories/new).
If you cannot use GitHub, write to contact@clavis-rallye.de.

Please include:

- The affected version or commit.
- What an attacker can do, and what they need for it.
- The steps to reproduce, or a proof of concept.
- Your name for the credit, if you want one.

The maintainers confirm the report, assess it and tell you what happens next. A fix goes into a
release first. The advisory is published after the release. Reporters are credited unless they
ask otherwise.

## Supported versions

| Version | Security fixes |
|---|---|
| The latest release | yes |
| Older releases | no. Update to the latest release |

## Scope

In scope: the code in this repository, its container build files and its deployment examples.

Not in scope:

- A specific installation of Clavis. Each operator runs its own installation. Report a problem of
  an installation to its operator, named in its imprint.
- Third-party services such as map tiles or geocoding.
- Findings that need a compromised server, a compromised operator account or physical access to a
  participant's phone.

## For operators

The security design is described in
[docs/development/security-and-compliance.md](docs/development/security-and-compliance.md) and
[docs/development/authentication.md](docs/development/authentication.md). Keep your installation
on the latest release. Watch the releases of this repository to learn about security fixes.
