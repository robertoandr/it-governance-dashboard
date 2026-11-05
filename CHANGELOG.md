# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Rede — Template da Ubiquiti pelo DHCP e UniFi Dream Machine:** o vendor class do DHCP (`vci`, que a Ubiquiti manda como `ubnt`) passa a contar na escolha do template. Os 41 APs e antenas Ubiquiti da página Rede apareciam como access point, mas a sugestão caía no genérico porque o fabricante só vinha no DHCP; agora recebem **Ubiquiti AirOS by SNMP** (confiança alta quando o SNMP responde). A UniFi Dream Machine (ex.: Pro Max, 172.29.1.186) recebe **Linux by SNMP** no grupo Links WAN, porque a Ubiquiti não publica template de UDM para o Zabbix.

### Added
- **Cyber — Aba Programas:** nova página `/gov/cyber/programas` com os programas instalados nas máquinas (máquina, programa, versão, quando), os instalados em mais máquinas e os programas de acesso remoto (TeamViewer, AnyDesk, RustDesk…) em destaque, com busca e filtro "só acesso remoto". Fonte: alertas `MiMonitoringSoftwareInstalled` do monitoramento do Acronis (`/api/alert_manager/v1/alerts`), sem repetir máquina+programa+versão; cache de 1 h. Mostra o que foi **instalado** desde que o aviso está ligado, não o inventário completo: a API pública não traz o inventário de software nem os dispositivos descobertos. Item 3.4 do plano de implantação do Acronis.

### Added
- **Cyber — Aba Permissões e incidentes por categoria:** nova página `/gov/cyber/permissoes` com o que os planos de proteção do Acronis liberam (processos e hashes confiáveis, pastas fora da verificação, conexões) e bloqueiam, com a origem de cada item (quarentena, incidente ou manual) e filtros. Destaca as permissões arriscadas (binário do sistema ou interpretador, pasta inteira de usuário) para revisão no console. O NOC de Segurança ganha o bloco **Incidentes abertos por categoria**, no lugar da tática MITRE, que a API pública não tem; a quarentena também não tem API. Fonte: configurações dos planos (`policy_management`) e incidentes abertos do EDR; cache de 1 h. Item 3.3 do plano de implantação do Acronis.

### Fixed
- **CFTV — Coletor de modelos e gravador sem fabricante:** gravador `dvr-*` cadastrado sem a tag `vendor` passa a ser consultado pela API Intelbras, e não pelo ONVIF. Pelo ONVIF ele recusava a mesma senha que a leitura dos gravadores do dashboard usa (falso "senha recusada" em Corredor Técnico, Triunfo_2_Folha2 e Triunfo_4_Extrato). A versão fica em 2.12.1, porque o merge do #377 tinha deixado a 2.12.0.

### Fixed
- **CFTV — Segunda senha da Loja Centro e faciais:** nova chave `loja2` na tag `cftv_cred` (`CFTV_CAM_LOJA_USER` + `CFTV_CAM_LOJA_PASS2`) para as câmeras Hikvision da Loja que recusam a primeira senha (D2–D13, D18), no coletor de modelos e na padronização de senha. A chave `facial` passa a ler `FACIAL_USER`/`FACIAL_PASS`, as variáveis que existem no `.env` (as `FACIAL_CENTRO_*` nunca foram criadas, então os faciais nunca eram consultados).

