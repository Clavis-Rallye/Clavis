# Transactional Email

How Clavis sends mail, what it sends, and what to check when it stops working.

## The one rule

**A mail failure never breaks the operation that triggered it.** Invitations are
stored before anything is sent, sends are queued *after* the response is
returned, and transport errors are logged rather than raised. An invitation
whose mail bounces still exists and can be resent; a password reset whose
confirmation fails still reset the password.

The single exception is the operator's test probe, where the transport error is
the entire point and is returned to the caller.

## Configuration

All of it is environment variables (`app/config.py`, `MailSettings`). Nothing is
in the repository.

| Variable | Default | Notes |
|---|---|---|
| `SMTP_HOST` | *(empty)* | **Empty disables sending.** The app still starts and still creates invitations; each message is logged instead of delivered. |
| `SMTP_PORT` | `465` | |
| `SMTP_TLS_MODE` | `ssl` | `ssl` (implicit TLS, 465), `starttls` (587), or `none` (local catch-all only) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | Never logged, never returned by the API |
| `SMTP_TIMEOUT_SECONDS` | `20` | Per send attempt |
| `MAIL_FROM_ADDRESS` | *(empty)* | The sender address the mail server accepts. **Empty disables sending too**, and the start log says so. No default, because only the operator knows the address |
| `MAIL_FROM_NAME` | `Clavis` | |
| `MAIL_REPLY_TO` | *(empty)* | Set it to a mailbox a human reads, deliberately not a black hole. Empty: no `Reply-To` header, and the start log says so |
| `PUBLIC_BASE_URL` | *(empty)* | Absolute origin for links in mail and for the result link. **Never** derived from a request header: Host-header poisoning would rewrite reset links. Empty: `WEBAUTHN_ORIGIN`, the one public origin of a single-origin installation |
| `INVITATION_EXPIRY_DAYS` | `7` | |

An empty `SMTP_HOST` is the default in local development and in CI, which is
what stops a test run from ever reaching a real mailbox.

The app sends only when `SMTP_HOST` and `MAIL_FROM_ADDRESS` are both set
(`MailSettings.enabled`). It never refuses to start over mail. At start it
prints what an operator most likely forgot (`config.startup_warnings`), also as
a `config_incomplete` line in the JSON log.

### Example values

```
SMTP_HOST=mail.example.org
SMTP_PORT=465
SMTP_TLS_MODE=ssl
SMTP_USERNAME=noreply@rallye.example.org
SMTP_PASSWORD=<the password of that mailbox>
MAIL_FROM_ADDRESS=noreply@rallye.example.org
MAIL_REPLY_TO=team@rallye.example.org
PUBLIC_BASE_URL=https://rallye.example.org
```

## Transports

`app/services/mail/transport.py` has two, chosen by whether `SMTP_HOST` is set:

- **`SMTPTransport`** — a real submission via `aiosmtplib`. One connection per
  message; the volume here is a handful of mails per event, and pooling would
  add reconnect and staleness handling for no measurable gain.
- **`LoggingTransport`** — records messages in memory and logs them. Tests
  install it via `mail.set_transport(...)` and assert on `transport.sent`.

## Messages

`app/services/mail/templates.py`. Every message exists in German and English and
in both plain text and HTML, with the same information in each — a mail client
that refuses HTML must not receive a message whose only action is missing.

| Kind | Trigger |
|---|---|
| `invitation` | An org_admin invites an address + role |
| `password_reset` | `POST /auth/password-forgot`, or an admin using "send password link" |
| `password_changed` | Any successful password change or reset (security notice) |
| `role_changed` | An admin changes a user's role |
| `test` | The operator probe in organisation settings |

The HTML is deliberately plain — tables and inline styles, no external
stylesheet, no image, no web font, no tracking pixel. The recipients are city
staff on Outlook and participants on whatever their phone ships with.

### Language

Picked per message, in this order: the invitation's stored language → the
requesting client's `X-Clavis-Language` header (the frontend sends its i18n
state) → `Accept-Language` → the organisation's `default_language` →
German. A user's language preference is not stored on the account, so for a
password reset the request is the only signal available.

## Invitations

An invitation is an **offer** of an account, not an account. The user row is
created only on acceptance, which keeps password-less rows out of the users
table, out of the login path and out of the GDPR retention sweep.

Only the SHA-256 hash of the token is stored (`user_invitations.token_hash`).
The raw token exists in exactly one place: the mail in the recipient's inbox.

