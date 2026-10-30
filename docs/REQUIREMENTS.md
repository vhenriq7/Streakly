# Requisitos — Streakly

## 1. Escopo

Aplicativo pessoal de estudos para **Windows e Android**, com experiência simples, funcionamento offline, sincronização e backup. Uma única conta pessoal; não haverá cadastro público de outros usuários. Interface responsiva com navegação inferior no celular e lateral no PC. Tema claro, escuro e opção de acompanhar o sistema.

## 2. Matérias e classificação

- Criar, renomear e excluir matérias.
- **MVP 0.11.0:** renomear pelo identificador da matéria, preservando sessões, eventos, duração, preferências e sessão ativa. Remover espaços nas extremidades e recusar nome vazio; nomes iguais não unem matérias. Atualizar nomes na interface e no Histórico carregado, mantendo sua página. Cancelar não grava; recusar sobrescrever uma alteração de nome feita desde a leitura. Erro anterior à gravação mantém o formulário; erro posterior informa que a alteração já ocorreu.
- Criar e personalizar tipos de atividade por matéria ou catálogo reutilizável: **leitura, aula, exercícios, revisão** e outros.
- **MVP 0.13.0:** informar um tipo personalizado no Cronômetro e na criação/edição do Histórico, removendo espaços nas extremidades do novo nome e recusando vazio. Reutilizar os quatro padrões e nomes já registrados, sem alterar classificações antigas. O nome persiste no estudo após iniciar ou salvar; não há cadastro independente de tipos nesta etapa. Preservar rascunhos e impedir alterar o tipo da sessão ativa. Gestão de catálogo e personalização por matéria ficam para etapa posterior.
- Informar assunto/tópico opcional dentro de uma matéria (ex.: Python → laços → exercícios).
- Cada sessão armazena matéria, tipo de estudo, assunto opcional, início, fim e duração efetiva.
- **MVP 0.12.0 — escolha do usuário em 29/10/2026:** excluir permanentemente a matéria e todos os seus estudos, eventos e preferências após confirmação com nome, quantidade de sessões e duração total removida. Exigir finalização de qualquer sessão ativa dessa matéria, inclusive pausada ou aguardando fase Pomodoro. Recusar confirmação desatualizada se nome, conteúdo ou conjunto de estudos mudar desde a leitura. Cancelar, fechar ou desconectar antes de confirmar não exclui. Remover tudo em uma transação, recalcular totais e retornar o Histórico à primeira página, preservando outras matérias e a sessão ativa delas. Falha posterior à gravação informa que a exclusão já ocorreu; não oferecer repetição. Rascunhos cuja matéria foi removida exigem seleção explícita de outra; edições de sessões removidas preservam o preenchimento e bloqueiam salvar até cancelar e atualizar o Histórico.

## 3. Registro de tempo

### Painel inicial

- Exibir **total de estudo acumulado no dia**, somando todas as matérias. **Não** é botão para iniciar cronômetro.
- Atualizar enquanto houver sessão em andamento e reiniciar apenas a **exibição diária** à meia-noite; nunca apagar histórico.
- Exibir matérias e acesso à sessão ativa.
- **MVP 0.6.0:** o painel usa o dia do fuso local atual do dispositivo e calcula o total pelos eventos UTC, incluindo sessões finalizadas e a sessão ativa uma única vez. Repartir intervalos pela meia-noite local, excluindo pausas e espera. Ao mudar o dia, recalcular a exibição sem apagar registros. Alterar o fuso muda o dia exibido; a política de histórico entre dispositivos permanece para a sincronização.

### Cronômetro individual

- Escolher matéria, tipo e assunto opcional.
- Modos **progressivo** e **Pomodoro**, com último modo e configurações lembrados por matéria.
- Progressivo: iniciar, pausar, retomar e finalizar; pausas não contam como estudo.
- Pomodoro: presets **25 min foco / 5 min pausa**, **50/10** e valores personalizados para foco, pausa curta, pausa longa e ciclos até pausa longa.
- **PoC 0.4.0:** a interface personalizada usa minutos inteiros positivos e uma quantidade inteira positiva de focos. Os presets usam pausa longa de 15 minutos a cada 4 focos; esses valores podem ser alterados em Personalizado. O contador considera somente focos completos, é persistido por sessão e reinicia em uma nova sessão. Cada múltiplo de N prepara uma pausa longa; as demais são curtas. A sessão mantém a configuração do início e as preferências da matéria são salvas somente em um início bem-sucedido.
- Ao terminar uma fase, parar, notificar e **aguardar comando manual** para iniciar a próxima. Tempo de espera e intervalos não contam como estudo.
- Somente **uma sessão ativa por vez**, incluindo pausada. Antes de iniciar outra, orientar a retomar ou finalizar a anterior.
- Navegação entre telas não interrompe a sessão.
- Persistir eventos e horários para reconstruir duração após encerramento ou falha; não depender apenas de contador em memória.
- **Regra confirmada pelo usuário em 28/10/2026:** fechar o app com uma sessão progressiva rodando mantém a contagem até pausar ou finalizar, inclusive durante o período fechado. Ao reabrir, recuperar o estado e calcular esse intervalo pelos horários persistidos. Sessões pausadas permanecem pausadas, sem contabilizar o período fechado.
- Dividir corretamente o tempo de uma sessão que atravessa meia-noite entre os dias locais correspondentes.
- A execução em segundo plano no Android será validada em prova de conceito; não prometer comportamento impossível sob restrições do sistema.

