# Scripts

Lógica de orquestração e utilitários de execução do projeto.

Scripts coordenam o harness, mas não guardam a policy canônica nem o conhecimento de domínio, que fica nas skills.
Executores e gates: `api_chat.py` (api-chat), `cli_agent.py` (cli-agent), `skill_inject.py` (bloco de skills),
`check_portable.py`, `check_skill_format.py`, `check_card_skills.py`, `check_role_skills.py`.
Molde: [_modelo.md](_modelo.md).
