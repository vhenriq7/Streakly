# Arquitetura — Streakly

## 1. Visão geral

Aplicativo **offline-first**, pessoal, para Windows e Android.

```text
          ┌──────────────────────────────────────┐
          │ Aplicativo Python + Flet (a validar)  │
          │ UI · timers · regras · estatísticas   │
          └──────────────────┬───────────────────┘
                             │
                     ┌───────▼────────┐
                     │ SQLite local   │
                     │ fonte imediata │
                     │ + fila de sync │
                     └───────┬────────┘
                             │ rede disponível
                     ┌───────▼────────┐
                     │ Supabase       │
                     │ Auth + API     │
                     │ PostgreSQL     │
                     └────────────────┘
```

O SQLite responde às operações locais. O PostgreSQL centraliza cópias sincronizadas entre dispositivos, **não substitui** o armazenamento offline.

## 2. Stack e validação

- **Python:** lógica de domínio e aplicativo.
- **Flet:** UI multiplataforma proposta; testar empacotamento, permissões e plugins no Android antes de confirmar.
- **SQLite:** banco por instalação, transações e persistência local.
- **Supabase PostgreSQL:** armazenamento remoto no plano gratuito, sujeito a limites e eventual pausa por inatividade.
- **Supabase Auth:** e-mail/senha e Google. Configurar redirecionamentos/deep links específicos por plataforma.
- **GitHub:** documentação e versionamento.

Não criar servidor próprio na primeira versão. Cliente usa somente credenciais públicas apropriadas e autenticação do usuário, com **Row Level Security (RLS)** e regras de acesso. **Nunca** distribuir service_role, senha de banco ou segredo administrativo. Avaliar necessidade de funções de servidor apenas para operações que não possam ser protegidas no cliente.

## 3. Estrutura lógica sugerida

- `ui/`: telas, componentes e navegação.
- `domain/`: sessão, metas, ofensivas e regras de cálculo.
- `storage/`: SQLite, migrações e repositórios.
- `sync/`: fila de mudanças, envio, recebimento e resolução de conflitos.
- `auth/`: login e gestão segura da sessão.
- `notifications/`: lembretes e eventos Pomodoro.
- `backup/`: exportação, validação e importação.

Estrutura ilustrativa, não uma exigência de criar pastas antes da prova de conceito.

## 4. Modelo de dados inicial

Entidades candidatas:

| Entidade | Campos principais |
| --- | --- |
| `subjects` | id UUID, nome, estado de exclusão, versão |
| `study_types` | id UUID, nome, personalização |
| `topics` | id UUID, subject_id, nome |
| `sessions` | id UUID, subject_id, study_type_id, topic_id?, início, fim, duração efetiva, modo, versão |
| `session_events` | id UUID, session_id, tipo (start/pause/resume/finish/focus/break), instante |
| `daily_goals` | data local, meta efetiva/override, descanso, versão |
| `settings` | meta padrão, tema, notificações, preferências Pomodoro |
| `sync_outbox` | operação UUID, entidade, id, tipo, payload, data, estado |
| `sync_metadata` | cursor remoto, dispositivo, última sincronização |

Adicionar `user_id` remoto para vincular todos os dados à conta, mesmo sendo de uso pessoal. Usar UUID para chaves criadas offline; timestamps em UTC e data de estudo calculada com política explícita de fuso local. Criar migrações versionadas.

**Detalhes ainda a projetar:** regras para assuntos renomeados/excluídos, snapshots de metas concluídas e definição de conflitos. A representação Pomodoro da PoC está descrita na seção 5.

## 5. Contagem confiável de tempo

- Guardar eventos persistidos de início, pausa, retomada e conclusão.
- Calcular tempo efetivo somando **apenas intervalos de foco/estudo**, sem pausas e sem espera entre fases.
- Na retomada após falha, reconstruir a sessão a partir do estado persistido e horários; aplicar política segura para eventos interrompidos.
- Ao cruzar meia-noite, repartir intervalos entre datas locais para metas e estatísticas.
- Atualizar o acumulado diário em tela a partir das sessões registradas e do intervalo ativo.
- **Sessão única local:** impedir duas sessões simultâneas no mesmo dispositivo; detectar possíveis sobreposições vindas de dispositivos offline diferentes.