## 4. Histórico

- Listar sessões com data, matéria, tipo, assunto e tempo efetivo.
- **MVP 0.7.0:** listagem somente de sessões finalizadas, ordenadas pelo início mais recente, em páginas de 25. Exibir também modo, início/fim no fuso local e duração sem pausas ou espera. Consultar ao abrir, atualizar ou paginar; falhas mantêm a lista e a página anteriores. A consulta não altera dados nem a sessão ativa. Cadastro manual, edição e exclusão são descritos nas etapas 0.8–0.10.
- Criar sessões manualmente e editar/excluir sessões existentes.
- **MVP 0.8.0 — escolha do usuário em 29/10/2026:** cadastrar por data/hora de início local e duração efetivamente estudada em HH:MM, calculando o fim e exibindo a prévia. Cada lançamento é um período contínuo, sem pausas, já finalizado e identificado como manual. Recusar duração nula/negativa, fim futuro e sobreposição com intervalos efetivos de estudo registrados localmente. Permitir adjacência e períodos sem estudo durante pausas/espera. Preservar o cronômetro e as preferências; atualizar o total pelos eventos.
- **MVP 0.9.0:** corrigir matéria, tipo e assunto de qualquer sessão finalizada. Permitir também ajustar início e duração de registros manuais, preservando UUIDs e recalculando o total pelos eventos. Alterações de classificação mantêm integralmente horários, segundos, pausas, ciclos e origem; horários de sessões feitas pelo cronômetro permanecem protegidos nesta etapa. Recusar gravação sobre uma sessão alterada desde a leitura, sem descartar os dados mais recentes. Edição dos intervalos capturados pelo cronômetro fica para uma etapa seguinte.
- **MVP 0.10.0:** excluir sessões finalizadas somente após confirmação com os dados do registro e indicação de exclusão permanente. Cancelar ou fechar a confirmação não grava; ao confirmar, remover sessão e registros dependentes juntos, recalcular o total e preservar matérias, preferências e sessão ativa. Recusar exclusão se a sessão mudou desde a leitura. Informar o resultado correto quando houver falha depois da gravação; não oferecer repetição de uma exclusão já concluída.
- Toda alteração recalcula totais, metas, médias, calendário e ofensivas.
- Identificadores únicos estáveis evitam duplicações na sincronização.
- Confirmar ações destrutivas.

## 5. Metas, descanso e ofensivas

- Meta diária padrão configurável (por exemplo, 3 horas).
- **MVP 0.14.0:** configurar uma meta positiva em HH:MM, sem valor obrigatório inicial. Exibir no Início a meta vigente, o progresso e o tempo restante ou a indicação de meta atingida, usando o total efetivo já calculado. Preservar o cronômetro e os estudos; cancelar não grava e navegar preserva o rascunho. Recusar gravação com configuração desatualizada ou após mudança do dia local; distinguir falha antes e depois de salvar. Metas específicas e descanso foram acrescentados na 0.15.0, e ofensivas na 0.16.0.
- Permitir **meta específica por data**, que substitui a padrão apenas naquele dia.
- Permitir **dias de descanso**: não aumentam nem quebram a ofensiva.
- **MVP 0.15.0:** consultar uma data em DD/MM/AAAA e configurar explicitamente meta específica positiva em HH:MM ou descanso, para datas passadas ou futuras. A exceção substitui o padrão somente naquele dia; removê-la, após confirmação, volta ao padrão vigente na data, ou à ausência de meta antes da primeira configuração. Descanso não apaga nem impede estudos e não é tratado como meta zero atingida. Preservar o padrão, os registros e o cronômetro. Mudar a data digitada exige nova consulta; navegar preserva o rascunho, cancelar não grava e uma configuração desatualizada não sobrescreve a atual. O Início usa a exceção de hoje, e Configurações distingue a meta padrão da meta efetiva. A tela Calendário começa pela consulta por data; a grade visual fica para etapa posterior e as ofensivas foram acrescentadas na 0.16.0.
- A ofensiva aumenta quando o tempo efetivo de estudo do dia atinge a meta aplicável; dias comuns abaixo da meta interrompem a sequência.
- Exibir ofensiva atual e recorde, com indicador visual semelhante a uma chama.
- **MVP 0.16.0:** calcular ofensiva e recorde desde a primeira meta aplicável, considerando dias locais e ignorando datas futuras. Meta atingida acrescenta um dia; dia encerrado abaixo dela interrompe a sequência. Hoje pendente conserva a sequência até encerrar; descanso e ausência de meta são neutros. Recalcular após edições retroativas de estudos ou planejamento, sem persistir um contador. Exibir os indicadores no Início e oferecer Atualizar ofensiva; alterações de outra instância ou mudanças de fuso não detectadas são incorporadas ao atualizar, abrir Início ou reabrir o app.
- **Regra confirmada pelo usuário em 29/10/2026:** alterações na meta padrão valem a partir de hoje e não reescrevem metas dos dias anteriores. Preservar a vigência de cada configuração, inclusive durante períodos com o app fechado; antes da primeira configuração, não há meta aplicável. Se houver uma vigência posterior ao dia local atual, recusar a gravação para evitar reescrever o passado após retrocesso do relógio ou mudança de fuso.
- Usar calendário e fuso local consistentes; definir comportamento para mudanças de fuso entre dispositivos.

