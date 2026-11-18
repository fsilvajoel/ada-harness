# Adaptador: Claude Code

- **Orquestrador:** a sessão principal segue o [workflow feature](../workflow/feature.md).
- **Papéis:** subagentes em `.claude/agents/<papel>.md` no projeto (frontmatter `name`, `description`, `tools`),
  mapeados em `roles` do `harness.project.yaml`. A sessão principal chama um por etapa.
- **Skills:** `.claude/skills/<nome>/SKILL.md` no projeto (`skill_dir`).
- **Trava [edit-guard](../gate/edit-guard.md):** hook `PreToolUse` em `.claude/settings.json` do projeto, rodando
  `scripts/guard-hook.mts` em Bash, Read, Edit, Write, MultiEdit, NotebookEdit, Grep e Glob. Bloqueio = saída 2,
  motivo no stderr. Libera escrita só na memória (`~/.claude/projects/<projeto>/memory/**`) e no temp
  (`/tmp/claude-<uid>/**`) da ferramenta.
- **Pesquisa ([research](../policy/research.md)):** `WebSearch` e `WebFetch`.