**Decisão da PoC 0.2.0:** por escolha do usuário, uma sessão progressiva rodando continua contando enquanto o app está fechado, até pausar ou finalizar. Uma sessão pausada conserva o tempo acumulado. A interface reconstrói a duração a partir dos eventos UTC, sem depender de execução em segundo plano. A tarefa assíncrona atualiza a tela somente enquanto visível e é cancelada ao desconectar/encerrar a página; isso não altera a sessão no banco. A versão 0.5.0 mantém a verificação de prazos com a janela minimizada no Windows para enviar avisos, sem atualizar visualmente a janela oculta.

`timer.py` define os dados e soma intervalos; `storage.py` persiste sessões e eventos. O esquema 2 acrescenta as tabelas preservando as matérias do esquema 1. Um índice único parcial garante uma sessão ativa, inclusive pausada, e `BEGIN IMMEDIATE` serializa as transições. Cada ação grava evento, estado e duração acumulada na mesma transação. Os eventos têm UUID e sequência por sessão para ordenar ações com horários iguais. Versões futuras do banco são recusadas, sem rebaixar o esquema.

Nesta base, tipo de estudo e assunto são textos na sessão; o catálogo relacional completo permanece no modelo candidato. A duração atravessando meia-noite é preservada e o painel 0.6.0 acrescenta o rateio por dia; metas e estatísticas completas vêm depois. O relógio do dispositivo precisa estar correto: transições anteriores ao último evento são recusadas; a detecção completa de mudanças de relógio permanece em aberto.

**Pomodoro básico 0.3.0:** as fases são `focus` e `break`, com estados `running`, `paused` e `ready`. O esquema 3 adiciona `pomodoro_state` e `subject_timer_settings`, sem alterar os dados de sessões anteriores. Preferências por UUID da matéria são salvas junto de um início bem-sucedido; a sessão mantém seus próprios tempos. Essa versão introduziu os presets 25/5 e 50/10.

Os eventos de estudo descrevem somente o foco. A conclusão de foco grava `pause` no prazo exato e prepara a pausa em `ready`; a conclusão da pausa prepara o próximo foco em `ready`, sem somar estudo. Cada próxima fase exige `start_next_phase`. Pausar/retomar um intervalo modifica apenas seu estado e tempo restante. O `status` da sessão indica execução da fase (inclusive `running` durante um intervalo); o tempo de estudo vem dos eventos, usando `session_elapsed_seconds`.

`refresh_active_session` consulta primeiro e só abre transação de escrita para consolidar um prazo vencido, relendo o estado sob `BEGIN IMMEDIATE`. As ações também consolidam o prazo dentro da própria transação. O cálculo puro limita o foco ao prazo mesmo antes dessa consolidação, cobrindo o período com o app fechado. O aviso de término aparece na interface; o envio ao Windows foi acrescentado separadamente na versão 0.5.0.

**Personalização e pausa longa 0.4.0:** o esquema 4 adiciona `long_break_seconds`, `cycles_until_long_break` e `preset` às preferências e ao estado da sessão. `pomodoro_state` também recebe `completed_focuses` e `is_long_break`; a pausa longa continua sendo uma fase `break`. Os presets têm duração longa de 900 segundos a cada 4 focos. A interface converte minutos inteiros positivos em segundos, e a persistência valida os valores antes da gravação. A identificação `custom` é preservada mesmo quando os tempos coincidem com um preset.

O contador aumenta uma única vez ao consolidar um foco completo, na mesma transação do evento e da próxima fase. `completed_focuses % cycles_until_long_break == 0` escolhe a pausa longa. Pausas manuais, intervalos e finalização antecipada não completam focos. As configurações da sessão são uma cópia das preferências no início; mudar de modo para progressivo preserva os últimos parâmetros Pomodoro da matéria.

A migração usa `ALTER TABLE`, preservando as colunas e os eventos anteriores. Para recuperar o contador do esquema 3, desconta da duração consolidada o progresso já gravado do foco atual e divide o restante pela duração de foco, com arredondamento para compensar frações de segundo. Pausas manuais não são contadas como ciclos. O estado e o tempo restante atuais são preservados, inclusive pausas antigas que continuam curtas. A migração inteira é transacional e sua repetição não altera o banco já atualizado.