## 6. Estatísticas e calendário

- Tempo total estudado.
- Progresso da meta diária.
- Média de horas por dia, **incluindo dias sem estudo** no período selecionado.
- Ofensiva atual e recorde.
- Tempo por matéria, assunto e tipo de estudo.
- Calendário com dias estudados, metas específicas e dias de descanso.
- Filtros: dia, semana, mês, ano e intervalo personalizado.
- Não exigir métrica de **quantidade de sessões** no painel de estatísticas.

## 7. Conta, offline e sincronização

- Login por **e-mail/senha** e **Google**, com possibilidade de associar provedores à mesma conta, conforme suporte do serviço.
- Manter sessão autenticada; permitir uso offline após autenticação inicial, respeitando expiração e requisitos de segurança.
- Operações de estudo, histórico, matérias, metas e estatísticas disponíveis sem conexão.
- Sincronizar **automaticamente** ao recuperar a conexão e enquanto o aplicativo estiver ativo; oferecer **Sincronizar agora**.
- Exibir status: sincronizado, pendente, em andamento ou erro.
- Não perder dados locais por falha de rede; enviar alterações de forma idempotente.
- Conciliar registros criados em dispositivos distintos, inclusive offline. Em edição conflitante do mesmo registro, **não descartar alterações silenciosamente**: detectar e apresentar resolução.
- A regra de sessão única é garantida localmente; **não é possível garanti-la em tempo real entre dois dispositivos simultaneamente offline**. Detectar e reconciliar sessões sobrepostas depois.

## 8. Backup e restauração

- Exportar sessões e dados tabulares em **CSV** para análise.
- Exportar **JSON completo** com matérias, tipos, assuntos, sessões, metas, descanso e preferências relevantes.
- Importar JSON para restaurar histórico; validar formato, versão do esquema e integridade antes de aplicar.
- Pré-visualizar o que será restaurado, confirmar substituições/conflitos e criar cópia de segurança anterior à importação.
- Evitar duplicações ao importar repetidamente.
- Nunca incluir senhas, tokens ou chaves de serviço em backups.
- O backup exportado deve continuar utilizável mesmo se a nuvem estiver indisponível.

## 9. Lembretes e notificações

- Avisar conclusão de fase de foco e pausa Pomodoro.
- **PoC 0.5.0:** avisos do Windows com o app aberto, inclusive minimizado, mantendo início manual da próxima fase. Cada conclusão gera uma tentativa por instância; falha no envio não altera os dados nem repete o aviso a cada consulta. Na abertura, conclusões anteriores aparecem na tela sem notificação atrasada. O Windows controla a exibição; envio aceito não garante entrega visual. Avisos Android e agendamento com o app fechado permanecem para validação posterior.
- Lembretes de estudo em horários configuráveis.
- Alerta de meta pendente em horário configurável, somente se a meta não tiver sido atingida e o dia não for de descanso.
- Preferências sincronizadas, quando aplicável.
- Solicitar permissões necessárias; testar comportamento Android com aplicativo em segundo plano ou fechado.

## 10. Telas

1. **Início:** acumulado diário, matérias, sessão ativa.
2. **Estatísticas:** indicadores, gráficos e filtros.
3. **Calendário:** metas por data, descanso, ofensivas.
4. **Histórico:** listagem e edição de sessões.
5. **Configurações:** conta, meta padrão, Pomodoro, tema, notificações, sincronização e backups.

## 11. Requisitos não funcionais e aceite

- **Sem custo obrigatório** para uso pessoal dentro de limites gratuitos.
- Persistência local confiável, inclusive após reinício inesperado.
- Nenhuma sessão duplicada ou perdida após reenvio de sincronização.
- Cálculo do tempo baseado em eventos/horários confiáveis, sem contabilizar pausas.
- Operação offline verificável em ambos os sistemas.
- Separação de credenciais: nenhuma chave privilegiada embutida no cliente.
- Interface utilizável em telas pequenas e desktop.
- Testes mínimos: virada de dia, metas diferentes, descanso, edição retroativa, importação repetida, interrupção do app, desconexão e conflitos entre dispositivos.

## 12. Fora do escopo inicial

- Cadastro público e colaboração entre usuários.
- Recursos pagos, assinaturas e infraestrutura própria.
- Integrações com plataformas educacionais.
- IA para recomendações de estudo.
- Garantia de notificações com aplicativo encerrado em todas as versões do Android.

> Este documento registra decisões de produto. Itens marcados como recomendação ou dependentes de prova de conceito exigem validação antes de implementação definitiva.