A partial unique index (`WHERE accepted_at IS NULL AND revoked_at IS NULL`)
allows one live invitation per address. Inviting the same address again — or
pressing "resend" — rotates the token and restarts the clock, so a leaked older
link dies immediately. That predicate ignores expiry on purpose, so
`InvitationRepository.get_open_for_email` must ignore it too: an expired
invitation still occupies the slot, and a re-invite has to find and rotate it
rather than insert a duplicate and hit the index.

`org_admin` is invitable. Bootstrap registration closes after the first
organisation, so without this there is no path to a second administrator
at all.

## Retention

A pending invitation is an email address the organisation holds about somebody
who never became a user. Once the link is dead — expired, withdrawn, or redeemed
— there is no purpose left to keep it for, so `sweep_dead_invitations` (LK_D, in
the daily retention sweep) deletes it after a 30-day grace window. The window
exists so an admin can still see "yes, that was sent and it lapsed" in the days
around an event.

Accepted invitations go too: the account they produced is the record that
matters, and the audit log keeps the `user_invitation_accepted` event.

## Endpoints

| Method | Path | Auth |
|---|---|---|
| `POST` | `/api/orgs/{slug}/invitations/` | org_admin |
| `GET` | `/api/orgs/{slug}/invitations/` | org_admin |
| `POST` | `/api/orgs/{slug}/invitations/{id}/resend` | org_admin |
| `DELETE` | `/api/orgs/{slug}/invitations/{id}` | org_admin |
| `GET` | `/api/invitations/{token}` | none — the invitee has no account yet |
| `POST` | `/api/invitations/{token}/accept` | none |
| `POST` | `/api/orgs/{slug}/users/{id}/send-password-reset` | org_admin |
| `GET` | `/api/orgs/{slug}/admin/mail/status` | org_admin |
| `POST` | `/api/orgs/{slug}/admin/mail/test` | org_admin |

The public endpoints are rate-limited. The preview reveals only what the mail
already said: organisation, address, role, expiry.

A dead link is rejected with a *reason* — `invitation_not_found` (404),
`invitation_expired` / `invitation_revoked` / `invitation_already_accepted`
(410). The frontend turns each into a different sentence, because "this link no
longer works" without a reason is the kind of dead end that generates a support
call.

## Operating it

**Before an event**, use *Organisationseinstellungen → E-Mail → Testnachricht
senden*. Mail fails asymmetrically: a wrong password and "nobody has been
invited yet" look identical from the outside. The probe returns the actual SMTP
error (never the password).

### Where undeliverable mail goes

Use a real mailbox as sender (here `noreply@example.org`) and forward everything it receives to a
mailbox a human reads (here `team@example.org`), for example with a Sieve rule that redirects with
`:copy`. Then bounces and stray replies do not accumulate unseen.

A mistyped address fails in one of two ways:

| Address | What happens |
|---|---|
| On a local domain (`…@example.org`) | The mail server refuses at RCPT, `aiosmtplib` raises, the app logs `mail_send_failed`. **No bounce is generated** — the message was never accepted. |
| On a foreign domain (`…@city.example`) | The remote MX usually accepts, then bounces asynchronously. The DSN goes to `noreply@` and is forwarded to `team@`. |

In **both** cases the UI still shows the invitation as delivered.
`mail_delivered` reports whether this deployment can send at all, not the
outcome of one send — that happens after the response has been returned.
To find out whether a specific invitation arrived, check the `team@` mailbox or
the API log.

**When mail is not arriving:**

1. Send the test mail. A transport error appears in the UI.
2. `docker logs clavis-api | grep mail_` — `mail_sent`, `mail_send_failed`
   (with the transport error) and `mail_suppressed` (no `SMTP_HOST`) are the
   three outcomes.
3. Check the port from *inside the container*, not from the host:
   ```sh
   docker exec clavis-api python -c "import socket; socket.create_connection(('mail.example.org', 465), 8)"
   ```
   Some hosts block outbound ports such as 587 and 2525, so a time-out there points at the host, not at the app.
4. Check the recipient's spam folder before assuming non-delivery.

**A pending invitation whose mail failed** is shown in user management with a
"Nicht zugestellt" marker. Fix the cause, then press *Erneut senden*.

### Rolling back

A way back follows [deployment/README.md](../../deployment/README.md#update-to-a-new-release), section "Update to a new release".
