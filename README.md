# Streakly

Aplicativo pessoal de acompanhamento de estudos para **Windows e Android**, com registro de tempo, Pomodoro, metas diárias, ofensivas (streaks), calendário e estatísticas. Projeto **offline-first**: os dados são gravados localmente e sincronizados com a nuvem quando houver conexão.

> **Status:** prova de conceito. A base 0.1.0 foi validada no Windows e no Moto G84 5G. A versão 0.2.0 acrescenta cronômetro progressivo, recuperação da sessão e migração local preservando as matérias; testes automatizados passaram. A confirmação da nova interface e do cronômetro no aparelho ainda está pendente. Pomodoro e notificações seguem para etapas posteriores.

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

Para validar no aparelho, atualize o APK existente, inicie uma sessão, alterne entre as duas telas, feche e reabra. Depois pause, feche novamente e confirme que o tempo permanece parado. Retome e finalize para conferir a duração sem as pausas. As sessões finalizadas ficam no banco; a tela de histórico ainda não faz parte desta etapa.

## Princípios de desenvolvimento

- Não implementar funcionalidades sem consultar os requisitos e registrar decisões novas.
- Priorizar **integridade dos dados**, uso offline e interface simples.
- Evitar dependências pagas e complexidade desnecessária.
- Não armazenar segredos administrativos no aplicativo distribuído.
- Validar restrições reais de Android antes de prometer execução em segundo plano.

**Uso pretendido:** pessoal, em dispositivos do mesmo titular; não há plano de multiusuário público.
