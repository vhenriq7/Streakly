# Streakly

Aplicativo pessoal de acompanhamento de estudos para **Windows e Android**, com registro de tempo, Pomodoro, metas diárias, ofensivas (streaks), calendário e estatísticas. Projeto **offline-first**: os dados são gravados localmente e sincronizados com a nuvem quando houver conexão.

> **Status:** início do MVP offline. A versão 0.18.0 iniciou Estatísticas com total e média diária por período; os 198 testes e a integração com controles Flet passaram. A etapa local 0.19.0 acrescenta o tempo por matéria ao mesmo período; os 206 testes e a integração com controles Flet passaram. Falta a conferência na janela do Windows. As provas de conceito anteriores validaram persistência no Windows/Android, cronômetro progressivo no Windows e exibição do aviso de foco concluído no Windows, este confirmado pelo usuário em 28/10/2026. Avisos Android e com o app fechado seguem pendentes, nos marcos combinados.

## Funcionalidades planejadas

- Painel inicial com **tempo total estudado hoje** (somatório de todas as matérias, não um cronômetro acionável), acesso às matérias e à sessão em andamento.
- Cronômetro progressivo e Pomodoro por matéria; configuração lembrada por matéria.
- Pomodoro 25/5, 50/10 e personalizado; pausas e transições iniciadas manualmente.
- Matérias, tipos de estudo (leitura, aula, exercícios, revisão e personalizados) e assuntos opcionais.
- Histórico de sessões com criação, edição e exclusão manual.
- Meta diária padrão, metas específicas por data, dias de descanso e ofensivas.
- Estatísticas por matéria, assunto, tipo de estudo e período; calendário de estudos.
- Notificações de ciclos, lembretes de estudo e aviso de meta pendente.
- Login por e-mail/senha ou Google, temas claro/escuro, sincronização automática/manual e exportação/importação de backups.

## Tecnologias previstas

| Camada | Tecnologia |
| --- | --- |
| Aplicativo Windows/Android | Python + Flet (a validar) |
| Banco local | SQLite |
| Banco na nuvem | PostgreSQL via Supabase |
| Autenticação | Supabase Auth |
| Repositório | GitHub |

A proposta é operar com **custo zero dentro dos limites dos planos gratuitos**, sem garantia de disponibilidade ilimitada do serviço em nuvem.

## Roadmap

1. **Planejamento:** requisitos, arquitetura e critérios de aceite.
2. **Prova de conceito:** empacotamento Android, persistência, retomada de cronômetro e notificações.
3. **MVP offline:** matérias, sessões, cronômetros, metas, histórico e estatísticas.
4. **Conta e sincronização:** autenticação, PostgreSQL, reconciliação e conflitos.
5. **Confiabilidade e distribuição:** backups, lembretes, testes e instaladores.

## Documentação

- [Requisitos e regras de negócio](docs/REQUIREMENTS.md)
- [Arquitetura e decisões técnicas](docs/ARCHITECTURE.md)

## Executar a prova de conceito

No PowerShell, dentro do projeto, usando o Python 3.14:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Se a `.venv` já existe, pule o primeiro comando. `requirements.txt` instala as ferramentas de desenvolvimento no Windows. `pyproject.toml` define as dependências do aplicativo empacotado, sem o cliente desktop no Android.

**Fluxo de testes combinado:** o desenvolvimento e a validação cotidiana serão feitos no Windows, executando `main.py` e os testes automatizados. Não gerar nem instalar um APK a cada mudança. Retomar os testes no Android em marcos maiores e nas mudanças que dependem da plataforma, como permissões, notificações, segundo plano, armazenamento e empacotamento.

Para gerar o APK de teste:

```powershell
.\.venv\Scripts\python.exe build_android.py
```

Esse script executa `flet build apk`. Quando o caminho do repositório contém acentos, copia apenas `main.py`, `storage.py`, `timer.py`, `notifications.py` e `pyproject.toml` para `%PUBLIC%\Documents\Streakly-Android-PoC\app`, evitando uma limitação do gerador nativo do Dart. O repositório, o banco e a `.venv` permanecem nos locais originais. Essa pasta auxiliar conserva os arquivos gerados para acelerar novas compilações; o APK final é copiado de volta para `build/apk/` no repositório.

O cache dos pacotes Dart (`PUB_CACHE`) fica em `build/pub-cache` dentro da pasta efetiva de compilação, pois os plugins nativos também precisam de caminhos sem acentos. Esse ajuste vale apenas para os processos iniciados pelo script; não muda as variáveis permanentes do Windows.

