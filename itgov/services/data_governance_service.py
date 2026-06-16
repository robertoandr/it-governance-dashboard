"""Cálculo de governança do pilar Dados a partir de sensitivity labels do Graph."""

from __future__ import annotations

from itgov.models.governance_data import DataGovernanceSummary, SensitivityLabelInfo


def calcular_resumo_dados(labels: list[dict]) -> DataGovernanceSummary:
    """Calcula o resumo de governança de dados a partir dos sensitivity labels do Graph.

    Args:
        labels: Lista de dicts retornados por SensitivityLabelGraphClient.get_labels().

    Returns:
        DataGovernanceSummary agregado. Lista vazia é um resultado válido —
        significa que o tenant não tem labels publicados.
    """
    resultado = [
        SensitivityLabelInfo(
            label_id=label.get("id") or "",
            name=label.get("name") or "(sem nome)",
            description=label.get("description"),
            is_active=label.get("isActive", True),
        )
        for label in labels
    ]

    return DataGovernanceSummary(total_labels=len(resultado), labels=resultado)
