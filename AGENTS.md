# AGENTS.md

Этот репозиторий описан в **`CLAUDE.md`** — прочитай его целиком перед первой задачей. Все правила,
контракты и пороги там; этот файл только указатель для Codex.

Ты работаешь за **участника B — «сеть + API»** (`CLAUDE.md` §17). Твои файлы:
`mycelium/temporal.py, clusters.py, resilience.py, next_requests.py, sankey.py, layout.py, validate.py,
serve.py` (кроме ИИ-роутов), `tests/`, `docs/architecture.md`.

Не трогай чужие файлы. Сигнатуры своих функций бери строго из `CLAUDE.md` §6.8, форму ответов API —
из `docs/FRONTEND.md` §4 и `docs/mock/`. Эталонные расчёты (удержание, зависимый поток, блокировка) — в
`mycelium/explore.py`. gid в JSON — только строки. Коммит минимум раз в час.