Neste computador, o Android SDK instalado pelo Flet foi realocado para `%PUBLIC%\Documents\Streakly-Android-PoC\android-sdk`, pois o CMake também apresentou incompatibilidade com o caminho do SDK contendo acentos. O script reconhece essa instalação e define `ANDROID_HOME` apenas durante a compilação.

Somente se ocorrer o erro conhecido de leitura do caminho do Flutter com acentos, o script corrige os escapes de `local.properties` e continua pelo Gradle. Ele mantém as variáveis necessárias para incluir o código e as dependências Python no APK. Essa adaptação foi feita para Windows com Flet/CLI 1.0.5; erros diferentes são exibidos sem serem ignorados. O script não faz parte do aplicativo instalado.

A primeira compilação baixa Flutter, JDK e Android SDK caso estejam ausentes; requer internet e espaço em disco. O APK é gerado em `build/apk/`. Não é necessário instalar Android Studio nem publicar na Play Store.

O APK 0.2.0 foi gerado e usa a mesma assinatura de teste da versão 0.1.0, permitindo atualizar a instalação existente. Foram verificados a integridade dos arquivos, a assinatura, os três módulos Python e a inclusão de SQLite nas três arquiteturas (`arm64-v8a`, `armeabi-v7a` e `x86_64`). Ele não contém bancos, testes nem ferramentas de desenvolvimento. A versão 0.1.0 teve instalação, execução e persistência offline confirmadas pelo usuário no Moto G84 5G em 28/10/2026; o teste do cronômetro 0.2.0 no aparelho permanece pendente. As demais arquiteturas não foram testadas em aparelhos.

Se o Flutter informar `Building with plugins requires symlink support`, é necessário habilitar o Modo de Desenvolvedor do Windows para permitir links simbólicos. Abra `ms-settings:developers` com `Win+R` e ative essa opção antes de repetir a compilação.

O SQLite usa `%LOCALAPPDATA%\Streakly\poc.sqlite3` no Windows e `FLET_APP_STORAGE_DATA/poc.sqlite3` no Android (pasta privada de dados fornecida pelo Flet). Cada dispositivo tem seu próprio banco; ainda não há sincronização. O arquivo não deve ficar em cache nem ser incluído no APK.

No celular, instale o APK, cadastre uma matéria, feche o aplicativo, ative o modo avião e reabra. A matéria deve continuar disponível. Repita após forçar a parada pelas configurações do Android. Não desinstale nem limpe os dados durante esse teste: essas ações removem normalmente o banco local.

### Cronômetro progressivo — versão 0.2.0

Em **Cronômetro**, escolha matéria, tipo de estudo e assunto opcional. Use **Iniciar**, **Pausar**, **Retomar** e **Finalizar**. A navegação para **Matérias** preserva a sessão. Uma sessão pausada também impede iniciar outra.

Conforme escolha do usuário, fechar o app com uma sessão rodando mantém a contagem até pausar ou finalizar. Ao reabrir, o app recupera a mesma sessão pelos eventos UTC no SQLite. Se estiver pausada, o período fechado não conta. A tarefa de atualização da tela não precisa sobreviver ao fechamento.

`timer.py` contém os modelos e o cálculo dos intervalos; `storage.py` grava eventos e estado juntos em transação; `main.py` cuida da interface. A tela atualiza a cada segundo enquanto visível, com consultas ao banco; as gravações ocorrem nas ações. A migração do esquema 1 para 2 adiciona tabelas e preserva as matérias existentes. Os tipos de estudo e o assunto ficam como texto nesta PoC; o catálogo completo fica para depois. A listagem do histórico foi acrescentada na versão 0.7.0.

Para executar os testes, que usam apenas bancos temporários:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes cobrem recuperação em outro processo, pausas, sessão única, concorrência, rollback em falha, migração e duração atravessando meia-noite. O rateio por dia foi acrescentado no painel 0.6.0; metas e estatísticas completas continuam nas etapas seguintes. O cálculo depende da hora correta do dispositivo; transições anteriores ao último evento são recusadas, mas a política completa para alterações manuais do relógio ainda não foi implementada.

Para validar o cronômetro agora, execute `main.py` no Windows, inicie uma sessão, navegue entre **Início**, **Matérias**, **Cronômetro** e **Histórico**, feche e reabra. Depois pause, feche novamente e confirme que o tempo permanece parado. Retome e finalize para conferir a duração sem as pausas. Nos marcos de validação Android, repetir esse roteiro atualizando o APK existente. As sessões finalizadas ficam disponíveis em **Histórico**.

