# Capabilities

Comportamentos completos e reutilizáveis. Uma capability junta skills (conhecimento), papéis (execução),
policies (regras) e gates (aprovação). Formato: [_modelo.md](_modelo.md).

As skills ficam no projeto, na pasta `skill_dir` do `harness.project.yaml`; os papéis, em [agent/](../agent/README.md).

| Capability | Skills | Papel |
|---|---|---|
| [planning](planning.md) | — | planner |
| [testing](testing.md) | escrever-teste, revisar-teste | test-writer, reviewer |
| [code-modification](code-modification.md) | editar-arquivo | implementer |
