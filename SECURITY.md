# 🔒 Política de Segurança

## 📋 Versões Suportadas

| Versão | Suporte de Segurança |
| ------ | -------------------- |
| 1.x.x  | ✅ Ativo             |
| < 1.0  | ❌ Não suportado     |

## 🚨 Reportando Vulnerabilidades

Se você descobrir uma vulnerabilidade de segurança neste projeto:

### ⚠️ NÃO abra uma issue pública!

1. **Envie um email para:** robertoandr@gmail.com
2. **Assunto:** `[SECURITY] IT Governance Dashboard - <breve descrição>`
3. **Inclua:**
   - Descrição da vulnerabilidade
   - Passos para reproduzir
   - Impacto potencial
   - Sugestão de correção (se houver)

### ⏱️ Tempo de Resposta
- **Confirmação:** até 48h
- **Avaliação inicial:** até 7 dias
- **Correção:** depende da severidade
  - 🔴 Crítica: até 7 dias
  - 🟠 Alta: até 30 dias
  - 🟡 Média: até 90 dias

## 🛡️ Boas Práticas para Contribuidores

- ❌ **NUNCA** commite credenciais (`.env`, tokens, senhas)
- ✅ Use sempre `.env.example` como referência
- ✅ Revise dependências antes de adicionar
- ✅ Rode `pip-audit` ou `safety check` regularmente
- ✅ Mantenha Python e libs atualizados
