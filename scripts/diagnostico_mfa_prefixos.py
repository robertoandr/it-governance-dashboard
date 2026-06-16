"""Script diagnóstico — descobre padrões reais de UPNs no tenant.

Roda uma vez, sem gravar nada no InfluxDB.
Saída: resumo de prefixos + lista anonimizada de contas suspeitas.

Uso:
    cd /home/zabbix/projects/it-governance-dashboard
    python scripts/diagnostico_mfa_prefixos.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Carrega só as vars Azure do .env — sem depender do Settings do collector
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line.startswith("AZURE_") and "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, str(Path(__file__).parent.parent / "collector"))

from base_oauth_collector import BaseOAuthCollector  # noqa: E402 — import depende do sys.path.insert acima

_TENANT_ID = os.environ["AZURE_TENANT_ID"]
_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
_CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]

# ── Padrões candidatos (ajuste após ver o output) ────────────────────────────
_REGEXES_NON_HUMAN = [
    r"^svc[-_]",  # service accounts svc-xxx / svc_xxx
    r"^sa[-_]",  # service accounts sa-xxx / sa_xxx
    r"^srv[-_]",  # service accounts srv-xxx
    r"^app[-_]",  # app registrations app-xxx
    r"^adm[-_]",  # admin dedicado adm-xxx
    r"^admin[-_]",  # admin dedicado admin-xxx
    r"^bot[-_]",  # bots
    r"^noreply",  # caixas noreply
    r"^no-reply",
    r"^notif",  # notifications@
    r"^monitor",  # monitoring@
    r"^backup",
    r"^scan",
    r"^print",
    r"^shared",
    r"^grp[-_]",  # grupos de distribuição com caixa
    r"^dl[-_]",  # distribution lists
]

_REGEXES_ROOM = [
    r"^conf[-_]",  # salas de conferência
    r"^sala[-_]",
    r"^room[-_]",
    r"^meeting",
    r"^equip",  # equipamentos reserváveis
    r"^proj[-_]",  # projetores
    r"^tv[-_]",
]


def _anonimizar(upn: str) -> str:
    """Preserva prefixo e domínio, ofusca o nome real."""
    partes = upn.split("@")
    if len(partes) != 2:
        return "invalido@?"
    local, dominio = partes
    return f"{local[:3]}***@{dominio}"


class DiagnosticoCollector(BaseOAuthCollector):
    def __init__(self) -> None:
        super().__init__(
            tenant_id=_TENANT_ID,
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            scopes=_SCOPES,
        )

    def _todos_usuarios(self) -> list[dict]:
        """Puxa todos os usuários com os campos de classificação."""
        return list(
            self._paginate(
                f"{_GRAPH_BASE}/users",
                params={
                    "$select": ("displayName,userPrincipalName,accountEnabled,userType,mail,jobTitle"),
                    "$top": "999",
                },
            )
        )

    def _mfa_registrados(self) -> set[str]:
        """Retorna conjunto de UPNs com MFA registrado."""
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/reports/authenticationMethods/userRegistrationDetails",
                    params={"$select": "userPrincipalName,isMfaRegistered", "$top": "999"},
                )
            )
            return {i["userPrincipalName"] for i in items if i.get("isMfaRegistered")}
        except Exception as exc:
            print(f"\n[AVISO] Não foi possível buscar status MFA: {exc}")
            print("        → Reports.Read.All ausente? mfa_pct não será calculado.\n")
            return set()

    def _classificar(self, upn: str, user_type: str | None) -> str:
        """Classifica a conta em uma das 4 categorias."""
        upn_lower = upn.lower()

        # Externos: padrão Graph (#EXT#) ou userType == Guest
        if "#ext#" in upn_lower or user_type == "Guest":
            return "externo"

        # Recursos: regex de sala/sala/conf
        for pat in _REGEXES_ROOM:
            if re.search(pat, upn_lower):
                return "sala_recurso"

        # Não-humanos: prefixos de service account
        for pat in _REGEXES_NON_HUMAN:
            if re.search(pat, upn_lower):
                return "nao_humano"

        return "humano"

    def rodar(self) -> None:
        print("\n=== DIAGNÓSTICO MFA — PREFIXOS REAIS DO TENANT ===\n")

        usuarios = self._todos_usuarios()
        mfa_ok = self._mfa_registrados()

        total = len(usuarios)
        print(f"Total de objetos retornados pelo Graph: {total}\n")

        buckets: dict[str, list[dict]] = defaultdict(list)
        prefixos_counter: Counter = Counter()

        for u in usuarios:
            upn = u.get("userPrincipalName", "")
            habilitado = u.get("accountEnabled", True)
            user_type = u.get("userType")

            if not habilitado:
                buckets["desabilitado"].append(u)
                continue

            classe = self._classificar(upn, user_type)
            buckets[classe].append({**u, "mfa_ok": upn in mfa_ok})

            # Coleta prefixo real (3 primeiros chars antes de - _ . ou @)
            m = re.match(r"^([a-z0-9]+)[-_\.@]", upn.lower())
            if m:
                prefixos_counter[m.group(1)] += 1

        # ── Resumo por bucket ────────────────────────────────────────────────
        print("--- RESUMO POR CATEGORIA ---")
        for cat in ["humano", "nao_humano", "sala_recurso", "externo", "desabilitado"]:
            lst = buckets.get(cat, [])
            sem_mfa = [u for u in lst if not u.get("mfa_ok", True)]
            print(f"  {cat:<15}: {len(lst):>4} contas   (sem MFA: {len(sem_mfa)})")

        # ── MFA % sobre humanos ativos ───────────────────────────────────────
        humanos = buckets.get("humano", [])
        if humanos:
            com_mfa = sum(1 for u in humanos if u.get("mfa_ok"))
            pct = round(com_mfa / len(humanos) * 100, 1)
            print(f"\n  mfa_adoption_humanos_ativos: {pct}%  ({com_mfa}/{len(humanos)})")

        # ── Top 20 prefixos reais ────────────────────────────────────────────
        print("\n--- TOP 20 PREFIXOS DE UPN ---")
        for prefixo, qtd in prefixos_counter.most_common(20):
            print(f"  {prefixo:<20}: {qtd:>4}")

        # ── Contas nao_humano (anonimizadas) ────────────────────────────────
        print("\n--- CONTAS NÃO-HUMANAS DETECTADAS (anonimizadas) ---")
        for u in sorted(buckets.get("nao_humano", []), key=lambda x: x.get("userPrincipalName", "")):
            upn = u.get("userPrincipalName", "")
            mfa = "✓ MFA" if u.get("mfa_ok") else "✗ sem MFA"
            print(f"  {_anonimizar(upn):<40}  {mfa}")

        # ── Salas/recursos ───────────────────────────────────────────────────
        print("\n--- SALAS/RECURSOS DETECTADOS (anonimizadas) ---")
        for u in sorted(buckets.get("sala_recurso", []), key=lambda x: x.get("userPrincipalName", "")):
            upn = u.get("userPrincipalName", "")
            mfa = "✓ MFA" if u.get("mfa_ok") else "✗ sem MFA"
            print(f"  {_anonimizar(upn):<40}  {mfa}")

        # ── Humanos SEM MFA (precisa de ação) ───────────────────────────────
        print("\n--- HUMANOS ATIVOS SEM MFA (anonimizados) ---")
        sem_mfa = [u for u in humanos if not u.get("mfa_ok")]
        for u in sorted(sem_mfa, key=lambda x: x.get("userPrincipalName", "")):
            upn = u.get("userPrincipalName", "")
            titulo = u.get("jobTitle") or "?"
            print(f"  {_anonimizar(upn):<40}  cargo: {titulo}")

        print(f"\n  Total humanos sem MFA: {len(sem_mfa)}")

        # ── Suspeitos: humano mas parece não-humano ──────────────────────────
        print("\n--- HUMANOS COM NOME SUSPEITO (revisar regex) ---")
        _suspeito = re.compile(
            r"(service|svc|bot|monitor|scan|print|backup|shared|noreply|no.reply|"
            r"sala|conf|room|meeting|equip|admin|adm\d|grp|notify|alert|relay)",
            re.IGNORECASE,
        )
        suspeitos = [
            u
            for u in humanos
            if _suspeito.search(u.get("userPrincipalName", "")) or _suspeito.search(u.get("displayName", ""))
        ]
        if suspeitos:
            for u in suspeitos:
                upn = u.get("userPrincipalName", "")
                nome = u.get("displayName", "")
                mfa = "✓" if u.get("mfa_ok") else "✗"
                print(f"  {mfa} {_anonimizar(upn):<40}  display: {nome[:30]}")
        else:
            print("  (nenhum suspeito encontrado — regex estão bem calibrados)")

        print("\n=== FIM DO DIAGNÓSTICO ===\n")


if __name__ == "__main__":
    DiagnosticoCollector().rodar()
