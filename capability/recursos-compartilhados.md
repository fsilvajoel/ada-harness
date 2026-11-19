# Decisão: recursos compartilhados por todos os papéis

## Decisão
Todo papel (`planner`, `refiner`, `test-writer`, `reviewer`, `implementer`) tem acesso a **todas** as skills, tools,
gates, policies e arquivos de regra do harness, qualquer que tenha sido o papel da etapa anterior. O papel define
o que ele **escreve** (escopo de escrita) e o que entrega; não define o que ele pode **ler e consultar**. Em caso
excepcional o papel faz trabalho de outro: o implementer lê um teste, o reviewer roda um gate, o refiner consulta
a skill de teste.

## Onde fica
- **Skills:** [skill/](../skill/README.md) neste harness, uma pasta só (própria e de terceiros). Configuração:
  `skill_dir` no `harness.project.yaml` do projeto (por padrão `<harness>/skill`).
- **Gates:** [gate/](../gate/README.md); **policies:** [policy/](../policy/README.md); **capabilities:**
  esta pasta; **moldes de artefato:** [workflow/feature-artefatos/](../workflow/feature-artefatos/README.md);
  **comandos do projeto:** `commands` e `gates` do `harness.project.yaml`.
- **Caminho local da ferramenta:** o adaptador pode exigir uma pasta própria. No Claude Code, `.claude/skills` do
  projeto é **link simbólico** para `skill_dir` (chave `skill_link` do yaml), não cópia: um conteúdo só, sem
  duplicar. Ver [adapter/claude-code.md](../adapter/claude-code.md).

## Quem lê
| Recurso | planner | refiner | test-writer | reviewer | implementer |
|---|---|---|---|---|---|
| todas as skills de `skill/` | lê | lê | lê | lê | lê |
| gates (`gate/`) e execução de `<gerenciador> gate ...` | lê e roda | lê e roda | lê e roda | lê e roda | lê e roda |
| policies e capabilities | lê | lê | lê | lê | lê |
| moldes de `feature-artefatos/` | lê | lê | lê | lê | lê |
| plano, testes, estado, código do projeto | lê | lê | lê | lê | lê |

Uso habitual (não exclusivo): planner → capability planning; refiner → regras do plano e DoR/DoD; test-writer →
`escrever-teste`; reviewer → `revisar-teste` (e `escrever-teste` para julgar o teste); implementer →
`editar-arquivo`. Skills de terceiros (revisão, segurança, acoplamento etc.) valem para qualquer papel quando o
plano ou o orquestrador pedir.

## Skills obrigatórias por papel
O harness, e não a memória do orquestrador, garante que cada agente recebe as skills de que precisa:
1. **Do papel:** `role_skills.<papel>` no `harness.project.yaml` lista as skills que o papel **sempre** lê antes de
   começar. O molde do agente (Claude Code) repete a lista e manda ler; o executor dos trabalhadores sem ferramentas
   as injeta no pedido.
2. **Da tarefa:** o campo opcional `skills` do cartão ([formato](../workflow/feature-artefatos/cartao.md)) declara o
   extra (ex.: `react-best-practices#rerender-memo` numa tarefa de frontend). Quem decide é o plano (SDD).
3. **Sem divergência:** o gate [role-skills](../gate/role-skills.md) falha se `role_skills` e a seção "Skills
   obrigatórias" do molde (`roles.<papel>.agent`) diferirem; o gate [card-skills](../gate/card-skills.md) exige
   `skills:` nos cartões de `skills_required_paths` e confere que cada item existe.
4. **Peso:** `nome` traz só o índice `SKILL.md`; o detalhe vem por `nome#regra`; o `AGENTS.md` compilado nunca entra
   por padrão; teto `skills_max_bytes`, que falha em vez de truncar.

## Regras
1. **Leitura e execução são abertas; escrita é por papel.** Quem escreve o quê não muda (planner só o plano,
   refiner só plano/cartões/fila, test-writer só teste e `testes.md`, reviewer nada, implementer só o escopo).
   Tool de escrita extra só com pedido do orquestrador na chamada.
2. **Comando de verificação** (`commands`, gates `pnpm gate ...`, `git diff`) é permitido a todos, desde que não
   altere arquivo fora do escopo de escrita do papel. Gate que grava estado (`--sign`, gravação em `estado.json`)
   fica com o orquestrador.
3. **Skill nova vai para `skill/`**, nunca para pasta de papel. Skill é lida pelo caminho do arquivo
   (`skill/<nome>/SKILL.md`); o molde do agente cita o caminho, não repete o texto.
4. **Trabalhador sem ferramentas** ([api-chat](../adapter/api-chat.md#skills-e-regras-no-pedido),
   [cli-agent](../adapter/cli-agent.md)): o **executor** (`script/api_chat.py`, `script/cli_agent.py`) inclui no pedido as skills do papel e as do cartão (índice
   `SKILL.md`, mais as regras pedidas com `nome#regra`, com teto de tamanho). O orquestrador não escolhe nem cola.
5. **Regra que só um papel lê é lacuna**: se outro papel pode precisar, ela entra aqui ou em `policy/`.

## Registro
- 2026-11-19: skills saíram de `.claude/skills/` do projeto para `skill/` do harness (pedido do Joel: recursos
  compartilhados, não espalhados por papel). `.claude/skills` virou link simbólico. Regras locais do projeto
  (ex.: idioma da revisão, o que não se publica sem ok) passaram para o `CLAUDE.md` do projeto, para a pasta
  continuar portável.
