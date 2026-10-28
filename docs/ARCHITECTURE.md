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

**Detalhes ainda a projetar:** representação de ciclos Pomodoro, tempos de foco versus intervalos, regras para assuntos renomeados/excluídos, snapshots de metas concluídas e definição de conflitos.

## 5. Contagem confiável de tempo

- Guardar eventos persistidos de início, pausa, retomada e conclusão.
- Calcular tempo efetivo somando **apenas intervalos de foco/estudo**, sem pausas e sem espera entre fases.
- Na retomada após falha, reconstruir a sessão a partir do estado persistido e horários; aplicar política segura para eventos interrompidos.
- Ao cruzar meia-noite, repartir intervalos entre datas locais para metas e estatísticas.
- Atualizar o acumulado diário em tela a partir das sessões registradas e do intervalo ativo.
- **Sessão única local:** impedir duas sessões simultâneas no mesmo dispositivo; detectar possíveis sobreposições vindas de dispositivos offline diferentes.

**Decisão da PoC 0.2.0:** por escolha do usuário, uma sessão progressiva rodando continua contando enquanto o app está fechado, até pausar ou finalizar. Uma sessão pausada conserva o tempo acumulado. A interface reconstrói a duração a partir dos eventos UTC, sem depender de execução em segundo plano. A tarefa assíncrona atualiza a tela somente enquanto visível e é cancelada ao desconectar/encerrar a página; isso não altera a sessão no banco.

`timer.py` define os dados e soma intervalos; `storage.py` persiste sessões e eventos. O esquema 2 acrescenta as tabelas preservando as matérias do esquema 1. Um índice único parcial garante uma sessão ativa, inclusive pausada, e `BEGIN IMMEDIATE` serializa as transições. Cada ação grava evento, estado e duração acumulada na mesma transação. Os eventos têm UUID e sequência por sessão para ordenar ações com horários iguais. Versões futuras do banco são recusadas, sem rebaixar o esquema.

Nesta PoC, tipo de estudo e assunto são textos na sessão; o catálogo relacional completo permanece no modelo candidato. A duração atravessando meia-noite é preservada, mas o rateio por dia para estatísticas/metas vem depois. O relógio do dispositivo precisa estar correto: transições anteriores ao último evento são recusadas; a detecção completa de mudanças de relógio permanece em aberto.

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

**Andamento:** a base 0.1.0 foi confirmada no Windows e no Moto G84 5G, incluindo cadastro e persistência offline. A versão 0.2.0 implementa o cronômetro progressivo com recuperação, pausas e sessão única. Os 11 testes de domínio/persistência e a integração da interface com controles Flet passaram. O APK 0.2.0 foi gerado e verificado, com o mesmo identificador e assinatura da base 0.1.0; o teste da nova versão no cliente nativo permanece pendente. Pomodoro e notificações ainda serão implementados e validados.

O Android reutiliza `main.py`, `storage.py` e `timer.py`, com `pyproject.toml` para empacotamento. O banco usa `FLET_APP_STORAGE_DATA`; no Windows, o caminho original em `%LOCALAPPDATA%\Streakly` foi mantido para preservar os dados já cadastrados. `build_android.py` contorna limitações de caminhos com acentos do Flet/Flutter/CMake nesta instalação Windows, usando uma cópia apenas do código em pasta auxiliar e o SDK em caminho sem acentos. Os detalhes de execução estão no README. Autenticação e sincronização continuam reservadas à etapa de nuvem.

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