### Added
- **Licenças — Acronis Cyber Protect:** `/gov/licenses` ganha o bloco do Acronis com uso × contratado de cada item (estações de trabalho, Advanced Management, Advanced Security, XDR, DLP…), licenças sobrando e barra de uso. Em atenção a partir de 85% e crítico a partir de 95% ou acima da cota, como no M365, com aviso no topo do bloco. Também lista as máquinas que o Acronis já acusa sem licença suficiente (o trigger de cota). Fonte: API de contas (`/api/2/tenants/{tenant}/offering_items` e `/usages`), cache de 1 h. Em 05/11: estações 279/300 (93%) e Advanced Management 282/300 (94%). Item 3.3 do plano de implantação do Acronis (parte da cota).
- **Cyber — Aba Vulnerabilidades:** nova página `/gov/cyber/vulnerabilidades` com o resultado da última varredura do Acronis em cada máquina: programas com vulnerabilidade (e quantas cada um tem), patches pendentes, data da varredura, os programas mais vulneráveis e os patches pendentes em mais máquinas, com busca por máquina, programa ou patch. Fonte: atividades `WinScan`/`TpScan` da API pública de tarefas (`/api/task_manager/v2/activities`), mais recente de cada tipo por máquina, nome pelo agente; cache de 1 h. Só leitura: a API pública não tem número de CVE nem como aprovar patch, então aprovação continua no console. Em 05/11: 104 de 286 máquinas, 2.037 vulnerabilidades, 443 patches. As outras entram quando a varredura for para o plano de proteção (item 1.1). Item 3.2 do plano de implantação do Acronis.
- **Rede — Computador com Acronis reconhecido na descoberta:** o inventário do Acronis passa a guardar os MACs, os IPs (todos os adaptadores) e a hora do último login de cada máquina. Na página `/gov/rede`, o ativo descoberto com o mesmo MAC (ou, sem MAC conhecido, o mesmo IP) ganha a marca **Acronis** com o nome do computador e o último usuário logado, e modelo e nº de série vêm do Acronis quando a descoberta não tinha. A busca acha pelo nome do computador e pelo usuário. MAC ou IP repetido em mais de um computador (dock, VPN) não marca nada, e câmera lida pelo gravador nunca vira computador. Em 05/11: 241 ativos reconhecidos (223 computadores). Item 3.1 do plano de implantação do Acronis.

### Changed
- **Cyber — Triggers do Acronis sem backup:** backup de máquina não é contratado no plano da Gadens, então a categoria "Falha de backup" saiu do agente `acronis_triggers` e o `infra-setup/15_acronis_triggers.py` apaga o item `acronis.alerta[backup]` (e o trigger dele) no Zabbix. Ficam 5 categorias.

