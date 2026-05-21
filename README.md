# 🏛️ IT Governance Dashboard

> Dashboard completo para governança de TI com métricas baseadas em **COBIT**, **ITIL v4** e **ISO/IEC 27001**.

![Status](https://img.shields.io/badge/status-active-success)
![License](https://img.shields.io/badge/license-MIT-blue)
![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB)

---

## 📸 Preview

> _Adicione um screenshot aqui após o deploy_

---

## 🎯 Sobre o Projeto

Plataforma de monitoramento e governança de TI que centraliza indicadores estratégicos, táticos e operacionais, permitindo a gestão executiva visualizar em tempo real:

- **Conformidade** com frameworks de governança
- **Performance** de serviços de TI (SLA/OLA)
- **Riscos** e incidentes de segurança
- **Investimentos** e ROI de iniciativas tecnológicas

---

## 🏛️ Frameworks Implementados

| Framework | Domínios Cobertos |
|-----------|-------------------|
| **COBIT 2019** | EDM, APO, BAI, DSS, MEA |
| **ITIL v4** | Service Strategy, Design, Operation |
| **ISO 27001** | Controles A.5 a A.18 |
| **NIST CSF** | Identify, Protect, Detect, Respond, Recover |

---

## 📊 KPIs Disponíveis

- ✅ Disponibilidade de Serviços (Uptime %)
- ✅ MTTR (Mean Time to Repair)
- ✅ MTBF (Mean Time Between Failures)
- ✅ Taxa de Cumprimento de SLA
- ✅ Incidentes por Severidade
- ✅ Vulnerabilidades Críticas Abertas
- ✅ Aderência a Políticas
- ✅ Custos de TI vs. Orçamento

---

## 🛠️ Stack Tecnológica

- **Frontend:** React + TypeScript + Vite
- **UI:** TailwindCSS + Shadcn/UI
- **Gráficos:** Recharts / Chart.js
- **Estado:** Zustand / Redux Toolkit
- **Ícones:** Lucide React

---

## 🚀 Como Executar Localmente

### Pré-requisitos
- Node.js 18+
- npm ou pnpm

### Passos

```bash
# 1. Clone o repositório
git clone https://github.com/robertoandr/it-governance-dashboard.git
cd it-governance-dashboard

# 2. Instale as dependências
npm install

# 3. Configure variáveis de ambiente
cp .env.example .env

# 4. Inicie o servidor de desenvolvimento
npm run dev