### Pomodoro básico — versão 0.3.0

Em **Cronômetro**, escolha a matéria, o modo **Pomodoro** e um preset: **25 min foco / 5 min pausa** ou **50/10**. O último modo e preset efetivamente iniciados são lembrados por matéria. A sessão em andamento conserva sua configuração até ser finalizada.

A contagem principal mostra o tempo restante da fase; **Tempo de estudo nesta sessão** soma somente os intervalos de foco. **Pausar** e **Retomar** funcionam durante foco ou pausa. Ao terminar a fase, o aplicativo mostra um aviso na tela e aguarda **Iniciar pausa** ou **Iniciar foco**. O período de espera e a pausa Pomodoro não contam como estudo. A sessão continua ocupando a única vaga de sessão ativa enquanto espera.

Se o app for fechado durante uma fase, o horário previsto de término permanece salvo. Ao reabrir depois desse prazo, o foco é encerrado exatamente no limite e a próxima fase aguarda comando; nenhum ciclo começa sozinho. Se a contagem tiver sido pausada manualmente, o tempo restante fica congelado até retomar.

O esquema 3 adiciona estado do Pomodoro e preferências por matéria, preservando matérias, sessões e eventos anteriores. Além das ações manuais, o banco registra a conclusão de uma fase quando o app observa o prazo vencido; o horário gravado é o prazo original. Os testes simulam a passagem do tempo para verificar ciclos completos sem aguardar 25 minutos. Esta etapa usa testes no Windows e não gera um novo APK.

### Pomodoro personalizado e pausa longa — versão 0.4.0

Em **Preset Pomodoro → Personalizado**, informe minutos inteiros positivos para foco, pausa curta e pausa longa, além da quantidade de focos até a pausa longa. Os presets 25/5 e 50/10 usam pausa longa de **15 minutos a cada 4 focos completos**; escolha Personalizado para alterar esses valores. A configuração é salva ao iniciar uma sessão com sucesso.

Somente completar um foco aumenta o contador. Após cada múltiplo da quantidade escolhida, a próxima pausa será longa; nos demais casos, curta. **Iniciar pausa longa** continua sendo manual. O contador sobrevive ao fechamento do app e começa em zero em uma nova sessão. Pausas manuais, intervalos e espera não aumentam o contador nem o tempo de estudo.

O esquema 4 acrescenta as configurações e o contador sem recriar sessões ou eventos. A migração recupera os focos completos das sessões antigas e mantém a fase e o tempo restante já salvos; uma pausa antiga em andamento ou aguardando início continua curta.

Para conferir na janela do Windows, finalize a sessão anterior e escolha **Personalizado**: foco 1 minuto, pausa curta 1, pausa longa 2 e quantidade 2. Após o primeiro foco, inicie a pausa curta; após o segundo, deve aparecer **Iniciar pausa longa**, com 2 minutos de estudo acumulados. Feche e reabra durante a espera ou com a contagem pausada para conferir a recuperação. Os testes automatizados verificam esses mesmos ciclos com relógio simulado e bancos temporários. Nenhuma dependência nova ou APK é necessário nesta etapa.

### Avisos Pomodoro no Windows — versão 0.5.0

No modo **Pomodoro**, use **Testar aviso do Windows** para conferir a exibição sem iniciar ou alterar uma sessão. Ao completar um foco, uma pausa curta ou uma pausa longa, o aplicativo solicita um aviso do Windows com a próxima ação manual. O cronômetro continua aguardando seu comando.

O aplicativo precisa permanecer aberto; pode ficar minimizado. Nessa condição, ele continua verificando o prazo, sem enviar atualizações visuais à janela oculta. Ao fechar o app, a verificação é encerrada. Reabrir uma fase já concluída recupera a mensagem na tela, sem disparar um aviso atrasado. Cada conclusão gera no máximo uma tentativa de envio por instância aberta, inclusive se o envio falhar. A deduplicação não é compartilhada entre duas instâncias do aplicativo.

O envio usa Windows PowerShell e `System.Windows.Forms.NotifyIcon`, componentes do Windows, sem dependências Python adicionais. Um ícone temporário permanece na área de notificações por cerca de 12 segundos e é removido ao terminar. Um envio já iniciado pode terminar após fechar o app. A exibição e a duração do aviso dependem das configurações de notificações do Windows; sucesso no envio não comprova que o usuário viu o aviso. [Referência Microsoft](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.showballoontip?view=windowsdesktop-10.0).

