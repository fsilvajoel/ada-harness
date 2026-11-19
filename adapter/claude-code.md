# Adaptador: Claude Code

- **Orquestrador:** a sessão principal segue o [workflow feature](../workflow/feature.md).
- **Papéis:** subagentes em `.claude/agents/<papel>.md` no projeto (frontmatter `name`, `description`, `tools`),
  mapeados em `roles` do `harness.project.yaml`. A sessão principal chama um por etapa.
- **Skills:** pasta compartilhada [skill/](../skill/README.md) do harness (`skill_dir`). O Claude Code só carrega de
  `.claude/skills/`; por isso `.claude/skills` do projeto é **link simbólico** para `skill_dir` (`skill_link` no yaml):
  um conteúdo só, sem cópia. Criar: `ln -s <caminho relativo até o harness>/skill .claude/skills`. Os caminhos relativos
  dentro das skills valem na pasta física, então não quebram. Todo subagente lê todas as skills por esse caminho;
  o molde do agente cita `skill/<nome>/SKILL.md`, não repete o texto, e traz a lista **Skills obrigatórias** do papel
  (a mesma de `role_skills` do yaml) com a ordem de ler todas **antes de começar**, mais as do campo `skills` do cartão
  da tarefa ([formato](../workflow/feature-artefatos/cartao.md)). O orquestrador não precisa lembrar de nada.
- **Tools dos subagentes:** todo papel tem `Read, Grep, Glob, Bash` (consulta e comandos de verificação/gates); a
  escrita (`Write`, `Edit`) segue o escopo do papel ([decisão](../capability/recursos-compartilhados.md)). A trava
  edit-guard bloqueia escrita fora do repositório, inclusive na pasta de skills, que é só leitura para os papéis.
- **Trava [edit-guard](../gate/edit-guard.md):** hook `PreToolUse` em `.claude/settings.json` do projeto, rodando
  `scripts/guard-hook.mts` em Bash, Read, Edit, Write, MultiEdit, NotebookEdit, Grep e Glob. Bloqueio = saída 2,
  motivo no stderr. Libera escrita só na memória (`~/.claude/projects/<projeto>/memory/**`) e no temp
  (`/tmp/claude-<uid>/**`) da ferramenta.
- **Pesquisa ([research](../policy/research.md)):** `WebSearch` e `WebFetch`.
