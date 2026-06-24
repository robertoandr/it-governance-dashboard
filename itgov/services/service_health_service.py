"""Cálculo do resumo de Service Health a partir dos dados do Graph."""

from __future__ import annotations

from itgov.models.governance_service_health import ServiceHealthSummary, ServiceStatus

# Mapa status_raw → (label, code)  code: 0=OK, 1=atenção, 2=crítico
_STATUS_MAP: dict[str, tuple[str, int]] = {
    "serviceOperational": ("OK", 0),
    "serviceRestored": ("OK", 0),
    "postIncidentReviewPublished": ("OK", 0),
    "falsePositive": ("OK", 0),
    "verifyingService": ("ATENÇÃO", 1),
    "investigating": ("ATENÇÃO", 1),
    "restoringService": ("ATENÇÃO", 1),
    "extendedRecovery": ("ATENÇÃO", 1),
    "serviceDegradation": ("CRÍTICO", 2),
    "serviceInterruption": ("CRÍTICO", 2),
}


def calcular_resumo_service_health(services_raw: list[dict]) -> ServiceHealthSummary:
    services: list[ServiceStatus] = []

    for svc in services_raw:
        status_raw = svc.get("status", "")
        label, code = _STATUS_MAP.get(status_raw, ("ATENÇÃO", 1))
        services.append(
            ServiceStatus(
                service_id=svc.get("id", ""),
                display_name=svc.get("service", svc.get("id", "")),
                status_raw=status_raw,
                status_label=label,
                status_code=code,
            )
        )

    # Ordenar: crítico primeiro, depois atenção, depois OK
    services.sort(key=lambda s: -s.status_code)

    ok = sum(1 for s in services if s.status_code == 0)
    warning = sum(1 for s in services if s.status_code == 1)
    critical = sum(1 for s in services if s.status_code == 2)

    return ServiceHealthSummary(
        total=len(services),
        ok=ok,
        warning=warning,
        critical=critical,
        all_operational=(critical == 0 and warning == 0),
        services=services,
    )
