import json
import logging
import subprocess  # nosec B404
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from config import settings

log = logging.getLogger(__name__)

SCAN_TARGETS = [
    "/data/scan-targets",
]


def run_gitleaks_scan():
    log.info("Iniciando scan gitleaks...")
    scan_id = str(uuid.uuid4())
    _, tmp_path = tempfile.mkstemp(suffix=".json", prefix="gitleaks-")
    output_file = Path(tmp_path)
    total_findings = 0

    for target in SCAN_TARGETS:
        if not Path(target).exists():
            log.warning("Target não existe: %s", target)
            continue

        cmd = [
            "gitleaks",
            "detect",
            "--source",
            target,
            "--report-format",
            "json",
            "--report-path",
            str(output_file),
            "--no-git",
            "--exit-code",
            "0",
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)  # nosec B603
        except subprocess.CalledProcessError as e:
            log.exception("gitleaks falhou: %s", e.stderr.decode())
            continue
        except FileNotFoundError:
            log.error("gitleaks não instalado no container!")
            return

        if not output_file.exists():
            log.info("Nenhum finding em %s", target)
            continue

        findings = json.loads(output_file.read_text())
        log.info("%d findings em %s", len(findings), target)
        total_findings += len(findings)

        with psycopg.connect(settings.POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                for f in findings:
                    cur.execute(
                        """
                        INSERT INTO governance.secret_findings (
                            scan_id, file_path, rule_id, severity,
                            secret_hint, commit_hash, author, detected_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                        (
                            scan_id,
                            f.get("File"),
                            f.get("RuleID"),
                            _severity_from_rule(f.get("RuleID", "")),
                            (f.get("Secret", "")[:6] + "..."),
                            f.get("Commit"),
                            f.get("Author"),
                            datetime.now(UTC),
                        ),
                    )

                cur.execute(
                    """
                    INSERT INTO governance.metrics
                        (time, metric_name, source, tags, value_num)
                    VALUES (NOW(), 'gitleaks_findings_total', 'gitleaks', %s, %s)
                """,
                    (json.dumps({"target": target, "scan_id": scan_id}), len(findings)),
                )

            conn.commit()

        output_file.unlink(missing_ok=True)

    log.info("Scan concluído. Total: %d findings", total_findings)


def _severity_from_rule(rule_id: str) -> str:
    critical = [
        "aws",
        "gcp",
        "azure",
        "github-pat",
        "github-pat-fine-grained",
        "private-key",
        "stripe",
    ]
    if any(c in rule_id.lower() for c in critical):
        return "critical"
    return "high"