**Avisos Windows 0.5.0:** `notifications.py` contém `PhaseAlertTracker`, que observa a sessão e identifica uma nova fase `ready` pela chave `(session.id, phase, updated_at)`. A primeira leitura apenas inicializa o rastreador, evitando aviso atrasado ao reabrir. A chave é marcada antes do envio, preservada na reconexão da mesma página e não depende do sucesso da notificação. É uma tentativa por conclusão por instância, sem registro de entrega persistido ou deduplicação entre processos. Sessões progressivas, pausas manuais e sessões finalizadas não geram avisos.

O envio usa `NotifyIcon.ShowBalloonTip` via Windows PowerShell, em processo oculto. O script é constante; título e texto são dados JSON pelo stdin, sem interpolar nomes de matérias no código. Há limite de 20 segundos para o processo, e `finally` remove o ícone temporário. `asyncio.to_thread` mantém esse envio fora do loop da interface e da transação SQLite. Cancelar a tarefa Python não interrompe um processo já iniciado; um aviso em envio pode permanecer por aproximadamente 12 segundos após fechar. A exibição depende do Windows e não é comprovada pelo código de saída.

Com suporte disponível no Windows, a tarefa continua consultando o prazo enquanto a janela está oculta e só chama `page.update` quando conectada e visível. No Android, mantém `wait_until_visible` e somente o aviso na tela. O esquema permanece na versão 4; não há agendamento com o app fechado, permissões novas nem alterações nas configurações do sistema. A validação de notificações Android permanece pendente.

**Painel diário 0.6.0:** `timer.py` compartilha a leitura dos eventos em `_study_intervals`, um gerador de intervalos efetivos de estudo. `session_study_seconds_between` recorta esses intervalos à janela do dia e ao instante consultado, preservando o limite do foco Pomodoro. `local_day_window` converte separadamente as duas meias-noites locais para UTC; não reutiliza o deslocamento fixo de hoje nem presume dias de 24 horas. Os cálculos de interseção e duração usam UTC. O padrão é o fuso atual do sistema; os testes também podem fornecer um fuso explícito. Não há dependência nova de base de fusos.

`read_study_overview` consolida primeiro um prazo Pomodoro vencido e então abre uma transação de leitura para retornar `StudyOverview`: matérias, sessão ativa, data e total. Seleciona somente sessões cujo período sobrepõe o dia e reutiliza a sessão ativa dessa mesma leitura no cálculo, sem somá-la duas vezes. O total é derivado dos eventos e não fica gravado; o esquema permanece na versão 4. Mudar o fuso do dispositivo recalcula o dia atual exibido; política histórica entre dispositivos continua pendente.

`main.py` abre o painel Início e atualiza o resumo junto do cronômetro. Atalhos usam o UUID da matéria, carregam suas preferências e preservam a sessão existente. Falha de leitura mostra total indisponível e bloqueia ações até recuperar o acesso, sem substituir dados por zero. A virada do dia altera apenas o resumo, mantendo o histórico. Metas, edição e visualização de histórico continuam em etapas futuras.

## 6. Protocolo de sincronização

1. Salvar alteração **em transação local**, junto com item na outbox.
2. Com internet, autenticar, enviar operações pendentes de forma **idempotente** (UUID por operação).
3. Confirmar recebimento remoto antes de retirar da fila.
4. Buscar alterações remotas por cursor/versão, incluindo **tombstones** para exclusões.
5. Aplicar atualizações em transação SQLite, atualizar cursor e indicadores de status.
6. Para alterações concorrentes na **mesma entidade**, comparar versões e solicitar resolução, preservando ambas as versões até decisão.

Evitar a regra simplista de “última escrita vence” para sessões e metas. O sincronizador deve tolerar repetição, falhas intermediárias, reconexões e mudanças de ordem. Sincronização automática enquanto o app está ativo e ao recuperar a rede; botão manual adicional. Restrições de background Android impedem promessa de sincronização contínua com o app fechado.

## 7. Segurança

- Supabase Auth e RLS para isolar registros da conta.
- Credenciais de sessão em armazenamento seguro quando disponível; nunca em logs ou exportações.
- Tokens de autenticação não são dados de backup.
- Validar autorização em cada tabela/operação remota.
- Não confundir **sincronização** com **backup independente**.

## 8. Exportação e importação

