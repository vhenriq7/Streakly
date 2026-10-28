# Streakly

Aplicativo pessoal de acompanhamento de estudos para **Windows e Android**, com registro de tempo, Pomodoro, metas diárias, ofensivas (streaks), calendário e estatísticas. Projeto **offline-first**: os dados são gravados localmente e sincronizados com a nuvem quando houver conexão.

> **Status:** prova de conceito. A base 0.1.0 foi validada no Windows e no Moto G84 5G, e o cronômetro progressivo 0.2.0 foi confirmado no Windows. A versão 0.4.0 amplia o Pomodoro 0.3.0 com tempos personalizados e pausa longa a cada N focos completos, mantendo início manual e preferências por matéria. Os 29 testes automatizados e a integração com controles Flet passaram; falta a conferência do usuário na janela do Windows. Notificações do sistema ficam para uma etapa seguinte. Android segue reservado aos marcos combinados.

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

Esse script executa `flet build apk`. Quando o caminho do repositório contém acentos, copia apenas `main.py`, `storage.py`, `timer.py` e `pyproject.toml` para `%PUBLIC%\Documents\Streakly-Android-PoC\app`, evitando uma limitação do gerador nativo do Dart. O repositório, o banco e a `.venv` permanecem nos locais originais. Essa pasta auxiliar conserva os arquivos gerados para acelerar novas compilações; o APK final é copiado de volta para `build/apk/` no repositório.

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

`timer.py` contém os modelos e o cálculo dos intervalos; `storage.py` grava eventos e estado juntos em transação; `main.py` cuida da interface. A tela atualiza a cada segundo enquanto visível, com consultas ao banco; as gravações ocorrem nas ações. A migração do esquema 1 para 2 adiciona tabelas e preserva as matérias existentes. Os tipos de estudo e o assunto ficam como texto nesta PoC; o catálogo completo e a interface de histórico serão tratados nas etapas seguintes.

Para executar os testes, que usam apenas bancos temporários:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes cobrem recuperação em outro processo, pausas, sessão única, concorrência, rollback em falha, migração e duração atravessando meia-noite. A distribuição do tempo por dia para metas e estatísticas será implementada com essas funcionalidades. O cálculo depende da hora correta do dispositivo; transições anteriores ao último evento são recusadas, mas a política completa para alterações manuais do relógio ainda não foi implementada.

Para validar o cronômetro agora, execute `main.py` no Windows, inicie uma sessão, alterne entre as duas telas, feche e reabra. Depois pause, feche novamente e confirme que o tempo permanece parado. Retome e finalize para conferir a duração sem as pausas. Nos marcos de validação Android, repetir esse roteiro atualizando o APK existente. As sessões finalizadas ficam no banco; a tela de histórico ainda não faz parte desta etapa.

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

## Princípios de desenvolvimento

- Não implementar funcionalidades sem consultar os requisitos e registrar decisões novas.
- Priorizar **integridade dos dados**, uso offline e interface simples.
- Evitar dependências pagas e complexidade desnecessária.
- Não armazenar segredos administrativos no aplicativo distribuído.
- Validar restrições reais de Android antes de prometer execução em segundo plano.

**Uso pretendido:** pessoal, em dispositivos do mesmo titular; não há plano de multiusuário público.
