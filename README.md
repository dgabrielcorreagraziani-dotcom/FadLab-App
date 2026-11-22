# FadLab-App

Aplicação para organizar ministérios, cultos e equipes de Louvor, Mídia e Sonoplastia. A interface é mobile-first e pode ser instalada como PWA.

## Executar localmente

Use Python 3.12. Em Linux e macOS, ative a venv com `source .venv/bin/activate`. No PowerShell, use `.\.venv\Scripts\Activate.ps1`.

```sh
python -m venv .venv
python -m pip install -r requirements-dev.txt
python -m flask --app app db upgrade
python -m flask --app app create-admin
python -m flask --app app run
```

Abra [http://127.0.0.1:5000](http://127.0.0.1:5000). O primeiro administrador informa a própria senha no terminal, com entrada oculta. O ambiente local usa SQLite por padrão. Defina `SECRET_KEY` no ambiente para manter sessões ao reiniciar o servidor.

## Primeiro uso

1. Crie um ministério e habilite temporariamente a criação de contas em **Administração**.
2. Confirme com cada pessoa qual conta ela criou antes de acrescentá-la ao ministério. Depois do cadastro, cada conta tem o papel USER e começa sem acesso a ministérios.
3. Atribua o papel COORDINATOR e habilite a coordenação somente nos ministérios que a pessoa gerencia.
4. Em **Agenda**, crie um evento. Em **Escalas**, selecione o evento, atualize as opções e escolha participante e função. A aplicação bloqueia conflitos de horário.
5. Em **Louvor**, cadastre músicas e organize os itens do repertório do evento. Em **Sonoplastia**, cadastre equipamentos e registre ocorrências, manutenção ou checklists.
6. Envie avisos para um ministério ou uma pessoa desse ministério. Os destinatários podem marcar a leitura no próprio painel.

## Arquitetura e manutenção

- Flask Blueprints, Flask-Login e Flask-WTF para sessões e formulários. Use Jinja, CSS e JavaScript do projeto para a interface.
- SQLAlchemy e Alembic para persistência e migrações. Produção requer PostgreSQL com TLS. Os horários de eventos usam o fuso de São Paulo e os registros de auditoria usam UTC.
- ADMIN gerencia papéis, membros, configurações e auditoria. COORDINATOR gerencia os registros dos ministérios que coordena. USER consulta os ministérios aos quais pertence e as próprias escalas.
- O PWA usa a rede para páginas autenticadas e armazena em cache somente uma lista explícita de recursos estáticos públicos. Após alterações nos arquivos estáticos, incremente `CACHE` em `public/sw.js`.
- Veja o [guia de publicação e manutenção](docs/operacao.md) e o [fluxo de revisão dos agentes](docs/agentes.md).

## Testes

```sh
python -m pytest -q
python -m flask --app app db check
```

Para testar PostgreSQL, defina `FADLAB_TEST_DATABASE_URL` com uma conexão `postgresql+psycopg://` a um banco descartável. A suíte cria e remove schemas de teste. Os testes automatizados simulam o transporte de e-mail e o armazenamento de imagens.

O arquivo `tests/browser.mjs` roda o cenário de interface com Playwright. Use uma instância descartável e informe `FADLAB_BROWSER_URL`, `FADLAB_BROWSER_ADMIN` e `FADLAB_BROWSER_PASSWORD` por variáveis do processo. Instale o Playwright no ambiente de testes e execute `node tests/browser.mjs`. Se necessário, indique o módulo pelo caminho em `PLAYWRIGHT_MODULE` e o executável do Chromium com `CHROMIUM_BINARY`.
