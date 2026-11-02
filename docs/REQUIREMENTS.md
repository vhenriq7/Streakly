# Requisitos — Streakly

## 1. Escopo

**Prova de conceito Windows — empacotamento 0.31.0:** produzir `Streakly.exe` com nome e metadados de versão, contendo o aplicativo e seu runtime Python para execução sem instalação de Python no computador. Manter o artefato gerado em `build/`, sem adicioná-lo ao repositório, e manter o banco SQLite no armazenamento local do usuário. Esta prova não é instalador nem inclui assinatura digital. O usuário confirmou que o executável abre e preserva os dados ao reabrir; a validação Android continua pendente conforme o roadmap.

Aplicativo pessoal de estudos para **Windows e Android**, com experiência simples, funcionamento offline, sincronização e backup. Uma única conta pessoal; não haverá cadastro público de outros usuários. Interface responsiva com navegação inferior no celular e lateral no PC. Tema claro, escuro e opção de acompanhar o sistema.

**MVP 0.24.0 — tema:** oferecer Sistema, Claro e Escuro em Configurações, com Sistema como padrão sem registro inicial. Selecionar mantém um rascunho; somente Salvar tema grava e aplica a aparência. Preservar o rascunho ao navegar, e Cancelar tema restaura a preferência carregada sem escrever. Aplicar a escolha persistida ao iniciar ou reabrir o app; Atualizar tema consulta e aplica o valor salvo, preservando uma seleção diferente ainda em edição. Recusar gravação se outra janela alterou a preferência desde a leitura, orientando atualizar antes de tentar novamente. Falhas anteriores à gravação mantêm aparência e rascunho; erro ao aplicar depois do commit informa que a preferência já foi salva. Ler o tema na inicialização, ao abrir Configurações ou por atualização explícita, sem consulta no tick. A preferência é local a cada dispositivo nesta etapa, sem conta, sincronização, dependências novas ou APK.

**MVP 0.25.0 — navegação adaptável:** usar menu lateral em larguras a partir de 800 pixels e menu inferior com rolagem horizontal abaixo desse limite. Quando a largura ainda não estiver disponível, iniciar com menu inferior. Preservar os sete destinos existentes: Início, Matérias, Cronômetro, Histórico, Configurações, Calendário e Estatísticas, com rótulos visíveis e indicação do destino atual. Os atalhos do Início para Cronômetro e Configurações devem atualizar essa indicação. Concluir uma consulta de navegação ou do atalho de matéria não deve trocar o destino escolhido durante a espera. Redimensionar muda apenas a disposição da navegação, sem recriar painéis, repetir consultas ou alterar sessão, rascunhos, paginação e filtros. A validação cotidiana usa a janela do Windows em larguras diferentes; isso não comprova execução no Android. Não há migração, dependências novas ou APK nesta etapa.

**MVP 0.26.0 — rolagem de Início e Matérias:** permitir rolar todo o conteúdo de cada um desses painéis quando ultrapassar a altura disponível, incluindo indicadores, atalhos, cadastro, renomeação e lista de matérias. Usar uma rolagem vertical por painel, preservando controles, callbacks, rascunhos, seleção de tela e sessão ao redimensionar. A mudança de tamanho não consulta nem grava dados. Histórico e Calendário mantêm o comportamento existente nesta etapa. Validar cotidianamente no Windows em janelas baixas, sem presumir validação física no Android. Não há migração, dependências novas ou APK; o esquema permanece na versão 8.

**MVP 0.34.0 — diário remoto de sincronização:** criar no PostgreSQL uma tabela append-only para as operações com cursor ordenável, chave de idempotência por conta, entidade, identificador, ação e payload. Aplicar RLS para que usuários autenticados leiam e incluam somente operações da própria conta; negar ao cliente atualização e exclusão, preservando tombstones e o histórico do cursor. Esta etapa define a migração; aplicá-la e testar as políticas no projeto Supabase antes de habilitar tráfego. Não implementar autenticação no app nem envio de dados nesta etapa.

**MVP 0.35.0 — cursor concorrente:** serializar a atribuição do cursor por conta dentro da transação de inclusão. Leituras paginadas por cursor não podem perder uma operação cuja transação tenha confirmado após o cliente avançar além de sua sequência. Lacunas são aceitas, mas cada nova operação visível da mesma conta precisa ter sequência maior que as já confirmadas. Validar comportamento concorrente em PostgreSQL/Supabase antes de ativar sincronização.

