# Spike — Avaliação de ferramentas de monitoramento de rede (r5)

**Data:** 2026-10-03
**Pedido:** item r5 da conferência V2.0 — avaliar Auvik, Dynatrace, Datadog ou
LogicMonitor, ou uma solução própria no mesmo modelo.
**Recomendação:** solução própria (Zabbix + FortiGate API + este dashboard),
completando a lacuna de topologia física com SNMP/LLDP nos switches.

---

## O que precisamos

| Necessidade | Situação em 03/10/2026 |
|---|---|
| Descobrir dispositivos e avisar quando aparece um novo | Feito: descoberta do Zabbix + DHCP/ARP dos FortiGates, selo "novo na rede" (página Rede) |
| Classificar e nomear | Feito: regras + IA nos casos difíceis, padrão `SIGLA-TIPO-NNN` |
| Separar por unidade | Feito: faixas de IP por unidade e redes dos FortiGates |
| Latência e estabilidade | Feito: ping do Zabbix por unidade (24 h) e SD-WAN dos FortiGates |
| Links WAN e túneis entre unidades | Feito: FortiGate API (operadoras, SD-WAN, túneis) |
| Topologia lógica | Feito: operadora → FortiGate → VLAN → dispositivos (página Ativos de rede) |
| Topologia física (switch/porta) | **Falta**: exige SNMP + LLDP nos switches |
| Tráfego por aplicação/usuário (NetFlow/sFlow) | **Falta**: não há coletor de fluxo |
| Alertas virando chamado | Feito: ação do Zabbix → Zendesk (WhatsApp aguarda número) |

## As quatro ferramentas

Descrição do produto e do modelo de cobrança, sem preços: os valores mudam e
dependem de cotação com o fornecedor ou revenda.

| | Auvik | LogicMonitor | Datadog | Dynatrace |
|---|---|---|---|---|
| Foco | Rede (switches, roteadores, firewalls, Wi-Fi) | Infraestrutura híbrida (rede, servidores, nuvem) | Observabilidade em nuvem (apps, infra, logs); rede é um módulo | Observabilidade de aplicações (APM); rede é periférica |
| Coleta | Coletor local, SNMP, LLDP/CDP, APIs de fabricante | Coletor local, SNMP, APIs, WMI | Agente por host; NDM (SNMP) por dispositivo | OneAgent por host; extensões para SNMP |
| Topologia física automática | Sim, ponto forte | Sim | Parcial (NDM) | Não é o foco |
| Fluxo (NetFlow/sFlow) | Sim | Sim | Sim (NPM/NetFlow) | Limitado |
| Cobrança | Por dispositivo de rede gerenciado | Por recurso monitorado | Por host/dispositivo e por volume (logs, métricas) | Por consumo (host-hora, GiB) |
| Hospedagem | SaaS | SaaS | SaaS | SaaS ou gerenciado |
| Encaixe aqui | Bom para topologia física; duplica o que o Zabbix já faz | Bom, mas duplica Zabbix + dashboard | Caro para o perfil (muitos dispositivos de borda) | Sobra para o caso de uso (somos rede/CFTV, não apps) |

Pontos comuns às quatro: são SaaS (dados de rede saem da empresa — avaliar
LGPD/contrato), cobram por dispositivo ou consumo (o parque tem ~1.300 IPs
vistos, 277 câmeras e 3 FortiGates) e exigiriam integrar de novo com Zendesk e
com o inventário daqui.

## Por que a solução própria

1. **Os dados já estão aqui.** O Zabbix monitora 279 hosts com ping e roda a
   descoberta; os FortiGates entregam DHCP, ARP, SD-WAN e as redes por API; o
   dashboard junta tudo por unidade e já abre chamado no Zendesk.
2. **Custo de licença zero** e nenhum dado de rede saindo para SaaS.
3. **Encaixe com o resto do dashboard**: inventário de ativos, aprovação do
   super admin, CFTV, unidades — uma ferramenta externa ficaria ao lado, não
   dentro.
4. **O que falta é pontual** e tem caminho dentro do próprio stack.

## O que fazer para cobrir o que falta

| Lacuna | Caminho | Depende de |
|---|---|---|
| Topologia física | Habilitar SNMP (v2c leitura ou v3) e LLDP nos switches; template SNMP do Zabbix coleta vizinhos LLDP (`lldpRemTable`); o dashboard desenha switch → porta → dispositivo | Time de infra (configurar switches) |
| Switches gerenciados pelo FortiGate (FortiSwitch/FortiLink) | API do FortiGate (`switch-controller`) já dá portas e vizinhos sem SNMP | Confirmar se os switches são FortiSwitch |
| Tráfego por aplicação | FortiGate já classifica aplicações; ler pela API (FortiView) ou ativar NetFlow para um coletor | Decidir retenção |
| Triunfo | Token de API do FortiGate | Gerar token (pendente) |

## Quando reavaliar

- Se a topologia física for obrigatória e os switches não puderem receber
  SNMP/LLDP: Auvik é a opção mais direta (foco em rede, coletor local).
- Se o time de TI não puder manter o stack próprio (Zabbix + dashboard).
- Se surgirem aplicações próprias críticas que precisem de APM — aí Datadog
  ou Dynatrace entram pelo lado de aplicações, não de rede.