`notifications.py` detecta conclusões e faz o envio; `main.py` o executa com `asyncio.to_thread`, para manter a interface responsiva. Não há alteração no esquema 4 do banco. Falhas no aviso mantêm o registro da sessão e a mensagem na tela.

Para validar, execute `main.py`, escolha Pomodoro e clique em **Testar aviso do Windows**. Depois inicie um foco personalizado de 1 minuto e minimize a janela. Ao terminar, confira o aviso e restaure a janela: a próxima fase deve continuar aguardando início manual. Os testes automatizados usam envio simulado e bancos temporários, cobrindo janela oculta, reabertura, reconexão e falha no envio. O usuário confirmou em 28/10/2026 a exibição do aviso de foco concluído para a matéria Python, oferecendo início manual da pausa curta. O aviso aparece com o cabeçalho Windows PowerShell nesta PoC; identidade própria do Streakly fica para o empacotamento. O roteiro acima permanece para conferir as demais fases e a janela minimizada. Android mantém o aviso na tela nesta versão; não foi gerado APK novo.

### Painel diário — versão 0.6.0

O app abre em **Início**, com a data e o **Tempo estudado hoje** somando todas as matérias, sessões finalizadas e o trecho de estudo da sessão ativa. Esse total é informativo. Os atalhos das matérias abrem o cronômetro com a matéria escolhida; **Abrir sessão em andamento** recupera a sessão ativa, inclusive pausada ou aguardando a próxima fase. Trocar de tela preserva a sessão.

O total considera o dia local configurado no dispositivo. Um estudo das 23h50 às 00h10 contribui com 10 minutos para cada dia. Pausas manuais, intervalos Pomodoro e espera não contam. Ao virar o dia, somente a exibição diária começa de zero; sessões e eventos permanecem salvos. A contagem Pomodoro fica limitada ao prazo do foco, mesmo ao reabrir o app depois dele. Mudar o fuso do dispositivo recalcula o dia exibido; a política entre dispositivos ficará para a sincronização.

O esquema SQLite continua na versão 4. O painel calcula o total a partir dos eventos existentes, sem gravar um contador diário nem instalar dependências. Os testes usam horários simulados e verificam inclusive fronteiras de meia-noite e janelas de 23/25 horas. A integração com controles Flet passou; a conferência na janela nativa ainda está pendente. Nenhum APK foi gerado nesta etapa.

Para conferir no Windows, inicie uma sessão progressiva e volte a **Início**: o total deve crescer. Pause pelo cronômetro e volte: o total deve ficar parado. Finalize e confirme que o acumulado permanece; depois estude outra matéria para verificar a soma. Feche e reabra para conferir a recuperação da sessão e do total.

### Histórico de sessões — versão 0.7.0

Em **Histórico**, consulte as sessões finalizadas, da data de início mais recente para a mais antiga. Cada registro mostra matéria, tipo, assunto quando informado, modo, início, fim e duração efetiva de estudo. Os horários usam o fuso local do dispositivo, com seu deslocamento UTC entre parênteses. A duração exclui pausas manuais, intervalos Pomodoro e espera entre fases.

A lista carrega 25 sessões por página. Use **Mais antigas** e **Mais recentes** para navegar; **Atualizar** e reabrir a tela voltam à primeira página. A consulta ocorre apenas nessas ações, sem recarregar o histórico a cada segundo. Se outra instância finalizar uma sessão, use Atualizar para vê-la. Após erro de leitura, a lista e a posição anteriores são mantidas, com uma mensagem de erro.

A consulta não grava dados nem altera a sessão ativa. Uma sessão rodando, pausada ou aguardando a próxima fase entra no histórico somente depois de **Finalizar**. A contagem e os avisos continuam funcionando enquanto você consulta a lista. A etapa 0.7.0 manteve o esquema 4, sem dependências novas nem APK. O cadastro manual foi acrescentado na 0.8.0, a edição na 0.9.0 e a exclusão na 0.10.0, descritas abaixo.

Para conferir no Windows, abra Histórico e verifique as sessões já salvas. Inicie uma sessão, pause e consulte a lista: ela ainda não deve aparecer. Finalize, reabra o Histórico e confira matéria, assunto e tempo de estudo. Feche e reabra o app para confirmar a persistência. Os 59 testes e a integração com controles Flet passaram; falta a conferência do usuário na janela nativa.

### Cadastro manual — versão 0.8.0

Em **Histórico → Adicionar sessão**, escolha matéria, tipo e assunto opcional. Informe a data como **DD/MM/AAAA**, o início como **HH:MM** e a duração estudada como **HH:MM** (por exemplo, 01:30 para uma hora e meia). O fim calculado aparece antes de salvar. Essa forma de entrada foi escolhida pelo usuário em 29/10/2026.