**MVP 0.36.0 — transporte REST:** preparar envio de lotes da outbox ao diário Supabase, ignorando reenvios pela chave por conta/operação e confirmando que o servidor guardou o mesmo conteúdo antes de autorizar a remoção local. Buscar páginas ordenadas após o cursor com validação de forma e limite. Usar chave pública e token de sessão fornecido pelo futuro Auth, sem persistir nem registrar o token neste cliente. Não ligar o transporte à interface ou iniciar sincronização automática antes da configuração e teste do projeto Supabase. Sem dependências externas nesta etapa.

**MVP 0.37.0 — recebimento durável:** adicionar ao SQLite um vínculo de conta, cursor remoto e caixa de entrada que não faz parte dos backups. Salvar todas as operações de uma página e avançar o cursor na mesma transação; repetir uma página idêntica não duplica nem altera o estado. Recusar conta diferente, sequência fora de ordem e conteúdo divergente já recebido. Nesta etapa, conservar os dados remotos na caixa de entrada sem aplicá-los ao histórico local; a aplicação depende da política de conflitos por entidade. Não gerar operações locais ao receber dados. Migrar 9→10 sem alterar estudos ou enfileirar dados antigos.

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
- **MVP 0.15.0:** consultar uma data em DD/MM/AAAA e configurar explicitamente meta específica positiva em HH:MM ou descanso, para datas passadas ou futuras. A exceção substitui o padrão somente naquele dia; removê-la, após confirmação, volta ao padrão vigente na data, ou à ausência de meta antes da primeira configuração. Descanso não apaga nem impede estudos e não é tratado como meta zero atingida. Preservar o padrão, os registros e o cronômetro. Mudar a data digitada exige nova consulta; navegar preserva o rascunho, cancelar não grava e uma configuração desatualizada não sobrescreve a atual. O Início usa a exceção de hoje, e Configurações distingue a meta padrão da meta efetiva. A tela Calendário começou pela consulta por data; ofensivas foram acrescentadas na 0.16.0 e a grade mensal na 0.17.0.
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
- **MVP 0.17.0:** exibir grade mensal de segunda a domingo, com tempo efetivo e situação do planejamento por dia, navegação entre meses, retorno ao mês atual e atualização explícita. Usar textos, cores e detalhes para distinguir meta atingida, dia encerrado abaixo da meta, hoje em andamento, descanso, ausência de meta e futuro planejado. Consultar um dia pela grade não grava; preservar rascunhos ao navegar e exigir cancelar antes de selecionar outro dia durante a edição. A grade é uma leitura do mês, sem consulta a cada segundo; gravações atualizam o mês visível. Falhas mantêm mês e dados anteriores com aviso, sem substituir valores por zero.
- Filtros: dia, semana, mês, ano e intervalo personalizado.
- **MVP 0.27.0 — calendário em janelas estreitas:** manter a grade mensal com largura mínima de 640 pixels e preencher a área disponível quando for mais larga. Cabeçalho dos dias da semana e grade devem compartilhar a mesma rolagem horizontal, permitindo alcançar os sete dias sem comprimir as células abaixo dessa largura mínima. Manter título, avisos, legenda e editor de planejamento fora da área horizontal. Redimensionar preserva os botões, o mês consultado, a seleção de tela, a sessão e os rascunhos, sem reconstruir a grade ou consultar/gravar dados. Permanecem as regras existentes para consultar um dia, editar, cancelar e navegar entre meses. Validar no Windows sem presumir execução física no Android; não há migração, dependências novas ou APK, e o esquema permanece na versão 8.
- **MVP 0.28.0 — exportação CSV do Histórico:** oferecer no Histórico a exportação de todas as sessões finalizadas, sem depender da página exibida e sem incluir a sessão ativa. Preservar a ordenação por início mais recente e exportar UUID da sessão e da matéria, nome atual da matéria, tipo, assunto, início e fim locais em ISO 8601 com fuso, duração efetiva numérica em segundos, modo e origem. Gerar CSV UTF-8 com BOM para compatibilidade com planilhas e usar o seletor de destino do Flet; cancelar não grava nada. Ler as sessões em uma única visão consistente do SQLite, sem alterar registros, cronômetro ou banco. Não há migração ou dependências novas; validar no Windows e reservar teste físico Android ao marco combinado. O esquema permanece na versão 8.
- **MVP 0.29.0 — exportação de backup JSON:** oferecer em Configurações uma exportação versionada e UTF-8 de todas as tabelas locais, incluindo identificadores, eventos, sessões finalizadas e ativa, configurações dos cronômetros, metas, descansos e preferências relevantes. Incluir versão do formato, versão do esquema e horário de exportação. Ler em uma única transação SQLite; usar seletor de destino, sem alterar os dados e sem incluir segredos. Não implementar importação nesta etapa; ela exige validação, prévia, política de conflitos e backup antes de aplicar. Sem migração ou dependências novas; validar no Windows e reservar teste físico Android ao marco combinado. O esquema permanece na versão 8.
- **MVP 0.30.0 — importação de backup JSON por mesclagem:** selecionar um arquivo, validar UTF-8, formato, versão do esquema, colunas, restrições e relações antes de mostrar a prévia. Inserir linhas ausentes usando seus identificadores; manter localmente qualquer linha com ID já existente e conteúdo diferente. Se uma sessão tiver conflito, não importar seus eventos nem seu estado Pomodoro. Informar totais de novos, idênticos e conflitantes, pedir confirmação, salvar uma cópia JSON do estado atual e aplicar numa única transação; detectar mudança local entre prévia e gravação e exigir nova prévia. Cancelar ou falhar não altera parcialmente o banco. Importação repetida não duplica dados. Sem migração ou dependências novas; validar no Windows e reservar teste físico Android ao marco combinado. O esquema permanece na versão 8.
- **MVP 0.31.0 — lembretes diários no Windows:** permitir configurar e ativar separadamente um lembrete de estudo e um aviso de meta diária pendente, cada um com horário local HH:MM; ambos começam desativados. O aviso de meta só pode ocorrer se houver meta efetiva para hoje, o dia não for descanso e o tempo estudado estiver abaixo dela. Tentar cada aviso no máximo uma vez por data local e horário configurado durante a execução do aplicativo. Verificar o horário com o app aberto ou minimizado no Windows usando o envio de notificação já existente. Persistir as preferências locais em `app_settings` para incluí-las nos backups; validar horários e impedir sobrescrita de configuração concorrente. Não prometer avisos após encerrar o processo nem suporte Android nativo nesta etapa. Sem migração ou dependências novas; esquema permanece na versão 8.
- **MVP 0.32.0 — notificações Android do Pomodoro:** solicitar permissão para notificações e alarmes exatos, permitir enviar um aviso de teste e agendar a notificação do término da fase Pomodoro em curso, inclusive com o aplicativo em segundo plano. Cancelar o alarme ao pausar ou finalizar; o início da fase seguinte continua manual. Se alarmes exatos não estiverem disponíveis, usar o agendamento aproximado e informar a limitação do sistema. Validar no Windows com testes e empacotar APK para validação Android em marco próprio; não afirmar suporte quando o processo estiver encerrado sem teste específico. Integração `flet-android-notifications==0.12.0`, biblioteca comunitária MIT em Alpha. Sem alteração do esquema SQLite.
- **MVP 0.33.0 — fila local para sincronização:** acrescentar uma outbox SQLite com identificador idempotente para cada operação e captura do estado da entidade, gravada atomicamente com matérias, sessões, eventos, configurações dos cronômetros e metas. Manter operações pendentes até confirmação explícita; permitir leitura em lotes ordenados para reenvio. Não enfileirar atualizações derivadas de cada tick do cronômetro nem preferências exclusivas do dispositivo. Migrar o esquema 8→9 sem gerar operações para dados históricos; a carga inicial da conta será definida na integração de nuvem. Não implementar login, rede ou resolução de conflitos nesta etapa. Backups existentes continuam sem incluir a fila e backups de esquema 8 continuam importáveis.
- **MVP 0.18.0:** iniciar Estatísticas pelo total e pela média diária nos filtros Hoje, semana atual de segunda-feira até hoje, mês atual do dia 1 até hoje, ano atual de 1º de janeiro até hoje e intervalo personalizado com início/fim inclusivos em DD/MM/AAAA. Recusar início posterior ao fim. O divisor é o número de datas do período, incluindo dias sem estudo, descanso e ausência de meta; usar dias de calendário, sem presumir 24 horas em cada dia. Permitir datas futuras explicitamente escolhidas no personalizado, informando que contam como zero na média e que os estudos são limitados ao instante da consulta. Incluir a sessão ativa uma única vez, excluindo pausas/espera e respeitando o limite de foco Pomodoro. Consultar ao abrir, aplicar filtro, atualizar explicitamente e após gravações relevantes quando a tela estiver visível, sem recalcular no tick. Preservar preenchimento ao navegar e manter período e resultados anteriores em falha, com aviso. A consulta não grava dados. A divisão por matéria foi acrescentada na 0.19.0, por tipo na 0.20.0 e por assunto na 0.21.0; a comparação visual por matéria foi acrescentada na 0.22.0.
- **MVP 0.19.0:** mostrar o tempo efetivo por matéria no mesmo período, instante de corte e leitura consistente do total e da média. Agrupar pelo UUID da matéria, preservando registros separados mesmo com nomes iguais, e mostrar seu nome atual nessa leitura. Incluir somente matérias com duração positiva no período; se não houver estudo, exibir mensagem sem inventar registros de duração zero. Ordenar por duração decrescente, com desempates pelo nome sem distinção de maiúsculas, nome exato e UUID. Reutilizar os filtros, cache e regras de atualização existentes, sem consulta por matéria no tick. Renomeações e correções ou exclusões de estudos entram na próxima consulta. Em falha, manter total, média e lista anteriores juntos, com aviso; a consulta não grava dados. A divisão por tipo foi acrescentada na 0.20.0 e por assunto na 0.21.0; a comparação visual por matéria foi acrescentada na 0.22.0.
- **MVP 0.20.0:** mostrar o tempo efetivo por tipo de estudo no mesmo período, instante de corte e leitura consistente do total, média e matérias. Agrupar globalmente pelo texto exato persistido em cada sessão, reunindo o mesmo tipo entre matérias diferentes e preservando nomes distintos, inclusive diferenças de maiúsculas e caracteres Unicode, sem normalizar ou reescrever o histórico. Incluir tipos padrão e personalizados com duração positiva; na ausência de estudo, exibir mensagem sem criar linhas de duração zero. Ordenar por duração decrescente, com desempates pelo nome sem distinção de maiúsculas e nome exato. Reutilizar filtros, cache, atualização após gravações e proteção contra respostas tardias, sem consulta por tipo no tick. Correções e exclusões de estudos entram na próxima consulta. Em falha, manter juntos total, média e as duas listas anteriores, com aviso. A consulta não grava dados nem exige um catálogo independente de tipos. A divisão por assunto foi acrescentada na 0.21.0; a comparação visual por matéria foi acrescentada na 0.22.0.
- **MVP 0.21.0:** mostrar o tempo efetivo por assunto dentro de cada matéria, usando o mesmo período, instante de corte e leitura consistente do total, média e outras listas. Agrupar pelo UUID da matéria e pelo texto exato persistido do assunto, preservando diferenças de maiúsculas e Unicode, sem normalizar o histórico. Incluir estudos sem assunto como grupo próprio por matéria, mantendo a soma da distribuição igual ao total; ausência de assunto não se confunde com um texto literal. Exibir Assunto: nome ou Sem assunto informado, além do nome atual da matéria e identificação de homônimas. Incluir somente contribuições positivas, com mensagem quando não houver estudo. Ordenar por duração decrescente, nome da matéria sem distinção de maiúsculas, nome exato, UUID, ausência de assunto primeiro e nome do assunto sem distinção de maiúsculas e exato. Reutilizar filtros, rascunhos, cache e atualização após gravações, sem consulta por assunto no tick. Renomeações, correções de matéria/assunto e exclusões entram na próxima consulta. Atualizar total, média e as três listas juntos; falhas preservam todos os resultados anteriores com aviso. A consulta não grava dados nem exige cadastro independente de assuntos. A comparação visual por matéria foi acrescentada na 0.22.0; a gestão do catálogo permanece para depois.
- **MVP 0.22.0:** acrescentar barras horizontais em Tempo por matéria, usando como referência a maior duração positiva do período aplicado. Calcular a proporção com os segundos originais, incluindo frações, sem arredondar pela duração exibida. Manter nomes, durações, ordenação e identificação de matérias homônimas; explicar a referência em legenda e descrição acessível, distinguindo a comparação do progresso de uma meta. Um período sem estudo mantém a mensagem existente, sem barras nem divisão por zero. Reutilizar a mesma consulta, filtros, rascunhos, cache e atualização conjunta do total, média e três listas; falhas preservam todos os resultados anteriores com aviso. Não acrescentar consultas, métricas ou reconstrução das barras no tick. Outras visualizações permanecem para etapas posteriores.
- **MVP 0.23.0:** estender as barras às listas Tempo por tipo de estudo e Tempo por assunto, usando o maior tempo positivo da própria lista como referência, com segundos originais e frações. Manter rótulos, durações, ordem e descrições acessíveis; assuntos conservam a matéria, a identificação de homônimas e a distinção entre ausência e texto literal. Explicar cada referência na legenda, sem apresentar comparação como meta. Usar barras de largura comum abaixo dos rótulos e manter a mensagem sem barras em períodos vazios. Reutilizar a apresentação das três listas e as mesmas regras de consulta, cache e atualização conjunta, sem novas métricas ou reconstrução no tick.
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
