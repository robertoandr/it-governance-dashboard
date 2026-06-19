"""Cálculo de governança do pilar Dispositivos a partir de dados do Graph."""

from __future__ import annotations

from datetime import UTC, datetime

from itgov.models.governance_devices import DeviceSummary

_STALE_DAYS = 90


def calcular_resumo_dispositivos(devices: list[dict]) -> DeviceSummary:
    """Calcula o resumo de governança a partir da lista de dispositivos do Graph.

    Args:
        devices: Lista de dicts retornados por DeviceGraphClient.get_devices().

    Returns:
        DeviceSummary agregado.
    """
    total = len(devices)
    stale = 0
    managed_count = 0
    unmanaged_known_count = 0
    os_dist: dict[str, int] = {}
    trust_dist: dict[str, int] = {}
    agora = datetime.now(UTC)

    for device in devices:
        os_nome = device.get("operatingSystem") or "desconhecido"
        os_dist[os_nome] = os_dist.get(os_nome, 0) + 1

        trust = device.get("trustType") or "desconhecido"
        trust_dist[trust] = trust_dist.get(trust, 0) + 1

        is_managed = device.get("isManaged")
        if is_managed is True:
            managed_count += 1
        elif is_managed is False:
            unmanaged_known_count += 1
        # None (Intune não usado) não entra no denominador de managed_pct

        last_signin = device.get("approximateLastSignInDateTime")
        if last_signin:
            try:
                last_dt = datetime.fromisoformat(last_signin.replace("Z", "+00:00"))
                if (agora - last_dt).days > _STALE_DAYS:
                    stale += 1
            except ValueError:
                pass

    managed_denom = managed_count + unmanaged_known_count
    managed_pct = round(managed_count / managed_denom * 100, 1) if managed_denom > 0 else None

    return DeviceSummary(
        total_devices=total,
        stale_90d=stale,
        managed_pct=managed_pct,
        os_distribution=os_dist,
        trust_type_distribution=trust_dist,
    )
