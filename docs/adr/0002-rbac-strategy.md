# ADR-0002: RBAC Strategy

**Status:** Accepted
**Date:** 2026-01-15
**Author:** Roberto Andrade

---

## Context

The IT Governance Dashboard exposes sensitive vendor and contract data. Access must be controlled based on the user's organizational role. The authentication layer is Microsoft Entra ID (Azure AD) via OAuth2, which provides group membership and directory roles through MS Graph.

Several access-control models were considered:

- **ACL (Access Control Lists)** — per-resource per-user grants. Too granular for an internal dashboard; high administrative overhead.
- **ABAC (Attribute-Based Access Control)** — policy decisions based on attributes of user, resource, and environment. Very flexible but complex to implement and audit.
- **RBAC (Role-Based Access Control)** — users are assigned to roles; roles are granted permissions to resources. Well-understood, easy to audit, maps naturally to organizational hierarchy.

The team operates with well-defined organizational boundaries (IT, Finance, Procurement, Executive) that map directly to dashboard modules. RBAC is the natural fit.

---

## Decision

Adopt **Role-Based Access Control (RBAC)** with roles sourced from Microsoft Entra ID group membership, synchronized at login time via MS Graph API.

### Roles defined

| Role | Description | Modules accessible |
|---|---|---|
| `viewer` | Read-only access to allowed modules | Fornecedores (read), Contratos (read) |
| `operator` | Can create and edit, cannot delete | All modules — create/edit |
| `admin` | Full access including soft-delete | All modules |
| `executive` | Aggregated KPIs only, no PII | Dashboard home, Score cards |

### Implementation rules

1. Roles are mapped from Entra ID group names via a configurable `ROLE_GROUP_MAP` env var.
2. Role is stored in the JWT session token after login; no round-trip to MS Graph on each request.
3. Permission checks are enforced at the service layer, not just the route layer.
4. All permission denials are logged via `structlog` with the user ID, role, resource, and action.
5. Admin role requires explicit assignment — it is never inferred from group membership alone.

---

## Consequences

### Positive
- Simple mental model: role → permissions, easy to audit and explain to stakeholders.
- Roles synced from Entra ID — no separate user directory to maintain.
- JWT caching avoids MS Graph calls on every request (performance).
- Aligns with existing organizational boundaries.

### Negative
- Role granularity is coarse — exceptions (e.g., "this user can view contracts but not vendors") require a new role or a future ABAC extension.
- JWT role claims can become stale if a user's group membership changes between login and token expiry. Mitigated by short token TTL (4 hours).
- Requires a reliable MS Graph connection at login time.

---

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| ACL per resource | Too granular; high admin overhead for internal tool |
| ABAC | Correct long-term direction but adds implementation complexity not justified for current scope |
| Database-managed roles | Duplicates what Entra ID already provides; two sources of truth |
