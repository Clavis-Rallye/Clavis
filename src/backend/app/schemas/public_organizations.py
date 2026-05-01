"""Public, unauthenticated schema for the org list endpoint.

Used by the OrgPicker fallback in the ProfileSetupStep when a user reaches
`/register` directly (without a Welcome run-code lookup) and we therefore
have no organization_slug in the wizard state.

Surface is intentionally minimal — `slug` and `name` only — to avoid leaking
any operational data about an organisation beyond what's already public via
existing org-scoped URLs.
"""

from pydantic import BaseModel


class PublicOrganizationListItem(BaseModel):
    slug: str
    name: str


class PublicOrganizationListResponse(BaseModel):
    organizations: list[PublicOrganizationListItem]