Cada lançamento representa um período contínuo, sem pausas; registre blocos separados quando necessário. O início usa o fuso local do dispositivo e a duração é somada em UTC. O período deve ter terminado até agora. Horários locais inexistentes ou ambíguos e sobreposição com estudo já registrado são recusados. Intervalos adjacentes são permitidos; pausas e espera Pomodoro não ocupam tempo de estudo nessa verificação.

Ao salvar, o registro já entra finalizado no Histórico como **Registro manual** e o total diário é atualizado, inclusive repartindo o período pela meia-noite. O cronômetro e as preferências por matéria são preservados. **Cancelar** descarta o formulário sem gravar; navegar entre telas preserva o preenchimento enquanto o app estiver aberto.

O esquema 5 acrescenta a origem do registro e identifica as sessões anteriores como feitas pelo cronômetro, preservando dados e identificadores. Sessão e dois eventos são gravados juntos em transação. Os 72 testes e a integração com controles Flet passaram, incluindo falhas antes/depois da gravação e recusa de duplicação. Sem dependências novas ou APK nesta etapa. A edição foi acrescentada na 0.9.0 e a exclusão na 0.10.0.

Para conferir, registre um período passado que ainda não conste nos seus estudos, confira **Registro manual** no Histórico e o total em Início. Feche e reabra para confirmar a persistência. Falta essa conferência na janela nativa do Windows.

### Edição no Histórico — versão 0.9.0

Use **Editar** em uma sessão finalizada para corrigir matéria, tipo e assunto. Em um **Registro manual**, também é possível ajustar início e duração em HH:MM, conferindo o fim calculado antes de **Salvar alterações**. A correção do período atualiza o total diário e mantém as verificações de fim futuro e sobreposição.

Para sessões feitas pelo cronômetro, o formulário mostra o período registrado e permite corrigir a classificação. Horários, pausas e ciclos permanecem preservados; editar esses intervalos fica para uma etapa futura. Salvar somente matéria, tipo ou assunto mantém os segundos e frações originais, mesmo quando o início e a duração de um registro manual são mostrados em horas e minutos. A origem e os identificadores permanecem iguais.

Se outra instância alterar a sessão depois de ela ser carregada, o app recusa sobrescrevê-la. Cancele a edição, atualize o Histórico e confira os dados atuais antes de tentar novamente. Erros antes da gravação preservam o formulário; erros na atualização da tela após gravar informam que as alterações já foram salvas.

Os 84 testes e a integração com controles Flet passaram, incluindo preservação de eventos, precisão, conflitos, rollback e reabertura. O esquema continua na versão 5, sem dependências novas ou APK. Para conferir no Windows, corrija o assunto de uma sessão e verifique que sua duração permanece igual; em um registro manual, ajuste um período real e confira o total em Início. Falta essa validação na janela nativa.

### Exclusão no Histórico — versão 0.10.0

Use **Excluir** em uma sessão finalizada. A confirmação mostra matéria, tipo, assunto quando informado, origem, início, fim e tempo de estudo. **Cancelar** fecha a confirmação sem gravar. Somente **Excluir definitivamente** remove a sessão e seus eventos; a exclusão é permanente nesta etapa, sem lixeira ou desfazer.

Registros manuais, progressivos e Pomodoro finalizados podem ser excluídos. Sessões ativas, pausadas ou aguardando outra fase permanecem protegidas. O Histórico volta à primeira página e o total diário é recalculado após sucesso. Matérias, preferências, outras sessões e o cronômetro ativo são preservados. Se o registro mudou em outra instância desde a leitura, a exclusão é recusada: cancele, atualize o Histórico e confira os dados atuais.

Uma falha antes da gravação mantém a confirmação com erro; falha ao atualizar a tela depois da exclusão informa que a sessão já foi excluída. Fechar uma confirmação ou desconectar antes de confirmar não executa a exclusão. A operação já confirmada pode terminar se o app for fechado durante a gravação.

Os 93 testes e a integração com controles Flet passaram, incluindo cancelamento, conflitos, rollback e reconexão. O esquema permanece na versão 5; não há dependências novas nem APK. Para conferir no Windows, abra a confirmação e use primeiro Cancelar para verificar que a sessão permanece. Confirme a exclusão apenas em uma sessão que deseja remover e confira a lista e o total em Início. Falta essa conferência na janela nativa.

### Renomear matérias — versão 0.11.0

