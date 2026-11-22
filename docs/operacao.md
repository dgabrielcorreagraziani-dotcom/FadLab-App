# Publicação e manutenção

## Vercel e Supabase

Configure ambientes separados para produção e testes. Mantenha o app, o PostgreSQL, o remetente SMTP e o bucket de fotos sob controle da equipe responsável.

| Variável | Finalidade |
| --- | --- |
| `SECRET_KEY` | Valor aleatório exclusivo do ambiente, com pelo menos 32 caracteres. |
| `DATABASE_URL` | Conexão PostgreSQL autenticada e com TLS. Em serverless, use a porta de transações de Supavisor (6543). Prefira validação do certificado com `sslmode=verify-full` e o certificado raiz adequado a seu provedor. |
| `PUBLIC_URL` | Origem HTTPS usada pela aplicação e pelos links de recuperação. |
| `TRUSTED_HOSTS` | Hosts adicionais permitidos, separados por vírgulas, quando necessário. Por padrão, é usado o host de `PUBLIC_URL`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` | Provedor de recuperação por e-mail, usando SMTP com TLS implícito. |
| `SUPABASE_URL`, `SUPABASE_STORAGE_KEY`, `SUPABASE_AVATAR_BUCKET` | Armazenamento privado de fotos. Crie o bucket privado e limite-o a JPEG e 1 MB. Use a credencial exclusivamente no servidor. |
| `FADLAB_ENV` | Use `production` no ambiente que aplica as migrações e provisiona a conta inicial. A Vercel ativa esse modo automaticamente. |

1. Use um projeto Supabase dedicado ao FadLab ou um esquema dedicado sem exposição pela Data API. Habilite apenas os serviços necessários. As migrações ativam RLS nas tabelas, sem políticas de acesso direto para clientes da Data API. O Flask acessa pelo usuário SQL autorizado. Se optar por um usuário da aplicação separado do proprietário das tabelas, conceda somente os privilégios SQL necessários e configure as políticas desse usuário antes de publicar.
2. Aplique `python -m flask --app app db upgrade` pela conexão direta ou via pooler no modo sessão, em ambiente com backup e acesso de migração. Execute `python -m flask --app app create-admin` uma vez no ambiente autorizado. Cadastros começam fechados em produção. Abra o cadastro durante a entrada dos membros e confirme a identidade antes de conceder acesso.
3. Importe o repositório na Vercel, selecione Flask e configure as variáveis privadas. A entrada é `app.py`. A Vercel serve os recursos públicos e o Flask executa os fluxos autenticados. A aplicação não depende de arquivos locais persistentes nem de tarefas residentes. O pooler externo gerencia as conexões, e o cliente PostgreSQL usa `NullPool` sem prepared statements automáticos.
4. Após a publicação, valide HTTPS, login, uma operação por papel, migração, e-mail de redefinição, foto de perfil privada e o PWA em um telefone de teste. Use contas e dados de teste próprios do ambiente.

Documentação oficial do [Flask na Vercel](https://vercel.com/docs/frameworks/backend/flask) e do [pooler Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Rotina de operação

- Execute `python -m flask --app app prune-security` periodicamente por uma tarefa externa autorizada. O comando remove contadores antigos de tentativas e credenciais de recuperação vencidas.
- Antes de atualizações de esquema, faça backup e teste a migração em staging. Para uma regressão de código, restaure a versão anterior pela Vercel e trate a compatibilidade do esquema a partir de um backup ou de uma migração corretiva.
- Acompanhe erros de envio de recuperação e de limpeza de fotos. Uma falha entre o Storage e o banco pode deixar objetos sem referência. Compare os objetos do bucket com as chaves atuais e a política de retenção antes de remover órfãos pela API de Storage.
- As equipes compartilham nomes de membros no próprio ministério. O e-mail de outra conta aparece apenas para administradores. Logs de auditoria registram ação, data, identificadores e ator. Defina a retenção de backups, auditoria e dados pessoais com a equipe do FadLab.

## Limites e decisões

- O acervo original de julho usava exclusivamente armazenamento no navegador, incluía uma conta de demonstração e não fornecia dados de produção verificados. A versão Flask começa com banco vazio e não importa contas, senhas ou registros dessa cópia.
- `ADIM USER` aguarda definição de regras próprias. A implementação aplica ADMIN, COORDINATOR e USER com escopo por ministério.
- Os avisos são internos à aplicação. A preferência no perfil controla os destaques do painel. O e-mail é usado para recuperação de senha.
- A agenda mensal exibe até 250 eventos, e seletores operacionais exibem até 250 opções por lista. Use a agenda em lista para navegar por volumes maiores. Os testes não medem tráfego de produção.
- Os cinco perfis do Copilot foram configurados com passagem manual de revisão. A existência das definições não implica que o Copilot tenha executado testes.