- CSV para análise, sem compromisso de restaurar todos os relacionamentos.
- JSON versionado para backup integral, incluindo identificadores estáveis e metadados suficientes para reconstrução.
- Validar esquema, relações e duplicatas; exibir prévia e pedir confirmação.
- Criar cópia de segurança local antes de importar.
- Planejar importação transacional, reversão em erro e sincronização posterior das alterações restauradas.

## 9. Prova de conceito obrigatória

**Fluxo de validação combinado com o usuário:** usar Windows e testes automatizados no desenvolvimento cotidiano. A geração e instalação de APKs ficam reservadas a marcos maiores e às mudanças específicas do Android (permissões, notificações, segundo plano, armazenamento e empacotamento). Não exigir uma nova instalação no celular a cada funcionalidade; os critérios Android abaixo serão verificados nesses marcos.

**Andamento:** a base 0.1.0 foi confirmada no Windows e no Moto G84 5G, incluindo cadastro e persistência offline. O cronômetro progressivo 0.2.0 teve 11 testes de domínio/persistência e integração da interface aprovados, e o usuário confirmou seu funcionamento no Windows em 28/10/2026. O APK 0.2.0 foi gerado e verificado. O Pomodoro básico 0.3.0 passou nos 23 testes de regressão/Pomodoro. A versão 0.4.0 acrescentou personalização e pausa longa, passando nos 29 testes de regressão, ciclos e migração. A versão 0.5.0 acrescentou avisos Windows, com 39 testes aprovados e envio real isolado sem erro. A integração com controles Flet, SQLite temporário e relógio simulado também passou, incluindo janela oculta, não repetição/reabertura, falha no envio e interface responsiva durante o envio. O usuário confirmou visualmente em 28/10/2026 o aviso de foco concluído para Python, com instrução para iniciar a pausa curta. Essa confirmação não especificou o estado minimizado da janela ou as demais fases. O cabeçalho Windows PowerShell corresponde ao processo que envia o aviso nesta PoC; identidade própria fica para o empacotamento. Esta etapa não gera APK; notificações Android seguem nos marcos combinados.

O Android reutiliza `main.py`, `storage.py`, `timer.py` e `notifications.py`, com `pyproject.toml` para empacotamento. O envio específico do Windows fica desabilitado em outras plataformas. O banco usa `FLET_APP_STORAGE_DATA`; no Windows, o caminho original em `%LOCALAPPDATA%\Streakly` foi mantido para preservar os dados já cadastrados. `build_android.py` contorna limitações de caminhos com acentos do Flet/Flutter/CMake nesta instalação Windows, usando uma cópia apenas do código em pasta auxiliar e o SDK em caminho sem acentos. Os detalhes de execução estão no README. Autenticação e sincronização continuam reservadas à etapa de nuvem.

**Início do MVP offline 0.6.0:** o painel diário passou nos 51 testes de regressão e de rateio, incluindo pausas, meia-noite, fusos, limite Pomodoro e reabertura em outro processo. A integração com controles Flet e SQLite temporário verificou soma entre matérias, virada de data, navegação por UUID, recuperação, aviso com janela oculta e falha/recuperação de leitura. Falta a conferência do usuário na janela nativa. Nenhum banco pessoal foi usado nos testes e nenhum APK foi gerado nesta etapa.

Antes do MVP completo:

1. Empacotar e executar um aplicativo Flet simples no Android e Windows.
2. Persistir SQLite em diretório durável em ambas as plataformas.
3. Iniciar cronômetro, alternar telas, encerrar/reabrir e reconstruir duração.
4. Testar alertas Pomodoro e lembretes no Android em primeiro plano, segundo plano e app encerrado.

Na etapa de conta e sincronização, antes de concluir a integração com a nuvem:

1. Autenticar via e-mail e Google, incluindo retorno ao app.
2. Sincronizar sessões criadas offline nos dois dispositivos e detectar edição conflitante.

Se Flet não atender aos critérios, reavaliar somente a camada de interface, preservando requisitos e modelo de domínio.

## 10. Critérios de implementação

- Priorizar dados íntegros e lógica testável sobre efeitos visuais.
- Documentar mudanças de regra nestes arquivos, evitando documentação fragmentada.
- Usar bibliotecas gratuitas e manter possibilidade de exportação independente do provedor.
- Implementar por etapas: **prova de conceito → MVP offline → nuvem → confiabilidade/distribuição**.
