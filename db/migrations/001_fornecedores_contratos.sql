-- Migration: 001_fornecedores_contratos
-- Description: Initial schema for the hero module — Fornecedores & Contratos
-- Requires: PostgreSQL 16+, TimescaleDB 2.x extension enabled
-- Run with: psql $DATABASE_URL -f 001_fornecedores_contratos.sql

-- ---------------------------------------------------------------------------
-- Fornecedores
-- ---------------------------------------------------------------------------
CREATE TABLE fornecedores (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  nome             TEXT        NOT NULL,
  cnpj             TEXT        UNIQUE,
  categoria        TEXT        NOT NULL,       -- ex: Cloud, Software, Telecom
  status           TEXT        NOT NULL DEFAULT 'ativo',  -- ativo|inativo|suspenso
  contato_nome     TEXT,
  contato_email    TEXT,
  contato_telefone TEXT,
  suporte_24x7     BOOLEAN     DEFAULT false,
  observacoes      TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at       TIMESTAMPTZ           -- soft delete
);

-- ---------------------------------------------------------------------------
-- Contratos
-- ---------------------------------------------------------------------------
CREATE TABLE contratos (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  fornecedor_id     UUID        NOT NULL REFERENCES fornecedores(id) ON DELETE RESTRICT,
  numero            TEXT        NOT NULL,
  descricao         TEXT,
  data_inicio       DATE        NOT NULL,
  data_fim          DATE        NOT NULL,
  valor_mensal      NUMERIC(14,2),
  valor_anual       NUMERIC(14,2) GENERATED ALWAYS AS (valor_mensal * 12) STORED,
  moeda             TEXT        DEFAULT 'BRL',
  sla_prometido_pct NUMERIC(5,2),             -- ex: 99.90
  renovacao_auto    BOOLEAN     DEFAULT false,
  anexo_path        TEXT,                      -- caminho do PDF no storage
  status            TEXT        NOT NULL DEFAULT 'ativo',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Contratos Eventos (TimescaleDB hypertable)
-- ---------------------------------------------------------------------------
CREATE TABLE contratos_eventos (
  time          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  contrato_id   UUID        NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
  tipo          TEXT        NOT NULL,  -- alerta_vencimento|renovacao|sla_breach
  payload       JSONB,
  PRIMARY KEY (time, contrato_id)
);

SELECT create_hypertable('contratos_eventos', 'time', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_contratos_data_fim
  ON contratos(data_fim)
  WHERE deleted_at IS NULL;

CREATE INDEX idx_contratos_fornecedor
  ON contratos(fornecedor_id);

CREATE INDEX idx_fornecedores_status
  ON fornecedores(status)
  WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- updated_at trigger function (shared by both tables)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
  RETURNS TRIGGER
  LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_fornecedores_updated
  BEFORE UPDATE ON fornecedores
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_contratos_updated
  BEFORE UPDATE ON contratos
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