Em **Matérias → Renomear**, altere o nome e use **Salvar nome**. Espaços nas extremidades são removidos e nomes vazios são recusados. **Cancelar renomeação** não grava; navegar entre telas preserva o preenchimento. O novo nome aparece na lista, nos atalhos, nas opções de matéria, na sessão ativa e no Histórico já carregado, mantendo sua página. Nomes iguais continuam representando matérias separadas.

A alteração preserva identificadores, sessões, eventos, tempo de estudo e preferências. Se outra instância tiver renomeado a matéria, a gravação é recusada: cancele, confira a lista atualizada e reabra Renomear. Uma edição de sessão aberta antes da renomeação também deve ser cancelada e reaberta para carregar os dados atuais. Erros antes de gravar mantêm o formulário; erros posteriores informam que o nome já foi alterado.

O esquema permanece na versão 5, sem dependências novas ou APK. Os 101 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. Para conferir no Windows, renomeie uma matéria com estudos, verifique o Histórico e o cronômetro, feche e reabra o app. Falta essa conferência na janela nativa. A exclusão de matérias foi acrescentada na versão 0.12.0, descrita abaixo.

### Exclusão de matérias — versão 0.12.0

Conforme escolha do usuário em **29/10/2026**, **Matérias → Excluir** remove permanentemente a matéria, todos os seus estudos, eventos e preferências. A confirmação mostra o nome, a quantidade de sessões e o tempo de estudo que será removido. **Cancelar** ou fechar o diálogo não grava; somente **Excluir definitivamente** executa a remoção. Não há lixeira ou desfazer nesta etapa.

Uma matéria com sessão ativa exige **Finalizar** antes de excluir, inclusive se estiver pausada ou aguardando outra fase Pomodoro. Se o nome ou os estudos mudarem após abrir a confirmação, a exclusão é recusada: cancele e abra novamente para conferir os dados atuais. A leitura da confirmação não altera o banco. Após sucesso, o Histórico volta à primeira página e os totais são recalculados; outras matérias e sua sessão ativa permanecem preservadas. Falha na atualização da tela depois da gravação informa que a exclusão já ocorreu.

Se a matéria selecionada em um rascunho tiver sido excluída, o app mantém os demais campos e pede outra matéria, sem escolher automaticamente. Uma edição de sessão removida mantém o preenchimento, mas bloqueia salvar: cancele e atualize o Histórico. Desconectar invalida confirmações ainda não aceitas e leituras de confirmação pendentes.

O esquema continua na versão 5, sem dependências novas ou APK. Os 112 testes e a integração com controles Flet passaram, usando bancos temporários e sem excluir dados pessoais. Para conferir no Windows, abra a confirmação de uma matéria e use primeiro Cancelar. Confirme a exclusão somente em uma matéria cujos estudos realmente deseja remover; confira a lista, o Histórico e o total em Início, depois feche e reabra o app. Falta essa conferência na janela nativa.

### Tipos de estudo personalizados — versão 0.13.0

Em **Cronômetro → Tipo de estudo** ou no formulário de **Adicionar sessão/Editar** do Histórico, escolha **Personalizado…** e informe um nome, como Projeto prático. Espaços nas extremidades do novo nome são removidos e nomes vazios são recusados. O tipo é salvo ao iniciar o cronômetro ou salvar o registro; corrigir apenas essa classificação preserva horários, eventos, duração e origem.

As opções combinam Leitura, Aula, Exercícios e Revisão com os nomes já usados nos estudos, inclusive de outras matérias. O novo nome passa a ser reutilizável depois do primeiro registro e permanece após reabrir o app. Se seu último uso for excluído ou alterado, pode sair das sugestões na próxima atualização; uma seleção ou rascunho em andamento é preservado. Não há cadastro independente de tipos nesta etapa; gerenciamento de catálogo e personalização por matéria ficam para depois.

Navegar entre telas preserva o preenchimento. O tipo da sessão ativa permanece bloqueado junto dos demais campos. Erros anteriores à gravação mantêm o formulário; erros posteriores informam que o registro já foi salvo. O esquema SQLite continua na versão 5, sem dependências novas ou APK. Os 120 testes e a integração com controles Flet passaram, usando bancos temporários. Para conferir no Windows, registre um tipo personalizado, veja-o no Histórico e reabra o app para reutilizá-lo. Falta essa conferência na janela nativa.

### Meta diária padrão — versão 0.14.0