### Added
- **Cyber — Triggers do Acronis no lugar do e-mail:** novo agente (`acronis_triggers`, um worker só) lê os alertas ativos do Acronis a cada 10 min e separa em 6 categorias: EDR crítico não mitigado, malware ou ameaça, falha de backup, proteção com falha (serviço parado, conflito de antimalware, isolamento que falhou…), sem licença (cota) e agente offline. Cada categoria manda "N máquinas: A, B, C" ao item trapper `acronis.alerta[<categoria>]` do host `acronis-alertas-dashboard`, cujo trigger fica em problema enquanto houver máquina. Os triggers levam a tag `alerta=so-dashboard`: ficam no Zabbix e no novo bloco **Triggers do Acronis** de `/gov/cyber/noc`, sem chamado e sem WhatsApp. Falha ao ler o Acronis não zera os triggers. Zabbix: `infra-setup/15_acronis_triggers.py` (cria host, itens e triggers e tira `alerta=so-dashboard` da ação de chamado). Item 0.1 do plano de implantação do Acronis (decisão de 05/11: sem e-mail).
- **Rede — Tipo novo direto no cadastro do ativo descoberto:** o campo Tipo de `/gov/rede/cadastrar` ganhou a opção **+ Novo tipo…**, que abre ali mesmo nome, sigla e palavras-chave. Ao criar, volta ao cadastro com o tipo novo já escolhido e mantém o que já tinha sido digitado (nome, unidade, criticidade, ambiente, descrição). Usa a mesma classificação de `Rede → Classificações de dispositivo` (e a mesma fila de aprovação); a volta só aceita IP válido.
- **CFTV — Credenciais da Loja Centro e dos faciais:** novas chaves da tag `cftv_cred`: `loja` (`CFTV_CAM_LOJA_*`, câmeras Hikvision D1–D18) e `facial` (`FACIAL_CENTRO_*`, leitores faciais da Sede Centro), no coletor de modelos e na padronização de senha. `cftv_cred=nao_precisa` marca equipamento sem senha (central de alarme, app Intelbras): o coletor não o consulta e o painel o conta como ok, sem botão de padronizar.
- **CFTV — Um host por câmera no Zabbix, sem câmera de fora:** o agente de hora em hora (`cftv_ip_sync`) agora também (1) **desativa a cópia** quando a câmera fora tem IP novo já monitorado online em outro host (a mesma câmera gravada por dois gravadores); (2) com **vários hosts ativos no mesmo IP**, deixa um (o que o gravador confirma no canal, depois o online, depois o mais antigo) e desativa os outros, para a câmera que cai não avisar duas ou três vezes; (3) **cria o host de ping** (grupo `CFTV/Cameras`, template ICMP Ping, tags `dvr`/`canal`/modelo, `origem=dashboard`) para câmera IP com vídeo no gravador e sem host nenhum no Zabbix (no máximo 20 por rodada; canal marcado como outro equipamento fica de fora). Desativar é reversível (`status=1`); tudo fica no histórico do agente em `/gov/cftv?ver=gravadores`.
- **CFTV — IP de câmera corrigido sozinho no Zabbix:** novo agente (`cftv_ip_sync`, um worker só) que de hora em hora lê todos os gravadores e, para cada câmera **fora no Zabbix** cujo gravador recebe vídeo dela em **outro IP** (API Intelbras com leitura de menos de 2 h, ou SNMP `Connected`), troca no Zabbix as interfaces do host que estavam no IP antigo. Recusa IP vazio/de fábrica, fora da rede interna, do próprio gravador ou já usado por outro host CFTV; no máximo 20 trocas por rodada. Cada troca fica registrada e aparece em `/gov/cftv?ver=gravadores`. Acaba com o "câmera fora" falso no WhatsApp quando a câmera muda de IP.

### Fixed
- **CFTV — Host desativado aparecia no painel:** a busca dos hosts CFTV no Zabbix passa a trazer só os monitorados (`monitored_hosts`).
- **CFTV — Falso "sem imagem agora" em câmera que grava:** foto sem resposta num canal que está gravando normalmente agora aparece como "foto indisponível" (cinza) e fica fora do aviso e do item do Zabbix. O vídeo chega ao gravador; quem não entrega a foto é ele (NVD 1432 da Sede com câmera ONVIF responde HTTP 500 no `snapshot.cgi`). Somem ~76 falsos nos DVR-1/2/3 da Sede, Shopping_1 ch20 e Praça Cancelas. Tela chapada continua problema mesmo gravando.
- **CFTV — "Sem vídeo" com canais inexistentes no firmware antigo:** HDCVI 3116 G2 (Shopping_Superior, firmware 3.210 de 2017) lista todos os canais com máscara de bits (`channels[7]=128`), lida como canais 129, 257, 513... Agora o formato é reconhecido e vira os canais certos.
- **CFTV — Acessos de IP que não é mais gravador:** registros de IP antigo (ex.: 10.41.100.116, 10.41.102.72) ou de câmera (10.41.100.233) ficavam para sempre. A rodada horária apaga os que não são gravador no Zabbix e não são lidos há mais de 7 dias.
- **CFTV — Leitura antiga parecendo atual:** quando o gravador passava a recusar a senha, o card seguia mostrando canais e câmeras da última leitura boa sem avisar. Agora `gravador_acessos.lido_ok_em` guarda a data da última leitura que deu certo e o card diz "Canais e câmeras acima são da última leitura boa, de <data>".
- **CFTV — Canais livres inflados:** gravador sem leitura, conferência, SNMP nem câmera com canal no Zabbix contava todos os canais como livres (ex.: NVR Hikvision e Sede_4_3_2_Loja, 32 cada). Agora mostra "livres: sem leitura" e fica fora da soma.
- **CFTV — Senha própria recusada travava o gravador:** com `CFTV_DVR_<NOME>_*` configurada, só ela era tentada; recusada, o gravador ficava sem leitura para sempre (Triunfo desde 23/10). Agora a própria vem primeiro e, se recusada, as gerais entram no rodízio (uma por vez, respeitando a espera anti-bloqueio); nos gravadores do Triunfo entram também `CFTV_CAM_TRIUNFO_PASS` e `CFTV_CAM_TRIUNFO_PASS2`.

