# Skills

Pasta **compartilhada** de skills do harness Ada: fonte única, lida por **todos** os papéis (planner, refiner,
test-writer, reviewer, implementer) e pelo orquestrador. Nenhum papel tem skill só sua; o que cada papel usa
normalmente está em [capability/recursos-compartilhados.md](../capability/recursos-compartilhados.md). O projeto aponta
para cá com `skill_dir` no `harness.project.yaml`, e o adaptador ligado a uma ferramenta que exige caminho próprio
usa link simbólico para esta pasta (ex.: Claude Code, [adapter/claude-code.md](../adapter/claude-code.md)). Formato
verificado pelo gate `skill-format` (`python3 script/check_skill_format.py skill`, a partir da raiz do harness): uma pasta por skill, `SKILL.md` com frontmatter, `name`
igual ao nome da pasta e `description` não vazia.

## Skills próprias (pt-BR, seções do [_modelo.md](_modelo.md))

| Skill | Uso |
|---|---|
| escrever-teste | etapa tests de uma feature (test-writer); o reviewer e o implementer também a consultam |
| revisar-teste | revisão dos testes antes da implementação (reviewer); test-writer e refiner também a consultam |
| editar-arquivo | etapa implement (implementer); qualquer papel que edite arquivo dentro do seu escopo de escrita a consulta |

## Skills de terceiros

Copiadas de [tech-leads-club/agent-skills](https://github.com/tech-leads-club/agent-skills) (licença MIT, ver `LICENSE-tech-leads-club-agent-skills`), **fixadas no commit `e74dc8a`** (2026-11-10), em modo cópia, sem atualização automática. Scripts revisados antes da instalação. Toda skill nova de terceiro entra na lista `THIRD_PARTY` de `script/check_skill_format.py` do harness.

Ajustes locais sobre a cópia: a pasta `web-accessibility` virou `accessibility` (igual ao `name` do frontmatter) e o link para a skill `web-quality-audit`, que não foi instalada, virou texto; em `security-best-practices` saíram as referências de stacks que o projeto de origem não usa (Go, Django, Flask, FastAPI, Vue, jQuery).

| Skill | Uso |
|---|---|
| coding-guidelines | regras contra erros comuns de IA ao codar |
| tactical-ddd | revisão dos modelos de domínio |
| coupling-analysis, modular-design-principles | auditoria de acoplamento entre módulos a cada entrega |
| the-judge | revisão de PR baseada em evidência |
| security-threat-model, security-best-practices | modelo de ameaças (obrigatório antes do portal) e revisão de segurança |
| gh-fix-ci | diagnóstico de CI quebrado |
| harness-eval | avaliação do AGENTS.md e das travas |
| react-best-practices, accessibility | frontend Next.js e acessibilidade do portal |

## Regras locais
As regras do projeto que prevalecem sobre o texto das skills (ex.: idioma da revisão, o que nunca publicar sem ok,
documentos de decisão que vencem a sugestão da skill) ficam no `CLAUDE.md`/`AGENTS.md` do projeto, não aqui, para
a pasta continuar portável (gate [portable](../gate/portable.md)).
- Atualizar uma skill de terceiro = nova cópia de um commit revisado + atualizar o hash acima.
