#!/usr/bin/env python3
"""Sincroniza inventário e falhas de CFTV da planilha Dvrs.xlsx (aba 'Atualizado ') para o banco SQLite.

Atualiza:
1. `dvr_unidades`:
   - Cadastra os 4 gravadores nuvem/DDNS (Autoshop, Casa 1, Casa_2, Casa_Praia) com acesso='nuvem',
     modelo, número de série e capacidade de canais.
   - Atualiza modelos, séries e total de canais dos 22 gravadores locais.
   - Cadastra Sede_4_3_2_Loja (Sede Centro).
2. `cftv_canais`:
   - Registra ou atualiza os canais com falha identificados na auditoria com situacao='ativo',
     imagem='sem', origem='auditoria-dvrs-xlsx'.

Uso:
    python3 scripts/sincronizar_auditoria_dvrs.py
    python3 scripts/sincronizar_auditoria_dvrs.py --db data/app.db
    python3 scripts/sincronizar_auditoria_dvrs.py --container itgov-app
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime

PADRAO_PLANILHA = "/srv/compartilhado/Dvrs.xlsx"
ABA_ALVO = "Atualizado "
CONTAINER_PADRAO = "itgov-app"

# Gravadores com acesso nuvem/DDNS e sua unidade correspondente
GRAVADORES_NUVEM: dict[str, int | None] = {
    "Autoshop": 2,  # Unidade Autoshop
    "Casa 1": None,  # Residencial / sem unidade
    "Casa_2": None,  # Residencial / sem unidade
    "Casa_Praia": None,  # Residencial / sem unidade
}


def norm(s: str) -> str:
    """Normaliza string para comparação alfanumérica insensível a maiúsculas/espaços/acentos."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def extrair_dados_planilha(caminho_xlsx: str) -> list[dict[str, str]]:
    """Extrai os registros da aba 'Atualizado ' sem depender de bibliotecas externas (openpyxl)."""
    if not os.path.isfile(caminho_xlsx):
        raise FileNotFoundError(f"Arquivo de planilha não encontrado: {caminho_xlsx}")

    with zipfile.ZipFile(caminho_xlsx) as z:
        # 1. Encontrar o sheetId da aba "Atualizado " no workbook.xml
        with z.open("xl/workbook.xml") as f:
            wb_tree = ET.parse(f)

        sheet_filename = None
        for sheet_el in wb_tree.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
            nome_aba = sheet_el.attrib.get("name", "")
            if nome_aba.strip().lower() == "atualizado":
                r_id = sheet_el.attrib.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""
                )
                # Buscar no _rels/workbook.xml.rels
                with z.open("xl/_rels/workbook.xml.rels") as rf:
                    rels_tree = ET.parse(rf)
                for rel in rels_tree.iter("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
                    if rel.attrib.get("Id") == r_id:
                        target = rel.attrib.get("Target", "")
                        sheet_filename = f"xl/{target}" if not target.startswith("xl/") else target
                        break
                break

        if not sheet_filename or sheet_filename not in z.namelist():
            # Fallback para sheet3.xml se rels não resolver
            sheet_filename = "xl/worksheets/sheet3.xml"

        # 2. Ler sharedStrings
        with z.open("xl/sharedStrings.xml") as f:
            ss_tree = ET.parse(f)
        strings = [t.text or "" for t in ss_tree.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]

        # 3. Ler linhas da planilha
        with z.open(sheet_filename) as f:
            ws_tree = ET.parse(f)

        rows: list[dict[str, str]] = []
        for r in ws_tree.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            row_data: dict[str, str] = {}
            for c in r.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                ref = c.attrib.get("r", "")
                t = c.attrib.get("t", "")
                val_el = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                val = val_el.text if val_el is not None else ""
                if t == "s" and val.isdigit():
                    val = strings[int(val)]
                col_letter = "".join([ch for ch in ref if ch.isalpha()])
                row_data[col_letter] = val.strip()
            if row_data and row_data.get("A") and row_data.get("A") != "Nome":
                rows.append(row_data)

    return rows


def gerar_operacoes(rows: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """Gera operações de atualização de dvr_unidades e cftv_canais."""
    dvrs_ops: list[dict] = []
    canais_ops: list[dict] = []

    for r in rows:
        nome = r.get("A", "")
        tipo = r.get("B", "")
        modelo = r.get("C", "")
        serie = r.get("G", "")
        ip = r.get("J", "")
        # Correção de typo na planilha: Shopping_Novo está em 10.41.102.172
        if ip == "10.41.102.72":
            ip = "10.41.102.172"

        canais_raw = r.get("F", "")
        total_canais = int(canais_raw.split("/")[0]) if canais_raw and canais_raw.split("/")[0].isdigit() else None
        eh_nuvem = nome in GRAVADORES_NUVEM

        dvrs_ops.append(
            {
                "nome": nome,
                "tipo": tipo,
                "modelo": modelo,
                "serie": serie,
                "ip": ip,
                "total_canais": total_canais,
                "eh_nuvem": eh_nuvem,
                "unidade_id": GRAVADORES_NUVEM.get(nome),
            }
        )

        if ip and ip != "?":
            falhas_str = r.get("K", "")
            falhas = [int(n) for n in re.findall(r"\b\d+\b", falhas_str)] if falhas_str and falhas_str != "?" else []
            for ch in falhas:
                canais_ops.append(
                    {
                        "ip_gravador": ip,
                        "canal": ch,
                        "situacao": "ativo",
                        "imagem": "sem",
                        "origem": "auditoria-dvrs-xlsx",
                        "dvr_nome": nome,
                    }
                )

    return dvrs_ops, canais_ops


def aplicar_no_banco(conn: sqlite3.Connection, dvrs_ops: list[dict], canais_ops: list[dict]) -> tuple[int, int]:
    """Aplica as operações diretamente via conexão SQLite."""
    c = conn.cursor()

    # Mapeamento existente de dvr_unidades
    db_dvrs = c.execute("SELECT id, dvr, unidade_id, apelido FROM dvr_unidades").fetchall()
    vinculos = {r[1]: {"id": r[0], "dvr": r[1], "unidade_id": r[2], "apelido": r[3]} for r in db_dvrs}

    total_dvrs_atualizados = 0
    agora_iso = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")

    for d in dvrs_ops:
        nome = d["nome"]
        modelo = d["modelo"]
        serie = d["serie"]
        canais = d["total_canais"]
        eh_nuvem = d["eh_nuvem"]
        unidade_nuvem = d["unidade_id"]

        dvr_key = None
        for k, v in vinculos.items():
            if norm(nome) in (norm(k), norm(v["apelido"] or "")):
                dvr_key = k
                break

        if eh_nuvem:
            if dvr_key:
                c.execute(
                    "UPDATE dvr_unidades SET modelo=?, serie=?, canais=?, acesso='nuvem', unidade_id=? WHERE dvr=?",
                    (modelo, serie, canais, unidade_nuvem, dvr_key),
                )
            else:
                c.execute(
                    "INSERT INTO dvr_unidades (dvr, unidade_id, apelido, duplicado_de, canais, acesso, modelo, serie) "
                    "VALUES (?, ?, '', '', ?, 'nuvem', ?, ?)",
                    (nome, unidade_nuvem, canais, modelo, serie),
                )
            total_dvrs_atualizados += 1
        elif dvr_key:
            c.execute(
                "UPDATE dvr_unidades SET modelo=?, serie=?, canais=? WHERE dvr=?",
                (modelo, serie, canais, dvr_key),
            )
            total_dvrs_atualizados += 1
        else:
            # Gravador local novo
            unidade_id = (
                3
                if "sede" in nome.lower()
                else (1 if "shopping" in nome.lower() else (4 if "triunfo" in nome.lower() else None))
            )
            c.execute(
                "INSERT INTO dvr_unidades (dvr, unidade_id, apelido, duplicado_de, canais, acesso, modelo, serie) "
                "VALUES (?, ?, '', '', ?, '', ?, ?)",
                (nome, unidade_id, canais, modelo, serie),
            )
            total_dvrs_atualizados += 1

    # Atualizar canais com falha em cftv_canais
    total_canais_atualizados = 0
    for ch in canais_ops:
        ip = ch["ip_gravador"]
        canal = ch["canal"]
        existente = c.execute("SELECT id FROM cftv_canais WHERE ip_gravador=? AND canal=?", (ip, canal)).fetchone()
        if existente:
            c.execute(
                "UPDATE cftv_canais SET situacao='ativo', imagem='sem', origem='auditoria-dvrs-xlsx', conferido_em=? WHERE id=?",
                (agora_iso, existente[0]),
            )
        else:
            c.execute(
                "INSERT INTO cftv_canais (ip_gravador, canal, situacao, analogica, ip_camera, equipamento, imagem, origem, conferido_em) "
                "VALUES (?, ?, 'ativo', 0, '', '', 'sem', 'auditoria-dvrs-xlsx', ?)",
                (ip, canal, agora_iso),
            )
        total_canais_atualizados += 1

    conn.commit()
    return total_dvrs_atualizados, total_canais_atualizados


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza auditoria de DVRs da planilha para o banco de dados")
    parser.add_argument("--planilha", default=PADRAO_PLANILHA, help="Caminho do arquivo Dvrs.xlsx")
    parser.add_argument("--db", default="", help="Caminho do arquivo SQLite local (ex.: data/app.db)")
    parser.add_argument(
        "--container", default="", help="Nome do container Docker para aplicar em produção (ex.: itgov-app)"
    )
    args = parser.parse_args()

    print(f"Lendo aba '{ABA_ALVO}' de {args.planilha}...")
    rows = extrair_dados_planilha(args.planilha)
    print(f"-> {len(rows)} gravadores encontrados na planilha.")

    dvrs_ops, canais_ops = gerar_operacoes(rows)
    print(f"-> {len(canais_ops)} canais com falha identificados para registro.")

    if args.db:
        print(f"Aplicando no banco local: {args.db}")
        conn = sqlite3.connect(args.db, timeout=60.0)
        conn.execute("PRAGMA busy_timeout = 60000;")
        d_count, c_count = aplicar_no_banco(conn, dvrs_ops, canais_ops)
        conn.close()
        print(f"OK: {d_count} gravadores sincronizados e {c_count} canais com falha registrados.")
        return 0

    if args.container:
        print(f"Aplicando no container de produção '{args.container}'...")
        # Gerar script Python para execução dentro do container
        import json

        payload = json.dumps({"dvrs": dvrs_ops, "canais": canais_ops})
        script_code = f"""
import json, sqlite3
from datetime import datetime, timezone

data = json.loads({payload!r})
conn = sqlite3.connect('/app/data/app.db', timeout=60.0)
c = conn.cursor()
c.execute('PRAGMA busy_timeout = 60000;')

db_dvrs = c.execute('SELECT id, dvr, unidade_id, apelido FROM dvr_unidades').fetchall()
vinculos = {{r[1]: {{'id': r[0], 'dvr': r[1], 'unidade_id': r[2], 'apelido': r[3]}} for r in db_dvrs}}

def norm(s):
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())

d_count = 0
agora = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')

for d in data['dvrs']:
    nome = d['nome']
    modelo = d['modelo']
    serie = d['serie']
    canais = d['total_canais']
    eh_nuvem = d['eh_nuvem']
    unidade_nuvem = d['unidade_id']

    dvr_key = None
    for k, v in vinculos.items():
        if norm(nome) in (norm(k), norm(v['apelido'] or '')):
            dvr_key = k
            break

    if eh_nuvem:
        if dvr_key:
            c.execute('UPDATE dvr_unidades SET modelo=?, serie=?, canais=?, acesso=\"nuvem\", unidade_id=? WHERE dvr=?', (modelo, serie, canais, unidade_nuvem, dvr_key))
        else:
            c.execute('INSERT INTO dvr_unidades (dvr, unidade_id, apelido, duplicado_de, canais, acesso, modelo, serie) VALUES (?, ?, \"\", \"\", ?, \"nuvem\", ?, ?)', (nome, unidade_nuvem, canais, modelo, serie))
        d_count += 1
    elif dvr_key:
        c.execute('UPDATE dvr_unidades SET modelo=?, serie=?, canais=? WHERE dvr=?', (modelo, serie, canais, dvr_key))
        d_count += 1
    else:
        unidade_id = 3 if 'sede' in nome.lower() else (1 if 'shopping' in nome.lower() else (4 if 'triunfo' in nome.lower() else None))
        c.execute('INSERT INTO dvr_unidades (dvr, unidade_id, apelido, duplicado_de, canais, acesso, modelo, serie) VALUES (?, ?, \"\", \"\", ?, \"\", ?, ?)', (nome, unidade_id, canais, modelo, serie))
        d_count += 1

c_count = 0
for ch in data['canais']:
    ip = ch['ip_gravador']
    canal = ch['canal']
    existente = c.execute('SELECT id FROM cftv_canais WHERE ip_gravador=? AND canal=?', (ip, canal)).fetchone()
    if existente:
        c.execute('UPDATE cftv_canais SET situacao=\"ativo\", imagem=\"sem\", origem=\"auditoria-dvrs-xlsx\", conferido_em=? WHERE id=?', (agora, existente[0]))
    else:
        c.execute('INSERT INTO cftv_canais (ip_gravador, canal, situacao, analogica, ip_camera, equipamento, imagem, origem, conferido_em) VALUES (?, ?, \"ativo\", 0, \"\", \"\", \"sem\", \"auditoria-dvrs-xlsx\", ?)', (ip, canal, agora))
    c_count += 1

conn.commit()
conn.close()
print(f'SUCESSO_PROD: {{d_count}} gravadores sincronizados e {{c_count}} canais com falha registrados.')
"""
        cmd = ["docker", "exec", "-i", args.container, "python3", "-c", script_code]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Erro ao executar no container: {res.stderr}")
            return 1
        print(res.stdout)
        return 0

    # Padrão: aplicar no banco local se existir
    local_db = "data/app.db"
    if os.path.isfile(local_db):
        print(f"Aplicando no banco local: {local_db}")
        conn = sqlite3.connect(local_db)
        d_count, c_count = aplicar_no_banco(conn, dvrs_ops, canais_ops)
        conn.close()
        print(f"OK: {d_count} gravadores sincronizados e {c_count} canais com falha registrados em {local_db}.")
    else:
        print("Nenhum banco especificado (--db ou --container). Operações preparadas com sucesso.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
