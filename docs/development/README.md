# Development Documentation

Technical architecture documentation for the Clavis platform. These docs describe the system as built. Use them to understand how things work before making changes.

## Contents

| Document | Scope |
|----------|-------|
| [Getting Started](getting-started.md) | Prerequisites, setup, commands, environment variables, testing, troubleshooting |

| Document | Scope |
|----------|-------|
| [Architecture Overview](architecture-overview.md) | System topology, Docker services, request flow, network layout |
| [Data Layer](data-layer.md) | SQLAlchemy models, repository pattern, multi-tenancy, migrations |
| [Authentication & Authorization](authentication.md) | JWT tokens, 2FA with an authenticator app, RBAC, session management |
| [Real-Time Communication](realtime.md) | WebSockets, Postgres LISTEN/NOTIFY, ConnectionManager |
| [Security & Compliance](security-and-compliance.md) | Audit logging with HMAC chain, structured logging, GDPR, security headers |
| [API Structure](api-structure.md) | URL patterns, router organization, request lifecycle, Pydantic schemas |
| [Transactional Email](transactional-email.md) | SMTP transport, invitation + password-reset mail, configuration, operating and debugging delivery |
| [Feedback Module](qa-feedback.md) | The optional feedback bar for test sessions: switch, token, API, retention, hiding |

## How These Docs Relate to Other Documentation

- **[Domain Model](../domain/domain-model.md)**: Entity definitions, stable/dynamic separation, content lifecycle. The "what".
- **These docs**: How the code implements the domain model. The "how".
- **[Getting Started](getting-started.md)**: Setup, commands, environment variables, troubleshooting. The "getting started".
