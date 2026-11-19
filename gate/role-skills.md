# Gate: role-skills

## Objetivo
Impedir que `role_skills` do `harness.project.yaml` (o que o executor injeta nos trabalhadores sem ferramentas) e a
seção "Skills obrigatórias" do molde do agente (o que o agente com ferramentas é mandado ler) divirjam.

## Critério de sucesso (PASS)
Para cada papel com `roles.<papel>.agent`, as skills da seção `## Skills obrigatórias` do molde (itens com o caminho
de `SKILL.md`, ou de `rules/<regra>.md`, lidos como `nome` e `nome#regra`) são as mesmas de `role_skills.<papel>`,
como conjunto. Papel sem a seção e sem `role_skills` vale como vazio nos dois lados.

## Critério de falha (FAIL)
Skill só em um dos lados, molde inexistente, molde sem a seção com `role_skills` preenchido, ou `role_skills` de papel
sem molde. A mensagem lista o que sobra em cada lado.

## Evidência esperada
Saída de `python3 script/check_role_skills.py <raiz do projeto>`: "Gate role-skills passou." (saída 0) ou uma linha
por papel divergente (saída 2). Os moldes vêm de `roles.<papel>.agent`, então o caminho já é configurável.
