# ADR-0003: LGPD Compliance Strategy

**Status:** Accepted  
**Date:** 2026-01-22  
**Author:** Roberto Andrade

---

## Context

The IT Governance Dashboard processes personal data of vendor contacts (name, email, phone). Under Brazil's Lei Geral de Proteção de Dados (LGPD — Law 13.709/2018), the organization must implement appropriate technical and organizational measures to protect this data and honor data subject rights.

Key LGPD obligations relevant to this system:

- **Art. 6** — Processing must have a lawful basis (e.g., contract execution, legitimate interest).
- **Art. 18** — Data subjects have rights: access, correction, deletion, portability.
- **Art. 46** — Controller must adopt security measures to protect personal data.
- **Art. 48** — Breach notification obligations within 72 hours to the DPA (ANPD).

The dashboard stores contact information (nome, email, telefone) in the `fornecedores` table. This data is collected for legitimate interest (contract management) and must be retained only as long as needed.

The team evaluated two approaches for handling the time-series audit log (which may contain personal data in JSONB payloads):

1. **InfluxDB** — no native row-level deletion; entire series must be deleted or a tombstone approach used. Makes right-to-erasure complex.
2. **TimescaleDB** — PostgreSQL-native, supports standard `DELETE` and `UPDATE` for GDPR/LGPD erasure. Partial chunk deletion available via compression policies.

---

## Decision

Adopt the following LGPD compliance measures:

### 1. Data minimization
Store only contact data strictly necessary for contract management: `contato_nome`, `contato_email`, `contato_telefone`. No social security numbers, no personal financial data of individuals.

### 2. Soft delete with erasure path
The `deleted_at` column on `fornecedores` enables soft deletion. A separate erasure job can anonymize personal fields (overwrite with `[REMOVIDO]`) when a data subject invokes their right to erasure, without breaking referential integrity on historical contracts.

### 3. Audit log in TimescaleDB
Use TimescaleDB (`contratos_eventos` hypertable) for the audit/event log instead of InfluxDB. TimescaleDB supports standard SQL `DELETE`, making it possible to honor erasure requests on event payloads without time-series tombstone complexity.

### 4. Data retention policy
- Vendor contact data: retained while the vendor has active contracts + 5 years post-termination (legal obligation for tax records).
- Event log: retained for 2 years, then auto-compressed and eventually dropped via TimescaleDB retention policy.

### 5. Access logging
All reads of personal data fields are logged via `structlog` with user ID and timestamp. Logs are stored for 1 year.

### 6. Breach notification runbook
A documented runbook (in the ops wiki) defines the 72-hour ANPD notification process. This ADR records the technical obligation; the runbook covers the organizational process.

### 7. Legal basis
Processing is justified under LGPD Art. 7, VI (legitimate interest — contract management) and Art. 7, V (contract execution). Legal basis is documented in the Privacy Notice, not in code.

---

## Consequences

### Positive
- TimescaleDB choice (see ADR-0004) directly enables LGPD-compliant erasure on event payloads.
- Soft-delete pattern separates business deletion from legal erasure, satisfying both operational and compliance requirements.
- Structured logging enables audit trails required for regulatory response.

### Negative
- Anonymization job must be built and scheduled (Sprint 15-16 scope).
- Retention policies require operational monitoring to ensure they fire correctly.
- Contact data in JSONB `payload` fields in `contratos_eventos` must be avoided or explicitly excluded from retention scans.

---

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| InfluxDB for audit log | No native SQL DELETE; right-to-erasure on payload data would require series-level deletion, losing unrelated events |
| Hard delete | Breaks referential integrity on contracts; cannot recover from accidental deletion |
| External DLP service | Overkill for current scale; adds vendor dependency |