Em **Configurações**, defina uma duração positiva em **HH:MM**, como 03:00 para três horas. Nenhuma meta é imposta antes da primeira configuração. O **Início** mostra a meta de hoje, a barra de progresso e o tempo restante ou **Meta atingida**, usando o total de estudo já calculado, sem contabilizar pausas. O cronômetro e os registros de estudo permanecem preservados.

Conforme escolha do usuário em **29/10/2026**, uma mudança vale a partir de hoje e mantém as metas dos dias anteriores, inclusive quando o app ficou fechado. Navegar entre telas preserva o rascunho; cancelar não grava. Se o dia mudar ou outra instância alterar a meta durante a edição, cancele e reabra o formulário para conferir a configuração atual. Erros anteriores à gravação mantêm o preenchimento; erros posteriores informam que a meta já foi salva.

O esquema 6 acrescenta o histórico de vigência da meta, sem modificar sessões ou eventos existentes. Os 134 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. Para conferir no Windows, salve uma meta, veja o progresso no Início e reabra o app para confirmar a persistência. Falta essa conferência na janela nativa. Metas específicas por data e descanso foram acrescentados na 0.15.0, ofensivas na 0.16.0 e a grade mensal na 0.17.0. Não há dependências novas nem APK nesta etapa.

### Metas por data e descanso — versão 0.15.0

Em **Calendário**, informe uma data como **DD/MM/AAAA** e use **Consultar data**. Escolha entre usar a meta padrão vigente naquele dia, definir uma meta específica positiva em **HH:MM** ou marcar descanso. A configuração pode ser feita explicitamente para uma data passada ou futura e vale somente nela, sem alterar a vigência da meta padrão. Voltar a **Usar meta padrão** remove a exceção após confirmação; **Cancelar planejamento** não grava.

O **Início** usa a exceção de hoje quando houver uma. Em descanso, mostra essa indicação sem barra de meta; qualquer estudo realizado continua no total diário e no Histórico. **Configurações** mantém separada a meta padrão vigente. O cálculo de ofensivas foi acrescentado na 0.16.0 e a grade mensal na 0.17.0.

Mudar a data digitada exige consultar novamente antes de salvar. Navegar preserva o rascunho; alterações concorrentes são recusadas e falhas após gravar informam que a configuração já foi salva. O esquema 7 acrescenta as exceções por data, preservando metas padrão e estudos existentes. Os 151 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. Para conferir no Windows, configure uma exceção para hoje, veja o Início, experimente Cancelar e reabra o app. Falta essa conferência na janela nativa; não há dependências novas nem APK.

### Ofensiva atual e recorde — versão 0.16.0

O **Início** mostra a ofensiva atual com uma chama e seu recorde. Cada dia em que a meta aplicável é atingida acrescenta um dia à sequência. Um dia encerrado abaixo da meta interrompe a ofensiva; hoje, enquanto ainda estiver aberto e pendente, conserva a sequência anterior. Descanso e dias sem meta não aumentam nem quebram a sequência. Datas futuras não entram no cálculo.

O cálculo usa o tempo efetivo dos eventos, repartido pelo dia local, e respeita pausas e o limite de foco Pomodoro. Corrigir ou excluir estudos e alterar o planejamento de uma data recalcula a ofensiva e o recorde, sem gravar um contador separado. A atualização ocorre na abertura, ao entrar em Início, após gravações e ao detectar mudança de dia ou fuso. Use **Atualizar ofensiva** para incorporar alterações feitas em outra instância ou uma mudança de fuso ainda não detectada; reabrir o app também refaz o cálculo.

O esquema permanece na versão 7, sem dependências novas ou APK. Os 170 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. Para conferir no Windows, veja o Início antes e depois de atingir a meta de hoje, consulte um dia de descanso e reabra o app. Falta essa conferência na janela nativa. A grade mensal foi acrescentada na 0.17.0; as estatísticas completas ficam para uma próxima etapa.

### Calendário mensal — versão 0.17.0

Em **Calendário**, a grade de segunda a domingo mostra o tempo estudado e a situação de cada dia. Navegue entre os meses ou use **Mês atual** e **Atualizar mês**. Textos, cores e detalhes dos dias distinguem meta atingida, dia encerrado abaixo da meta, hoje em andamento, descanso, ausência de meta e planejamento futuro. Datas futuras não são tratadas como metas descumpridas.

A primeira abertura carrega o mês sem abrir o editor. Clicar em um dia consulta seu planejamento, sem gravar. Navegar entre meses preserva o formulário e a data em edição; clicar novamente no mesmo dia mantém o rascunho. Para selecionar outro dia enquanto edita, use primeiro **Cancelar planejamento**. A consulta digitada por data continua disponível.

