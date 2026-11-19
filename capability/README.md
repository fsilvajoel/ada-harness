# Capabilities

Comportamentos completos e reutilizáveis. Uma capability junta skills (conhecimento), papéis (execução),
policies (regras) e gates (aprovação). Formato: [_modelo.md](_modelo.md).

As skills ficam em [skill/](../skill/README.md), pasta compartilhada do harness (`skill_dir` do `harness.project.yaml`); os papéis, em [agent/](../agent/README.md). Todo papel acessa todas as skills, gates e policies: [recursos-compartilhados](recursos-compartilhados.md).

| Capability | Skills | Papel |
|---|---|---|
| [planning](planning.md) | — | planner |
| [testing](testing.md) | escrever-teste, revisar-teste | test-writer, reviewer |
| [code-modification](code-modification.md) | editar-arquivo | implementer |
| [recursos-compartilhados](recursos-compartilhados.md) | todas | todos |