### Changed
- **Cyber — Isolar máquina só pelo Acronis:** a quarentena do IP no FortiGate foi retirada (os tokens dos FortiGates são só de leitura e a decisão é não isolar por lá). Sem incidente aberto no Acronis que permita isolar, o pedido é recusado sem gravar nada, com a orientação de isolar pelo console. Sai `FORTIGATE_<UNIDADE>_TOKEN_ESCRITA` do `.env.example`.

### Fixed
- **Cyber — Agente SIEM com "database is locked":** o app esperava só 5 s pela trava de escrita do SQLite; agora espera até 30 s. O agente de incidentes não segura mais a trava enquanto fala com o Acronis e a IA (grava antes das chamadas HTTP).
- **Cyber — Ruído de IPsec no SIEM:** "Progress IPsec phase 1" (nível `error`, quase metade dos eventos da Sede) deixa de ser guardado, e os "IPsec phase 1 error" viram uma linha por FortiGate e origem a cada coleta, com o total em `repeticoes` (nova coluna, criada sozinha na subida). Contagens e a regra "Túnel IPsec falhando" somam as repetições. O banco parava de crescer ~60 mil linhas/dia.

### Added
- **Cyber — Isolar máquina (qualquer uma):** na página Agente de incidentes (`/gov/cyber/agentes`), novo campo **Isolar máquina** (nome ou IP + motivo). Se a máquina tem incidente aberto no EDR do Acronis que oferece `WORKLOAD_ISOLATE`, isola pelo agente (a API do Acronis só isola a partir de incidente); senão, coloca o IP em **quarentena no FortiGate** cuja rede contém o IP (`monitor/user/banned/add_users`, sem prazo — corta internet e outras VLANs; a mesma VLAN continua alcançando). Isolar e **Liberar rede** passam pela fila de aprovação do super admin; lista de isoladas + histórico (tabela `cyber_isolamentos`). Proteções: gateways dos FortiGates, IPs públicos e `ISOLAR_IPS_PROTEGIDOS` nunca vão para quarentena; a mesma máquina não é isolada duas vezes. A quarentena usa `FORTIGATE_<UNIDADE>_TOKEN_ESCRITA` (o token de leitura é recusado com 403 e a tela diz o que configurar).
- **Cyber — Refazer isolamento em timeout:** quando o isolamento no Acronis terminava em timeout (`TIMED_OUT`/`TIMEOUT`), a máquina permanecia na lista de isoladas sem opção de tentar de novo. Agora o painel exibe o botão **Refazer isolamento** para máquinas com isolamento incerto, disparando novamente a ação `WORKLOAD_ISOLATE` via fila de aprovação do super admin.
- **Cyber — Páginas separadas:** as abas do módulo Cyber (Incidentes, NOC de Segurança, Investigação ao vivo, Agente SIEM, Agente de incidentes, Phishing) viraram **páginas com URL própria** (`/gov/cyber/incidentes`, `/gov/cyber/noc`, `/gov/cyber/live`, `/gov/cyber/siem`, `/gov/cyber/agentes`, `/gov/cyber/phishing`), bookmarkáveis; `/gov/cyber` redireciona (301) para Incidentes. A navegação entre elas passou de abas client-side para links; o conteúdo de cada página é renderizado pelo servidor.
- **Cyber — Pentest (Strix):** nova página `/gov/cyber/pentest` para rodar varreduras de pentest com o [Strix](https://github.com/usestrix/strix) contra os **ativos próprios da allowlist** (`PENTEST_ALVOS`, nunca um alvo arbitrário — o formulário só manda a chave, a URL nunca vem do cliente; validada na rota **e** revalidada pelo worker). Lançar é restrito a admin e **passa pela fila de aprovação do super admin**. A execução fica num **container dedicado `itgov-strix`** (perfil `pentest`, o único com acesso ao socket do Docker; o app só enfileira no banco). Uma varredura por vez, com timeout; o resultado (status, contagem por severidade, relatório) aparece na própria página via polling. O container não sobe num `up` normal — precisa de janela de manutenção.

### Changed
- **Frontend — Redesign Modelo B (Linear Aesthetic):** modernização do design da dashboard (`https://noc.grupogadens.com.br/`) com foco em eliminar elementos genéricos de IA (cards volumosos e sombras difusas). Adoção de superfícies carvão suave (`#0A0A0C` e `#121215`), bordas hairline de 1px (`#222228`), tipografia tabular para métricas de telemetria e alinhamento visual com a marca institucional Grupo Gadens (`#C41E24`). Atualização dos templates `tema_gadens.html`, `base.html`, `topbar.html`, `sidebar.html`, `overview.html`, `cftv_monitoring.html` e `cyber.html`.
- **Cyber:** o item "Cyber" do menu lateral agora aponta para `/gov/cyber` (que abre Incidentes); sem novos itens de topo — a navegação das páginas fica dentro do módulo.

### Fixed (CFTV)
- **CFTV — Reteste aceito não tirava o equipamento da lista:** o **já resetei — testar** confirmava a senha padrão ("credencial liberada"), mas a troca da tag no Zabbix falhava (`unexpected parameter "automatic"`): o `host.get` com `selectTags: "extend"` devolve o campo `automatic`, que o `host.update` recusa. Agora só `tag`/`value` são lidos e regravados — vale para o reteste (aceito ou recusado) e para o **Padronizar senha**.
- **CFTV — Credencial liberada sai da lista na hora:** depois de **já resetei — testar** com sucesso ("senha padrão aceita — credencial liberada"), o equipamento continuava na lista de credenciais recusadas por até 2 min — o `/gov/cftv` lia de um cache que não era descartado, e cada um dos 4 workers do gunicorn tinha o seu. Agora a troca de tag no Zabbix invalida o cache de **todos** os workers (arquivo-marca em disco), e o equipamento some da lista já no redirect. Vale também para o **Padronizar senha** e para o reteste recusado (data da recusa renovada).

### Added (Zabbix)
- **Zabbix → GDesk:** os alertas passam a abrir chamado **também no GDesk** (`POST /api/public/tickets`), no mesmo evento que já abre no Zendesk — Zendesk segue intacto. Novo media type webhook "GDesk" (`infra-setup/gdesk/alerta.js`) ligado à ação 12, com script idempotente `infra-setup/14_create_gdesk_alert.py`. Abre chamado só em problema novo (recovery/ack ignorados — a API pública do GDesk hoje só cria), dedup por tag do evento (`gdesk_ticket`), severidade→prioridade (desastre→urgente, alta→alta, média/aviso→normal, info→baixa) e fila/URL/chave em macros (`{$GDESK.URL}`, `{$GDESK.QUEUE_ID}`, `{$GDESK.APIKEY}` secreta)

### Added (CFTV)
- **CFTV — Credencial recusada um a um:** a faixa "N equipamentos com credencial recusada" do `/gov/cftv` agora abre a **lista de cada câmera/gravador** (IP, tipo, gravador, credencial tentada, data da recusa). Para o admin, cada linha tem **abrir console ↗** (interface web do equipamento, para o reset) e **já resetei — testar**, que faz **uma única** tentativa de login com a senha padrão: se aceitar, marca `cftv_cred=padrao` e tira a recusa (o coletor volta a ler o equipamento); se recusar, renova a data da recusa. Só leitura no equipamento, por isso sem fila de aprovação.
- **CFTV — Credencial:** o `/gov/cftv` agora mostra o **status de credencial** de cada câmera/gravador (badge "credencial recusada" / "senha padrão" / "pendente", com total no topo), a partir das tags que o coletor de modelos já grava. Admin ganha o botão **Padronizar senha**, que troca a senha do equipamento para a padrão via API da câmera (Intelbras/Dahua), **só onde já há acesso** e passando pela **fila de aprovação do super admin** (como o desisolar do Cyber). Equipamento que recusa a credencial não tem troca remota (precisa reset físico/console) e aparece como "sem acesso". Nunca "chuta" senha, para não disparar o bloqueio de conta das Intelbras.
- **Cyber — Phishing:** segunda isca **"RH — prêmio surpresa"** (e-mail do RH convidando a se inscrever num sorteio) e **seletor de isca** na aba Phishing, ao lado da isca "senha do M365 expirando". Cada isca tem e-mail, landing e página educativa próprios; a landing do RH **não guarda nada do que é digitado** (`capture_credentials=False`), só registra que a pessoa caiu. Página educativa agora é por isca (`/gov/phishing/educativo/<isca>`)

### Changed
- Rota e item de menu `/gov/ciberseguranca` renomeados para **Cybersegurança** em `/gov/cyberseguranca` (com redirecionamento 301 de `/gov/ciberseguranca` e `/gov/backup` e compatibilidade reversa de permissões)

### Fixed
- **Cyber — Perfil de envio GoPhish falhava ao salvar com display name:** o GoPhish exige estritamente um endereço de e-mail puro em `from_address` (rejeitando com HTTP 500 formatos RFC 5322 como `Nome <email@...>` ou prefixos como `De: ...`). O backend agora sanitiza o remetente com `email.utils.parseaddr` extraindo o e-mail puro, valida a presença de e-mail válido com mensagem clara e o placeholder no formulário foi atualizado para indicar apenas o endereço de e-mail.
- **Cyber — Pentest com horário e dia incorretos:** os timestamps de pentest no SQLite eram gravados em UTC, mas retornados como naive datetimes. Na serialização para o frontend, a ausência de indicador de fuso (`+00:00` / `Z`) fazia o navegador tratar a string como hora local, não convertendo para o fuso de Brasília (`America/Sao_Paulo`). Agora a serialização do modelo garante o offset UTC e a função `dataHora` do frontend assume UTC caso a string não contenha fuso.
- **Cyber — Container Strix falhava com exit 1 ("DOCKER NOT INSTALLED"):** o container `itgov-strix` instalava apenas o pacote Debian `docker.io` (que inclui apenas o daemon `dockerd`), mas não o cliente `/usr/bin/docker`. O cliente `docker-cli` foi adicionado ao `strix-worker/Dockerfile` para permitir que o `strix-agent` orquestre os sandboxes de pentest via socket Docker.
- **Cyber — Pentest (Strix) travado em "aprovado":** o worker do Strix fazia o poll da fila (`cyber_pentest_runs`) no mesmo `app.db` SQLite do app, mas o `create_engine` não tinha `busy_timeout` — qualquer escrita concorrente do app devolvia `database is locked` e o pentest aprovado nunca saía da fila. O engine SQLite do worker agora espera pelo lock (`busy_timeout=30s` + WAL), então a varredura é pega em segundos.
- **Rede — Switch Intelbras caía como câmera:** na descoberta do `/gov/rede`, qualquer equipamento Intelbras com SNMP era classificado como "camera", porque a regra testava o fabricante (Intelbras/Hikvision/Dahua) **antes** de reconhecer equipamento de rede. Os switches Intelbras são OEM H3C e reportam `INTELBRAS Platform Software` / `Comware` na descrição SNMP — agora esses sinais (e `h3c`, `procurve`, `jetstream`) classificam como **switch** antes do teste de câmera. Câmera/DVR Intelbras (VIP, MHDX…) continuam como câmera.
- **nginx:** `https://noc.grupogadens.com.br/` (raiz nua) dava 404 — agora redireciona para `/gov/`, que leva à tela de login quando não há sessão

## [2.2.0] - 2026-10-26

Módulo Cyber ganha agentes de IA e simulação de phishing.

### Added
- **Cyber — Refazer isolamento em timeout:** quando o isolamento no Acronis terminava em timeout (`TIMED_OUT`/`TIMEOUT`), a máquina permanecia na lista de isoladas sem opção de tentar de novo. Agora o painel exibe o botão **Refazer isolamento** para máquinas com isolamento incerto, disparando novamente a ação `WORKLOAD_ISOLATE` via fila de aprovação do super admin.
- **Cyber — Agente SIEM:** analisa os logs dos FortiGate a cada poucos minutos, levanta achados com IA (via OmniRoute) e avisa no WhatsApp os graves, cruzando com o Acronis (#323); item e trigger no Zabbix para o alerta (`infra-setup/13_cyber_siem.py`)
- **Cyber — Agente de incidentes (Acronis EDR):** triagem por regras + IA que propõe encerrar, agir ou investigar; toda ação passa pela fila de aprovação (#331)
- **Cyber — Phishing (GoPhish):** leitura das campanhas com taxa de clique e de reporte (#333) e gestão pelo dashboard — criar perfil de envio, montar e disparar a campanha (disparo pela fila de aprovação), com acesso liberado por usuário na coluna "Phishing" (#334)
- **Menu:** item "Cyber" na barra lateral, no grupo Segurança (#324)

### Fixed
- **Cyber:** rotas do agente e do phishing não expõem texto de exceção ao usuário (CodeQL)
- **Cyber — Agente de incidentes:** não insiste na IA quando ela está lenta e retoma na rodada seguinte (#332); manda 1 incidente por vez para a IA, para não estourar o timeout na combo lenta (#335)

## [2.1.0] - 2026-10-09

Versão final da V2 "Governança de TI 360": revisão página por página de 30/09 e
conferência final de 08–09/10/2026.

### Added
- **Cyber — Refazer isolamento em timeout:** quando o isolamento no Acronis terminava em timeout (`TIMED_OUT`/`TIMEOUT`), a máquina permanecia na lista de isoladas sem opção de tentar de novo. Agora o painel exibe o botão **Refazer isolamento** para máquinas com isolamento incerto, disparando novamente a ação `WORKLOAD_ISOLATE` via fila de aprovação do super admin.
- **Usuários:** super admin que aprova alterações de cadastro e configuração (#251), aviso para quem pediu quando o pedido vai para aprovação e quando é decidido (#278) e permissões por usuário, página a página (#279)
- **Rede:** descoberta pelo Zabbix com novos na rede, nomenclatura e latência por unidade (#253); FortiGates pela API, com SD-WAN, WANs e DHCP/ARP (#254); topologia lógica por unidade e geral (#255)
- **CFTV:** drill-down por sede, renomear e unir gravador duplicado (#249), histórico de quedas (#250), SNMP v2c/v3 dos gravadores no Zabbix (#266) e cadastro de gravador novo
- **Alertas:** trigger vira chamado no Zendesk e aviso no grupo de WhatsApp (#265), em português simples, das 08:00 às 19:00, com resumo da manhã (#276)
- **Painel TV:** modelos v1–v6 com dados ao vivo; v6 "Sala de Controle Bento" liberada por usuário (#269, #273, #275, #277)
- **M365:** painel com dados reais (#241), licenças com custo e alerta de esgotamento (#242, #274), uso dos apps com histórico (#257), compliance em português (#243)
- **Gestão:** plano de melhoria nos pilares (#236), PMO por responsável com trigger de atraso (#238), aba ClickUp em Tarefas (#237), relatórios temáticos e personalizados (#264), Zendesk mês a mês (#248)
- **Unidades:** endereço, CEP, logo e CNPJ (#232, #247); situação de todas as fontes na Visão Geral (#235)
- **Backup:** InfluxDB, segredos, configurações, retenção remota, teste de restauração e aviso ao Zabbix (#268, #271)

### Changed
- Visual institucional Gadens, com marca, paleta e modo claro/escuro (#272)
- Mapa geral da topologia ocupa a largura toda da página
- Nome do sistema passa a ser **Governança de TI 360** (títulos, menu, rodapé, login)
- Versão com fonte única no `pyproject.toml`; `APP__VERSION`/`APP__NAME` não precisam mais estar no `.env`
- Home: "Calculado em" (sempre o horário do acesso) trocado por **Última atualização em**, com a coleta real mais recente do InfluxDB em horário de Brasília, e botão **Atualizar**
- Página `/gov/backup` renomeada para **Cibersegurança** em `/gov/ciberseguranca` (o endereço antigo redireciona com 301)

### Fixed
- Lentidão: falha da origem que devolvia `None` não ficava em cache (M365 e Aplicativos esperavam o Graph a cada página); Compliance passa a atualizar em segundo plano; a página Rede converte as faixas de IP uma vez só
- Alternância de tema claro/escuro não funcionava: a config do Tailwind era definida antes do script e sobrescrita, ficando no modo `media` (tema do SO)
- Score de governança misturava 7 componentes sem coletor (valores fixos: cobertura de KPIs, orçamento, frequência de deploy, vulnerabilidades críticas, patches, MTTR, sucesso de mudanças) na média do pilar; agora ficam fora do cálculo e aparecem como "sem coleta" no detalhe do pilar
- Páginas sem variantes `dark:` (M365, Licenças, Compliance, Infra, Acronis, Zabbix, SLA etc.) ficavam ilegíveis no tema escuro

## [1.1.0] - 2026-06-06

### Added
- **Cyber — Refazer isolamento em timeout:** quando o isolamento no Acronis terminava em timeout (`TIMED_OUT`/`TIMEOUT`), a máquina permanecia na lista de isoladas sem opção de tentar de novo. Agora o painel exibe o botão **Refazer isolamento** para máquinas com isolamento incerto, disparando novamente a ação `WORKLOAD_ISOLATE` via fila de aprovação do super admin.
- Kubernetes liveness and readiness probes (`GET /api/health`, `GET /api/health/ready`) with parallel async dependency checks and per-check timeouts (#134)
- `k8s/deployment.yaml` with `startupProbe` + `livenessProbe` + `readinessProbe` configured for Flask/Gunicorn boot characteristics
- Asset inventory CRUD endpoints (`/api/v1/ativos`) with SQLite-backed persistence
- 5-pillar COBIT governance dashboard at `/` with score, trend, and component breakdown
- Pillar detail pages at `/pillars/<id>` with 30-day simulated trend chart

### Fixed
- Tooltip ghost on pillar cards — new `app/templates/` architecture does not use `data-tip` attributes; legacy `templates/dashboard.html` isolated to old dashboard routes only

### Changed
- `docker-compose.yml` healthcheck path updated from deleted `/health` stub to `/api/health`
- `INFLUX_PORT` must be set to `18086` on dev server (`172.29.2.11`) to avoid conflict with host-native InfluxDB used by Zabbix

## [1.0.0] - 2026-05-31

### Added
- **Cyber — Refazer isolamento em timeout:** quando o isolamento no Acronis terminava em timeout (`TIMED_OUT`/`TIMEOUT`), a máquina permanecia na lista de isoladas sem opção de tentar de novo. Agora o painel exibe o botão **Refazer isolamento** para máquinas com isolamento incerto, disparando novamente a ação `WORKLOAD_ISOLATE` via fila de aprovação do super admin.
- Supplier and contract management module with SLA tracking and breach alerting
- Zabbix, Zendesk, and Microsoft Graph integrations
- Flask-RESTX API with Swagger documentation at `/api/`
- Dual storage: SQLite/PostgreSQL for CRUD + InfluxDB for time-series metrics
- Grafana dashboard embedding via kiosk iframe
