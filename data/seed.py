"""Database seeder for IT Governance Dashboard.

Usage:
    python -m data.seed           # seed only (idempotent)
    python -m data.seed --reset   # drop & recreate data/govti.db first
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "govti.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}✅ {msg}{RESET}")


def _info(msg: str) -> None:
    print(f"{CYAN}ℹ️  {msg}{RESET}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}⚠️  {msg}{RESET}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"schema.sql not found at {SCHEMA_PATH}")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    _ok("Schema aplicado")


def seed_vendors(conn: sqlite3.Connection) -> list[int]:
    vendors = [
        ("Microsoft", "cloud", "suporte@microsoft.com", "https://microsoft.com"),
        ("Zabbix SIA", "monitoring", "support@zabbix.com", "https://zabbix.com"),
        ("Veeam Software", "backup", "sales@veeam.com", "https://veeam.com"),
        ("Fortinet", "security", "support@fortinet.com", "https://fortinet.com"),
        ("Dell Technologies", "hardware", "support@dell.com", "https://dell.com"),
    ]
    ids: list[int] = []
    cur = conn.cursor()
    for name, cat, contact, website in vendors:
        cur.execute(
            "INSERT OR IGNORE INTO vendors(name,category,contact,website) VALUES(?,?,?,?)",
            (name, cat, contact, website),
        )
        row = cur.execute("SELECT id FROM vendors WHERE name=?", (name,)).fetchone()
        ids.append(row["id"])
    conn.commit()
    _ok(f"Vendors inseridos: {len(vendors)}")
    return ids


def seed_contracts(conn: sqlite3.Connection, vendor_ids: list[int]) -> None:
    today = datetime.now(UTC).date()
    contracts = [
        (vendor_ids[0], "Microsoft 365 E3", 85000.00, today - timedelta(days=90), today + timedelta(days=275)),
        (vendor_ids[0], "Azure — IaaS Prod", 42000.00, today - timedelta(days=180), today + timedelta(days=185)),
        (vendor_ids[1], "Zabbix Enterprise", 12000.00, today - timedelta(days=30), today + timedelta(days=335)),
        (vendor_ids[2], "Veeam Backup & Replication", 18000.00, today - timedelta(days=60), today + timedelta(days=300)),
        (vendor_ids[3], "FortiGate FW Support", 24000.00, today - timedelta(days=15), today + timedelta(days=350)),
        (vendor_ids[4], "Dell PowerEdge HW Warranty", 9500.00, today - timedelta(days=120), today + timedelta(days=240)),
    ]
    cur = conn.cursor()
    for vendor_id, title, value, start, end in contracts:
        cur.execute(
            """INSERT OR IGNORE INTO contracts(vendor_id,title,value,start_date,end_date)
               VALUES(?,?,?,?,?)""",
            (vendor_id, title, value, str(start), str(end)),
        )
    conn.commit()
    _ok(f"Contracts inseridos: {len(contracts)}")


def seed_assets(conn: sqlite3.Connection, vendor_ids: list[int]) -> None:
    assets = [
        ("SRV-PROD-01", "server", "10.0.0.10", "DC Principal", vendor_ids[4]),
        ("SRV-PROD-02", "server", "10.0.0.11", "DC Principal", vendor_ids[4]),
        ("SRV-BKP-01", "server", "10.0.1.10", "DC Backup", vendor_ids[4]),
        ("FW-CORE-01", "network", "192.168.0.1", "Borda", vendor_ids[3]),
        ("SWT-CORE-01", "network", "10.0.0.2", "Core LAN", None),
        ("NAS-01", "storage", "10.0.2.10", "Storage", vendor_ids[4]),
        ("Azure-VNET-01", "cloud", None, "Azure East US", vendor_ids[0]),
        ("WIN11-CORP-042", "endpoint", "10.1.0.42", "Escritório SP", None),
        ("WIN11-CORP-043", "endpoint", "10.1.0.43", "Escritório SP", None),
        ("Zabbix-Server", "software", "10.0.0.20", "Monitoring", vendor_ids[1]),
    ]
    cur = conn.cursor()
    for name, atype, ip, loc, vid in assets:
        cur.execute(
            """INSERT OR IGNORE INTO assets(name,type,ip_address,location,vendor_id)
               VALUES(?,?,?,?,?)""",
            (name, atype, ip, loc, vid),
        )
    conn.commit()
    _ok(f"Assets inseridos: {len(assets)}")


def seed_m365_pillars(conn: sqlite3.Connection) -> None:
    pillars = [
        ("Identity", 88.0),
        ("Devices", 74.0),
        ("Apps", 92.0),
        ("Data", 81.0),
        ("Infrastructure", 69.0),
    ]
    cur = conn.cursor()
    for name, score in pillars:
        cur.execute(
            "INSERT OR IGNORE INTO m365_pillars(name,score) VALUES(?,?)",
            (name, score),
        )
    conn.commit()
    _ok(f"M365 pillars inseridos: {len(pillars)}")


def seed_checklist(conn: sqlite3.Connection) -> None:
    pillar_names = ["identity", "devices", "data", "apps", "infrastructure"]
    cur = conn.cursor()
    for pillar in pillar_names:
        total = random.randint(8, 15)
        passed = random.randint(total // 2, total)
        failed = total - passed
        cur.execute(
            "INSERT INTO checklist_runs(pillar,total,passed,failed) VALUES(?,?,?,?)",
            (pillar, total, passed, failed),
        )
        run_id = cur.lastrowid
        for i in range(total):
            status = "pass" if i < passed else "fail"
            cur.execute(
                "INSERT INTO checklist_items(run_id,title,status) VALUES(?,?,?)",
                (run_id, f"Checklist item {pillar}-{i+1}", status),
            )
    conn.commit()
    _ok("Checklist items inseridos")


def seed_lgpd(conn: sqlite3.Connection) -> None:
    processings = [
        ("Gestão de RH", "Contrato", "dados pessoais, dados financeiros", 1825, "Gadens Adv."),
        ("Marketing Digital", "Legítimo interesse", "email, nome, comportamento", 365, "Gadens Adv."),
        ("Acesso a sistemas", "Legítimo interesse", "logs de acesso, IP", 180, "TI Gadens"),
    ]
    incidents = [
        ("Vazamento de logs de acesso", "medium", "Logs expostos internamente por 2h"),
        ("Phishing Campaign", "high", "3 usuários clicaram em link malicioso"),
    ]
    cur = conn.cursor()
    for name, basis, cats, days, ctrl in processings:
        cur.execute(
            """INSERT OR IGNORE INTO lgpd_processings
               (name,legal_basis,data_categories,retention_days,controller)
               VALUES(?,?,?,?,?)""",
            (name, basis, cats, days, ctrl),
        )
    today = datetime.now(UTC)
    for title, sev, desc in incidents:
        cur.execute(
            "INSERT INTO lgpd_incidents(title,severity,description,reported_at) VALUES(?,?,?,?)",
            (title, sev, desc, (today - timedelta(days=random.randint(1, 30))).isoformat()),
        )
    conn.commit()
    _ok("LGPD data inserida")


def seed_score_history(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    cur = conn.cursor()
    base_date = datetime.now(UTC) - timedelta(days=29)
    for day in range(30):
        rec_date = base_date + timedelta(days=day)
        scores = {
            "strategic_alignment": rng.uniform(65, 85),
            "value_delivery": rng.uniform(70, 95),
            "risk_management": rng.uniform(55, 80),
            "resource_management": rng.uniform(62, 82),
            "performance_measure": rng.uniform(72, 95),
        }
        weights = {
            "strategic_alignment": 0.15,
            "value_delivery": 0.20,
            "risk_management": 0.25,
            "resource_management": 0.15,
            "performance_measure": 0.25,
        }
        global_score = sum(scores[k] * weights[k] for k in scores)
        if global_score >= 85:
            status = "OPERACIONAL"
        elif global_score >= 60:
            status = "DEGRADADO"
        else:
            status = "CRÍTICO"

        cur.execute(
            """INSERT INTO governance_scores_history
               (recorded_at,global_score,status,
                strategic_alignment,value_delivery,risk_management,
                resource_management,performance_measure)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                rec_date.isoformat(),
                round(global_score, 2),
                status,
                round(scores["strategic_alignment"], 2),
                round(scores["value_delivery"], 2),
                round(scores["risk_management"], 2),
                round(scores["resource_management"], 2),
                round(scores["performance_measure"], 2),
            ),
        )
    conn.commit()
    _ok("30 dias de histórico de scores inseridos")


def main() -> None:
    parser = argparse.ArgumentParser(description="IT Governance DB Seeder")
    parser.add_argument("--reset", action="store_true", help="Delete and recreate govti.db")
    args = parser.parse_args()

    if args.reset and DB_PATH.exists():
        DB_PATH.unlink()
        _warn(f"Banco removido: {DB_PATH}")

    _info(f"Conectando em {DB_PATH}")
    conn = _connect()
    _apply_schema(conn)

    vendor_ids = seed_vendors(conn)
    seed_contracts(conn, vendor_ids)
    seed_assets(conn, vendor_ids)
    seed_m365_pillars(conn)
    seed_checklist(conn)
    seed_lgpd(conn)
    seed_score_history(conn)

    conn.close()
    print(f"\n{GREEN}🎉 Seed concluído em {DB_PATH}{RESET}")


if __name__ == "__main__":
    main()
