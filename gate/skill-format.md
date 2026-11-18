# Gate: skill-format

## Objetivo
Garantir que toda skill seja descoberta pelo agente e siga o mesmo formato.

## Critério de sucesso (PASS)
- a pasta de skills do projeto (`skill_dir` no `harness.project.yaml`) contém só pastas, `README.md`, `_modelo.md`
  e a licença das skills de terceiros;
- cada pasta tem `SKILL.md` com frontmatter (`---` no início e no fim);
- o frontmatter tem `name` igual ao nome da pasta e `description` não vazia;
- skill própria tem também as seções do modelo (`_modelo.md` da pasta de skills): `## Propósito`, `## Contexto de uso`,
  `## Passos...`, `## Limitações`, `## Exemplos práticos`.

**Própria × terceiro:** a lista `THIRD_PARTY` em `script/check_skill_format.py` nomeia as skills de terceiros
(copiadas sem alterar o conteúdo). Toda pasta fora da lista é tratada como própria. Para adicionar uma skill de
terceiro, inclua o nome na lista e registre a proveniência no `README.md` da pasta de skills.

## Critério de falha (FAIL)
Arquivo solto na pasta de skills, pasta sem `SKILL.md`, frontmatter ausente, `name` diferente da pasta,
`description` vazia ou seção obrigatória ausente em skill própria.

## Evidência esperada
Saída de `python3 script/check_skill_format.py <skill_dir>`: "Gate skill-format passou." ou uma linha por problema.
