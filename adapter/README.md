# Adaptadores

O núcleo do harness (workflow, papéis, policies, gates) é neutro. Um adaptador diz como uma ferramenta de IA
executa esse núcleo: quem orquestra, como cada [papel](../agent/README.md) é chamado e quais travas valem.
O adaptador não muda o núcleo.

| Adaptador | Ferramenta |
|---|---|
| [claude-code](claude-code.md) | Claude Code |
| [api-chat](api-chat.md) | modelo por API de chat compatível com OpenAI, sem ferramentas (trabalhador de um papel) |

Ferramenta nova ganha um arquivo aqui quando for usada.
