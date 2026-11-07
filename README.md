# Strata Hub

Стенд агентного бенчмаркинга локальной LLM на llama.cpp: production-конфигурация,
harness, мониторинг, полная документация экспериментов.

**EN:** Strata Hub is a local LLM agent-benchmarking stand built around llama.cpp
(b11985) and a 27B dense-hybrid model on a single RTX 5070 Ti 16GB. It contains
the production server configuration, an AgentBench-style harness (10 software
engineering tasks, autonomous multi-round agent with tool calls), a harness
tournament (AgentBench vs DSH vs Pi), a llama-server monitoring dashboard, and
the full experiment reports (in Russian).

## Железо и прод-конфигурация

| компонент | значение |
|---|---|
| GPU | RTX 5070 Ti 16 GB (sm_120), одна карта = один движок |
| CPU / RAM | i7-14700F / 128 GB DDR4 |
| ОС | Windows 11 |
| Модель | **Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3** (IsValorum, 11.18 GB, 3.27 BPW, dense-гибрид: 64 слоя = 48 GatedDeltaNet + 16 full-attn, mmproj для vision) |
| Движок | llama.cpp 0.8.0-dev **b11985** (unsloth, commit 6f4cc4d72), CUDA, MSVC — см. [engine/ENGINE-BUILD-AND-CONFIG.md](engine/ENGINE-BUILD-AND-CONFIG.md) |
| Контекст | 131 072, KV-кэш **q4_0** (18 KB/токен), flash-attn, без спекулятивного декодирования |
| Reasoning | effort=low, preserve ON, `--reasoning-budget 16384` (кап думы на запрос) |
| Сэмплинг | сервер: 0.6/0.95/20/0.05 (APEX-рецепт); harness перекрывает: temp 0.2, seed 42 |
| Мониторинг | [panel8107](panel8107/) — порт 8091: живые т/с, контекст, VRAM, журнал запросов, долговременный JSONL-журнал |

Полные детали с обоснованием каждого флага: [docs/PROD-CONFIG-2026-11-07.md](docs/PROD-CONFIG-2026-11-07.md)

## Контур

```
schtasks SUPERVISOR (сторож очереди)
   └─ agentbench_runner.py (caps из harness-freeze-*.json)
        └─ AgentGW :8109 (OpenAI-совместимый шлюз: 10 bench-инструментов + pyapi + localdocs MCP)
             └─ llama-server :8107 (прод)
panel8107 :8091 (мониторинг: /health /props /slots /metrics + nvidia-smi)
```

## Результаты (кратко, полная история в docs/)

| серия | результат |
|---|---|
| abx5 (05.11) | 8/10 задач, принята как baseline v2 |
| r02full (06.11) | **9/10 — действующий baseline v3** (bugfix 5/5) |
| Турнир harness (05.11) | AgentBench / DSH / Pi: идентичные 3/5, эффективность DSH 2.67/ч > Pi 2.07 > AB 1.41; production = AB v2 |
| Ось движка (07.11) | feveromo REJECT (коллапс на длинных сессиях), TierKV REJECT (sustained + retrieval) — прод остаётся на b11985-mix |
| Budget 8192 (07.11) | GO-кандидат против марафонов; полная серия b8192f — в работе |

## Структура репозитория

```
engine/      — сборка и конфигурация движка (пин, флаги, reasoning-budget, freeze-политика)
bench/       — harness: runner, шлюз-инструменты, валидаторы, tournament-драйверы
  tasks/     — 10 задач (bugfix bf1–bf5, diagnostics d1–d3, integration i1–i2)
  harness/   — замороженные конфиги капов (freeze-abx5, abx5b)
  drivers/   — цепочные драйверы экспериментов (R0, b8192) и speed-probe
panel8107/   — монитор-панель (stdlib Python, порт 8091)
supervisor/  — сторож очереди задач (schtasks-совместимый)
launch/      — батники запуска прода/панели/шлюза + AgentGW
docs/        — полные отчёты по всем экспериментам (RU)
```

## Как воспроизвести

1. **Движок**: поставить llama.cpp b11985 (unsloth win64-CUDA релиз или сборка
   на пине 6f4cc4d72) — рецепт и все флаги: [engine/ENGINE-BUILD-AND-CONFIG.md](engine/ENGINE-BUILD-AND-CONFIG.md)
2. **Сервер**: `launch/START-NANOPLUS-V3.bat` (guard VRAM, ожидание /health).
   Модель — GGUF NanoPlus V3 от IsValorum в папку `models/` (не входит в репозиторий).
3. **Контур**: `launch/AGENTGW-SERVE.bat` (шлюз 8109) → `supervisor/START-SUPERVISOR.bat`
   (сторож) → задачи ставятся в очередь `supervisor/queue/pending`.
4. **Серия**: `bench/mkagentbench.py --prefix <имя> <задачи...>` создаёт job'ы;
   раннер подхватывает, отчёты падают в `runs/<prefix>-<task>/report.json`.
5. **Мониторинг**: `launch/START-PANEL8107.bat` → http://127.0.0.1:8091/

Методология: любое изменение движка/harness валидируется по критерию
«успешные задачи при фиксированном бюджете времени» против замороженного
baseline; A/B с seed 42; гейт длительной устойчивости (sustained 50 мин)
перед AgentBench для новых движков.

## Что НЕ входит в репозиторий

- **GGUF-модели** (NanoPlus V3 11.18 GB, компаньоны) — берутся у авторов моделей.
- **Зеркало целевого репозитория (IBF)**, на котором выполняются задачи
  harness — приватный код; задачи ссылаются на его коммиты (`base`/`overlay_commit`
  в `bench/tasks/*.json`). Без зеркала задачи читаемы, но не воспроизводимы.
- Runtime-состояния (очереди, логи, транскрипты прогонов) — остаются локально.
- Репозиторий дополняет приватное рабочее зеркало (`Aleksisin/strata`), где
  лежат все артефакты прогонов.

Код и документация выложены «как есть» для воспроизведения стенда.
