"""AD + WinRM patch compliance collector.

Escreve gov_patch_compliance (agregado) e gov_windows_patches (por host) no InfluxDB.

Pipeline:
  AD (ldap3, NTLM) → enumera Windows Server ativos (exclui workstations)
  → fan-out WinRM (pywinrm, NTLM) → Get-HotFix + WUA search por máquina
  → agrega summary + ponto por host → escreve no InfluxDB

Sem WSUS, sem Intune, sem agente. Get-HotFix + Windows Update Agent via WinRM.

Autenticação: NTLM — servidor de coleta Linux fora do domínio.
Kerberos exigiria krb5.conf + keytab; NTLM funciona com só usuário/senha.
NTLM depende de MD4 (hashlib não suporta em Python 3.12 / OpenSSL 3.x) —
por isso pycryptodome é dependência obrigatória, não opcional.

Deps adicionais em requirements.txt: ldap3, pywinrm, pycryptodome
Schedule: every 6 hours via APScheduler.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import structlog
import winrm
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from ldap3 import ALL, NTLM, SUBTREE, Connection, Server

sys.path.insert(0, __import__("pathlib").Path(__file__).parent.parent.__str__())
from config import settings

log = structlog.get_logger("patch_collector")

# ── PowerShell embarcado ─────────────────────────────────────────────────────
# Duplo {{ }} porque o bloco passa por str.format(threshold=N) antes de ir pro WinRM.
# TryParse usa InvariantCulture primeiro para contornar locales pt-BR que invertem dia/mês.
_PS_SCRIPT = r"""
$thresh = {threshold}
try {{ $hotfixes = Get-HotFix -ErrorAction Stop }}
catch {{
    @{{ Reachable=$false; Error=$_.Exception.Message }} | ConvertTo-Json -Compress; exit
}}
$parsed=@(); $nullCount=0
foreach ($hf in $hotfixes) {{
    $raw = $hf.InstalledOn
    if ($null -eq $raw -or "$raw" -eq '') {{ $nullCount++; continue }}
    if ($raw -is [datetime]) {{ $parsed += $raw; continue }}
    $tmp = [datetime]::MinValue
    $ok = (
        [datetime]::TryParse("$raw",[System.Globalization.CultureInfo]::InvariantCulture,[System.Globalization.DateTimeStyles]::None,[ref]$tmp) -or
        [datetime]::TryParse("$raw",[System.Globalization.CultureInfo]::new('en-US'),[System.Globalization.DateTimeStyles]::None,[ref]$tmp) -or
        [datetime]::TryParse("$raw",[System.Globalization.CultureInfo]::new('pt-BR'),[System.Globalization.DateTimeStyles]::None,[ref]$tmp) -or
        [datetime]::TryParse("$raw",[System.Globalization.CultureInfo]::CurrentCulture,[System.Globalization.DateTimeStyles]::None,[ref]$tmp)
    )
    if ($ok) {{ $parsed += $tmp }} else {{ $nullCount++ }}
}}
$now = Get-Date
$last = if ($parsed) {{ ($parsed | Sort-Object -Descending)[0] }} else {{ $null }}
$days = if ($last) {{ [math]::Floor(($now - $last).TotalDays) }} else {{ $null }}
$comp = ($null -ne $days -and $days -le $thresh)

$osVersion = $null
try {{ $osVersion = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption }} catch {{}}

