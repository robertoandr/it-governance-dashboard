# ADR-0005: Flask + Grafana Coexistence Architecture

**Status:** Accepted  
**Date:** 2026-02-12  
**Author:** Roberto Andrade

---

## Context

The IT Governance Dashboard serves two functionally distinct categories of UI:

1. **Business layer** — transactional screens for managing vendors (Fornecedores), contracts (Contratos), the governance hub (Hub), and service desk integration (Service Desk). These screens require form submission, RBAC-gated write operations, workflow actions (contract renewal, vendor suspension), and tight integration with business logic.

2. **Observability layer** — infrastructure metrics (M365 health, server uptime, Zabbix dashboards) and KPI visualizations. These are read-only, metric-heavy, and best served by a purpose-built visualization tool.

Two architectural options were considered:

**Option A — Flask renders everything**: Flask serves both the business screens and the metrics dashboards. Metrics are pulled from TimescaleDB continuous aggregates and rendered via a charting library (Chart.js, Plotly). All UI is Flask-templated.

**Option B — Flask + Grafana embed via Nginx**: Flask owns the business layer. Grafana (already deployed for infrastructure monitoring) is embedded as an iframe for observability dashboards. Nginx acts as a single reverse-proxy gateway, routing `/app/*` to Flask and `/grafana/*` to Grafana. Auth is handled at the Nginx layer (Entra ID JWT validation) with Grafana in anonymous-org mode behind the proxy.

---

## Decision

Adopt **Option B: Flask + Grafana coexistence, unified under Nginx as the single gateway**.

### Responsibility split

| Layer | Owns | Technology |
|---|---|---|
| **Flask** | Fornecedores, Contratos, Hub, Service Desk | Python, Flask[async], Flask-RESTX, Pydantic, SQLAlchemy |
| **Grafana** | M365 metrics, Infrastructure observability, SLA trend charts | Grafana OSS, TimescaleDB datasource plugin |
| **Nginx** | TLS termination, JWT validation, routing, rate limiting | Nginx + `ngx_http_auth_jwt_module` |

### Routing rules (Nginx)

```
/             → Flask (dashboard home, RBAC login redirect)
/app/*        → Flask upstream (business modules)
/api/v1/*     → Flask upstream (REST API)
/grafana/*    → Grafana upstream (strip prefix, anonymous auth)
/static/*     → Nginx static file serving
```

### Grafana configuration

- Grafana runs in a dedicated Docker container / Kubernetes pod.
- `GF_AUTH_ANONYMOUS_ENABLED=true`, `GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer` — read-only public access behind the Nginx auth boundary.
- Grafana datasource configured to query TimescaleDB (same PostgreSQL instance as Flask, read-only user).
- Dashboard panels are provisioned via `grafana/provisioning/dashboards/*.json` — version-controlled, not hand-edited in the UI.
- Grafana's own user management is disabled; all access control is at the Nginx/Flask layer.

### Flask embed contract

Flask pages that need to display a Grafana panel use `<iframe>` with `src="/grafana/d/{uid}/{slug}?kiosk=1"`. The `kiosk=1` parameter hides the Grafana nav chrome. Panel-level embed uses `&panelId=N`.

---

## Consequences

### Positive
- Grafana provides best-in-class metric visualization with no custom charting code in Flask.
- Strict separation of concerns: Flask developers do not need to know Grafana internals; ops/monitoring team does not need to touch Flask.
- Nginx as single gateway: one TLS certificate, one auth boundary, one rate-limit policy.
- Grafana dashboard changes (adding panels, adjusting time ranges) do not require Flask deployment.
- TimescaleDB continuous aggregates serve both Flask queries and Grafana datasource — no data duplication.

### Negative
- Two application processes to deploy and monitor (Flask + Grafana), even though Nginx unifies them externally.
- `kiosk` iframe embedding has UX limitations — mobile responsiveness of embedded panels requires explicit Grafana panel sizing.
- Grafana anonymous mode means Nginx must correctly block unauthenticated access to `/grafana/*` — a misconfiguration exposes dashboards publicly.
- Grafana version upgrades must be tested against provisioned dashboard JSON schema changes.

---

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| Flask renders all charts (Chart.js) | High development cost to replicate Grafana's time-series UX; no alerting integration |
| Separate domains (app.domain + grafana.domain) | Two CORS origins, two TLS certs, two auth boundaries — more complex than a single Nginx gateway |
| Metabase instead of Grafana | No existing Metabase on infra stack; Grafana already deployed for Zabbix |
| Superset | Python-native but no existing deployment; overkill for embed-only use case |
