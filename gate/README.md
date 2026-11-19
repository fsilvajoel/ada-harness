# Gates

Critérios objetivos que aprovam ou bloqueiam uma execução. Um gate responde só **PASS** ou **FAIL**.

O cálculo da resposta fica no script correspondente; o gate documenta a condição e a evidência. Gate marcado como
**manual** é decidido por um papel e registrado no estado. Pular um gate é FAIL.
Formato de cada arquivo: [_modelo.md](_modelo.md).

| Gate | Pergunta | Executor |
|---|---|---|
| [portable](portable.md) | o harness está livre de referências ao projeto alvo? | `script/check_portable.py <config do projeto>` |
| [skill-format](skill-format.md) | toda skill segue `<skill_dir>/<nome>/SKILL.md` com frontmatter? | `script/check_skill_format.py <skill_dir>` |
| [card-skills](card-skills.md) | todo cartão de caminho exigido (ex.: web) declara `skills:` e cada item existe? | `script/check_card_skills.py <raiz do projeto>` |
| [role-skills](role-skills.md) | `role_skills` e a seção "Skills obrigatórias" dos moldes dizem o mesmo? | `script/check_role_skills.py <raiz do projeto>` |
| [status-consistency](status-consistency.md) | o `estado.json` acompanha os commits que citam a feature? | `script/check_status_consistency.py <raiz do projeto>` |
| [edit-guard](edit-guard.md) | a tool respeita hooks do git, `.env` e o repositório? | trava do adaptador ([Claude Code](../adapter/claude-code.md)) |
| [done-checks](done-checks.md) | a feature está pronta? | `pnpm verify` |
| [testes-revisados](testes-revisados.md) | os testes revisados continuam com o mesmo sha e cada critério tem teste da camada declarada? | `pnpm gate tests-reviewed <feature> [--sign]` |
| [expect-red](expect-red.md) | os testes falharam contra o esboço antes da implementação? | `pnpm gate expect-red <feature>` |
| [cobertura](cobertura.md) | os arquivos da feature têm 100% de cobertura e a medição não é vazia? | `pnpm gate coverage <feature>` |
| [mutacao](mutacao.md) | os testes matam os mutantes dos arquivos de domínio acima do score mínimo? | `pnpm gate mutation <feature>` |
| [e2e](e2e.md) | os fluxos e2e da feature passam no navegador? | `pnpm gate e2e <feature>` |
| [auditoria](auditoria.md) | a auditoria aprovou? | manual (`reviewer`) |