# Reboot pendente — checagem local via registro, rápida, não depende de rede.
$rebootRequired = $false
try {{
    if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') {{ $rebootRequired = $true }}
    elseif (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') {{ $rebootRequired = $true }}
    elseif (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue) {{ $rebootRequired = $true }}
}} catch {{}}

# Updates pendentes via Windows Update Agent — pode ser lento (busca real contra
# WSUS/Windows Update); best-effort, falha aqui não derruba o resultado do host.
$pending = $null; $critical = $null; $wuaError = $null
try {{
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $searchResult = $searcher.Search("IsInstalled=0 and IsHidden=0")
    $pending = $searchResult.Updates.Count
    $critical = @($searchResult.Updates | Where-Object {{ $_.MsrcSeverity -eq 'Critical' }}).Count
}} catch {{
    $wuaError = $_.Exception.Message
}}

@{{
    Reachable       = $true
    IsCompliant     = $comp
    LastPatchDate   = if ($last) {{ $last.ToString('yyyy-MM-dd') }} else {{ $null }}
    DaysSinceLast   = $days
    HotfixCount     = @($hotfixes).Count
    NullDateCount   = $nullCount
    OsVersion       = $osVersion
    RebootRequired  = $rebootRequired
    PendingUpdates  = $pending
    CriticalUpdates = $critical
    WuaError        = $wuaError
}} | ConvertTo-Json -Compress
"""


# ── Modelo de resultado por host ─────────────────────────────────────────────
@dataclass
class _HostResult:
    computer_name: str
    reachable: bool = False
    is_compliant: bool = False
    last_patch_date: str | None = None
    days_since_last: int | None = None
    hotfix_count: int = 0
    null_date_count: int = 0
    os_version: str | None = None
    reboot_required: bool = False
    pending_updates: int | None = None
    critical_updates: int | None = None
    error: str | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── 1. Enumerar máquinas ativas no AD ────────────────────────────────────────
def _enum_domain_computers() -> list[str]:
    """Retorna dNSHostName de máquinas Windows ativas com logon recente."""
    server = Server(settings.LDAP_SERVER, get_info=ALL)
    conn = Connection(
        server,
        user=settings.LDAP_BIND_DN,
        password=settings.LDAP_BIND_PASSWORD,
        authentication=NTLM,
        auto_bind=True,
    )

    # lastLogonTimestamp em Windows FILETIME (100ns ticks desde 01/01/1601)
    epoch_1601 = datetime(1601, 1, 1, tzinfo=UTC)
    cutoff_ts = datetime.now(UTC).timestamp() - settings.PATCH_STALE_MACHINE_DAYS * 86400
    cutoff_ft = int((datetime.fromtimestamp(cutoff_ts, UTC) - epoch_1601).total_seconds() * 1e7)

    # Restrito a Windows Server: sem isso, o filtro pega TODO computer object
    # ativo no AD (estações de trabalho inclusas) — WinRM normalmente não está
    # habilitado em desktops de usuário, gerando "unreachable" em massa.
    ldap_filter = (
        f"(&(objectClass=computer)"
        f"(!(userAccountControl:1.2.840.113556.1.4.803:=2))"  # não desabilitado
        f"(operatingSystem=*Windows Server*)"
        f"(lastLogonTimestamp>={cutoff_ft}))"
    )
    conn.search(
        settings.LDAP_BASE_DN,
        ldap_filter,
        search_scope=SUBTREE,
        attributes=["dNSHostName", "name"],
    )

    hosts = []
    for entry in conn.entries:
        host = str(entry.dNSHostName) if entry.dNSHostName else str(entry.name)
        if host:
            hosts.append(host)
    conn.unbind()

    log.info("ad_inventory", total=len(hosts), stale_days=settings.PATCH_STALE_MACHINE_DAYS)
    return hosts


# ── 2. Consultar hotfixes via WinRM (NTLM) ───────────────────────────────────
def _query_host(host: str) -> _HostResult:
    """Executa Get-HotFix na máquina remota via WinRM/NTLM e retorna _HostResult."""
    try:
        sess = winrm.Session(
            f"http://{host}:5985/wsman",
            auth=(settings.WINRM_USER, settings.WINRM_PASSWORD),
            transport="ntlm",  # NTLM: Linux sem domínio. Kerberos exigiria krb5.conf + keytab.
            read_timeout_sec=settings.WINRM_TIMEOUT_S + 5,
            operation_timeout_sec=settings.WINRM_TIMEOUT_S,
        )
        result = sess.run_ps(_PS_SCRIPT.format(threshold=settings.PATCH_THRESHOLD_DAYS))

        if result.status_code != 0:
            err = result.std_err.decode("utf-8", "ignore")[:300]
            log.warning("winrm_ps_error", host=host, stderr=err)
            return _HostResult(host, error=err)

        data = json.loads(result.std_out.decode("utf-8", "ignore") or "{}")
        if not data.get("Reachable"):
            return _HostResult(host, error=data.get("Error", "ps_reported_unreachable"))

        if data.get("WuaError"):
            log.warning("winrm_wua_search_failed", host=host, error=data["WuaError"][:300])

        return _HostResult(
            computer_name=host,
            reachable=True,
            is_compliant=bool(data.get("IsCompliant")),
            last_patch_date=data.get("LastPatchDate"),
            days_since_last=data.get("DaysSinceLast"),
            hotfix_count=int(data.get("HotfixCount") or 0),
            null_date_count=int(data.get("NullDateCount") or 0),
            os_version=data.get("OsVersion"),
            reboot_required=bool(data.get("RebootRequired")),
            pending_updates=data.get("PendingUpdates"),
            critical_updates=data.get("CriticalUpdates"),
        )
    except Exception:
        log.warning("winrm_host_failed", host=host, exc_info=True)
        return _HostResult(host, error="winrm_exception")


# ── 3. Fan-out + agregação ────────────────────────────────────────────────────
def _collect_all() -> tuple[dict, list[_HostResult]]:
    hosts = _enum_domain_computers()
    results: list[_HostResult] = []

    with ThreadPoolExecutor(max_workers=settings.PATCH_MAX_WORKERS) as pool:
        futures = {pool.submit(_query_host, h): h for h in hosts}
        for fut in as_completed(futures):
            results.append(fut.result())

    reachable = [r for r in results if r.reachable]
    unreachable = [r for r in results if not r.reachable]
    compliant = [r for r in reachable if r.is_compliant]
    # Máquinas com >5 hotfixes sem data — dado pouco confiável (CBS incompleto)
    low_confidence = [r for r in reachable if r.null_date_count > 5]

    pct = round(len(compliant) / len(reachable) * 100, 1) if reachable else 0.0

    log.info(
        "patch_collection_complete",
        total=len(results),
        reachable=len(reachable),
        unreachable=len(unreachable),
        compliant=len(compliant),
        compliance_pct=pct,
        low_confidence=len(low_confidence),
    )

    stats = {
        "generated_at": datetime.now(UTC).isoformat(),
        "threshold_days": settings.PATCH_THRESHOLD_DAYS,
        "total_machines": len(results),
        "reachable": len(reachable),
        "unreachable": len(unreachable),
        "compliant": len(compliant),
        "non_compliant": len(reachable) - len(compliant),
        "compliance_pct": pct,
        "low_confidence": len(low_confidence),
        "hosts": [asdict(r) for r in results],
    }
    return stats, results


# ── 4. Pontos por host (gov_windows_patches) ─────────────────────────────────
def _build_host_points(results: list[_HostResult]) -> list[Point]:
    """Um ponto por host alcançável — dado agregado continua em gov_patch_compliance."""
    points = []
    for r in results:
        if not r.reachable:
            continue
        p = (
            Point("gov_windows_patches")
            .tag("host", r.computer_name)
            .tag("os", r.os_version or "unknown")
            .field("reboot_required", bool(r.reboot_required))
            .field("last_scan", r.checked_at)
            .time(datetime.fromisoformat(r.checked_at), WritePrecision.S)
        )
        if r.pending_updates is not None:
            p = p.field("pending_updates", int(r.pending_updates))
        if r.critical_updates is not None:
            p = p.field("critical_updates", int(r.critical_updates))
        points.append(p)
    return points


# ── 5. Escrita no InfluxDB ────────────────────────────────────────────────────
def collect() -> None:
    """Coleta patch compliance: agregado em gov_patch_compliance + detalhe por
    host em gov_windows_patches."""
    log.info("patch_collection_started")
    stats, results = _collect_all()
    collected_at = datetime.now(UTC)

    point = (
        Point("gov_patch_compliance")
        .field("compliance_pct", float(stats["compliance_pct"]))
        .field("compliant", int(stats["compliant"]))
        .field("non_compliant", int(stats["non_compliant"]))
        .field("total_machines", int(stats["total_machines"]))
        .field("reachable", int(stats["reachable"]))
        .field("unreachable", int(stats["unreachable"]))
        .field("low_confidence", int(stats["low_confidence"]))
        .time(collected_at, WritePrecision.S)
    )
    records = [point, *_build_host_points(results)]

    try:
        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=records,
            )
    except Exception:
        log.exception(
            "patch_write_failed",
            measurement="gov_patch_compliance",
            total_machines=stats["total_machines"],
        )
        raise

    log.info(
        "patch_metrics_written",
        measurement="gov_patch_compliance",
        host_points_written=len(records) - 1,
    )


def run() -> None:
    """Entry point para APScheduler."""
    try:
        collect()
    except Exception:
        log.exception("patch_job_failed")
