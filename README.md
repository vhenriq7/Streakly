# Streakly

Aplicativo pessoal de acompanhamento de estudos para **Windows e Android**, com registro de tempo, Pomodoro, metas diárias, ofensivas (streaks), calendário e estatísticas. Projeto **offline-first**: os dados são gravados localmente e sincronizados com a nuvem quando houver conexão.

> **Status:** planejamento. Ainda não há implementação. Stack sujeita a testes de viabilidade, especialmente no Android.

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

- [Requisitos e regras de negócio](REQUIREMENTS.md)
- [Arquitetura e decisões técnicas](ARCHITECTURE.md)

## Princípios de desenvolvimento

- Não implementar funcionalidades sem consultar os requisitos e registrar decisões novas.
- Priorizar **integridade dos dados**, uso offline e interface simples.
- Evitar dependências pagas e complexidade desnecessária.
- Não armazenar segredos administrativos no aplicativo distribuído.
- Validar restrições reais de Android antes de prometer execução em segundo plano.

**Uso pretendido:** pessoal, em dispositivos do mesmo titular; não há plano de multiusuário público.