A grade representa a consulta mensal mais recente. Ela é carregada ao abrir, navegar ou atualizar o mês, e gravações atualizam o mês visível. Use Atualizar mês ou reabra Calendário para incorporar tempo recente e alterações de outra instância. Falhas mantêm a grade e o mês anteriores com aviso. Os 186 testes automatizados e a integração com controles Flet passaram, usando bancos temporários, incluindo navegação, rascunhos, falhas de leitura e gravação de meta em data limite. Para conferir no Windows, navegue entre meses, selecione um dia e confirme que cancelar preserva seus dados. Falta essa conferência na janela nativa. O esquema permanece na versão 7, sem dependências novas ou APK; as estatísticas completas ficam para depois.

### Estatísticas por período — versão 0.18.0

Em **Estatísticas**, consulte o tempo total e a média diária com os filtros **Hoje**, **Semana atual** (segunda-feira até hoje), **Mês atual** (dia 1 até hoje), **Ano atual** (1º de janeiro até hoje) e **Personalizado**. Neste último, informe início e fim em **DD/MM/AAAA**; as duas datas entram no período e o início não pode ser posterior ao fim.

A média divide o tempo pelo número de dias do intervalo, incluindo dias sem estudo, descanso e ausência de meta. Um intervalo personalizado pode conter datas futuras: esses dias entram no divisor com estudo zero, e o tempo contabilizado termina no instante da consulta. Por exemplo, três horas em um intervalo de três dias resultam em uma hora diária, mesmo que todo o estudo tenha ocorrido em um único dia. O cálculo usa os dias do fuso local, exclui pausas e espera e respeita o prazo do foco Pomodoro.

Os indicadores mostram a consulta mais recente e seu período aplicado. **Aplicar período** consulta os filtros preenchidos; **Atualizar estatísticas** e reabrir a tela recarregam o período aplicado, preservando o rascunho. Gravações relevantes atualizam os resultados quando a tela está visível; não há consulta de estatísticas a cada segundo. Navegar preserva o preenchimento personalizado, inclusive parcial. Falhas mantêm o período e os resultados anteriores com aviso.

Os 198 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. Falta a conferência na janela nativa do Windows. Para conferir, abra Estatísticas, compare Hoje com Mês atual e aplique um intervalo personalizado que inclua um dia sem estudo. A consulta não altera o banco; o esquema continua na versão 7, sem dependências novas ou APK. A divisão por matéria foi acrescentada na 0.19.0; divisões por tipo e assunto e gráficos ficam para as próximas etapas.

### Tempo por matéria — versão 0.19.0

Em **Estatísticas**, a lista **Tempo por matéria** mostra as matérias com estudo efetivo no período aplicado, da maior duração para a menor. Nomes iguais continuam sendo matérias distintas; nesses casos, a lista acrescenta uma identificação para distingui-las. Empates têm ordenação determinística pelo nome e identificador. Um período sem estudo mostra uma mensagem, sem preencher a lista com matérias de duração zero.

A lista, o total e a média usam a mesma consulta e o mesmo instante de corte. As durações seguem as regras já existentes de dia local, pausas, espera e foco Pomodoro. Renomear uma matéria muda o nome exibido na próxima consulta; corrigir a matéria de uma sessão ou excluir estudos recalcula a distribuição. Os filtros, rascunhos e botões Aplicar período/Atualizar estatísticas continuam compartilhados; uma falha mantém juntos os resultados e a lista anteriores, com aviso. Não há consulta por matéria a cada segundo.

Os 206 testes automatizados e a integração com controles Flet passaram, usando bancos temporários. A integração verificou nomes iguais, filtros, ausência de estudo, renomeação durante navegação, correções de matéria, exclusões, falhas e reabertura. Falta a conferência na janela nativa do Windows: abra Estatísticas, compare a lista em Hoje e Mês atual e aplique um intervalo personalizado sem estudo. O esquema permanece na versão 7, sem dependências novas ou APK. Divisões por tipo e assunto e gráficos ficam para as próximas etapas.

## Princípios de desenvolvimento

- Não implementar funcionalidades sem consultar os requisitos e registrar decisões novas.
- Priorizar **integridade dos dados**, uso offline e interface simples.
- Evitar dependências pagas e complexidade desnecessária.
- Não armazenar segredos administrativos no aplicativo distribuído.
- Validar restrições reais de Android antes de prometer execução em segundo plano.

**Uso pretendido:** pessoal, em dispositivos do mesmo titular; não há plano de multiusuário público.
