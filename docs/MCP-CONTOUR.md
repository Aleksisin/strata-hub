# MCP-КОНТУР СТЕНДА — ЕДИНЫЙ ДОКУМЕНТ

**Обновлён:** 03.11.2026 (после выполнения TASK-MCP-CONTOUR-2026-11-03, W1–W6 закрыты).
**Что это:** все локальные MCP-серверы стенда — что установлено, как запускается,
как обновляется, ловушки. Один документ — один источник правды.

---

## 0. Карта контура (схема)

```
Hermes (профиль pi-operator)  ─┐
Pi 1.5.0 (глобальный mcp.json)─┴─►  ЛОКАЛЬНЫЕ MCP-СЕРВЕРЫ:
                                      ├─ localdocs  (HTTP  127.0.0.1:6280/mcp)  — документация библиотек
                                      ├─ pyapi      (stdio)                     — фактический Python API venv
                                      ├─ serena     (stdio, LSP)                — символы/структура кода
                                      └─ mgimind    (stdio, C:\Users\shogu\mgimind) — долговременная память
                                     ОБЛАЧНЫЙ (запасной): context7 (mcp.context7.com)
                                     ВЫКЛЮЧЕНЫ: linear, notion, tinyfish
agentgw (HTTP 127.0.0.1:8109) — НЕ для агентов: tool-gateway для трека «Peakstone Agent»
```

**Порты стенда (дисциплина):** 8107 = NanoPlus V3 (Hermes-дефолт) · 8108 = Bonsai Q2_0 ·
**8109 = agentgw** · 6280 = localdocs · 8090/8099 = панель/MiniPlus · 8095 = strata-смок.
llama-server принимает ЛЮБОЕ имя модели → занимать 8107/8108 чужим движком = молчаливый
перехват дефолта Hermes (silent-swap trap). agentgw живёт на 8109 специально.

---

## 1. localdocs — Grounded Docs (документация библиотек)

| | |
|---|---|
| Что | `arabold/docs-mcp-server` **3.4.1**, MIT, Node 22+ (стоит 24.19), офлайн, SQLite, полнотекстовый поиск (без embeddings — для точных Python-символов FTS точнее; вектора опциональны) |
| Программа | `C:\Users\shogu\AppData\Roaming\npm\docs-mcp-server.cmd` (npm -g) |
| Данные | `C:\Users\shogu\Strata-Hub\localdocs\` (SQLite `documents.db`) |
| Запуск | [LOCALDOCS-SERVE.bat](../launch/LOCALDOCS-SERVE.bat) → задача **StrataHub\LOCALDOCS** (schtasks, БЕЗ /IT — сессия 0, окно не убить случайно) |
| Адреса | web UI `http://127.0.0.1:6280` · MCP `http://127.0.0.1:6280/mcp` (streamable HTTP) |
| Инструменты | search_docs, read_page, list_libraries, find_version, scrape_docs, refresh_version, remove_docs, list_jobs, fetch_url… |
| Индексировано | **21 библиотека, все completed**: pandas 3.1.0 (12 008 чанков), matplotlib 3.11.2, numpy 2.5.4, python 3.14, pydantic 2.14.1, networkx 3.7, more-itertools 11.2.1, sortedcontainers 2.4.0, python-dateutil 4.0.0, rich 15.0.0, jsonschema 4.26.0, pytest, ruff, pyright, uv, fastapi 0.162.0, httpx 0.28.1, huggingface_hub 2.3.0, textual 8.2.8, pillow 13.0.0, cryptography 50.0.2. Версии = факты peakstone-venv |

**READ-ONLY СУЖЕНИЕ (04.11, «независимая мера сразу» из задания Supervisor §9):**
runtime-агентам — только `search_docs / read_page / list_libraries / find_version`;
`scrape_docs / refresh_version / remove_docs / fetch_url / list_jobs` — административные.
Исполнено на стороне Hermes (профиль pi-operator: секция localdocs получила
`tools.include` = 4 read-only инструмента, бэкап `config.yaml.bak-20261104-prereadonly`;
рестарт агента pi-operator — за оператором). Для Pi: глобальный mcp.json НЕ умеет
фильтра инструментов по серверу — вариант «второго инстанса» или осознанное
ограничение (админ-инструменты остаются видимы Pi-агентам) — РЕШЕНИЕ ЗА ОПЕРАТОРОМ.
Индексное обновление после сужения — только LOCALDOCS-INDEX.bat руками оператора.
Приёмка меры по спеке: состав инструментов НЕ оптимизировать — только права.

### Установка (была сделана, для воспроизведения)
```
npm install -g --allow-scripts=@arabold/docs-mcp-server,better-sqlite3,tree-sitter,tree-sitter-javascript,tree-sitter-python,tree-sitter-typescript @arabold/docs-mcp-server
```
**ЛОВУШКА:** без `--allow-scripts` npm молча НЕ собирает нативные биндинги
(better-sqlite3/tree-sitter) — пакет ставится «успешно» и падает в рантайме.
**ЛОВУШКА:** телеметрия по умолчанию ВКЛ (POSTHOG) — гасится `DOCS_MCP_TELEMETRY=false`
(проверено через `docs-mcp-server config`).

### Запуск вручную / env
```
set DOCS_MCP_STORE_PATH=C:\Users\shogu\Strata-Hub\localdocs
set DOCS_MCP_TELEMETRY=false
docs-mcp-server server          REM unified: web UI + MCP на 6280
```
`DOCS_MCP_STORE_PATH` обязан совпадать у сервера и CLI — иначе второй молча создаст
пустую базу в другом месте.

### CLI (обновление индекса)
```
docs-mcp-server list                                     REM что внутри + прогресс
docs-mcp-server search pandas "DataFrame.groupby"        REM проверка поиска
docs-mcp-server scrape <lib> <url> --version X -p N -d N -c 4 --telemetry false
docs-mcp-server refresh <lib>                            REM перескрапить изменившиеся страницы
docs-mcp-server scrape <lib> <url> --version X --replace REM пересобрать версию с нуля
docs-mcp-server remove <lib> <version>                   REM удалить версию
```
Повторный `scrape` той же версии без `--replace` падает — это защита, не баг.

### Процедуры
- **Новая версия пакета:** `scrape` с новой `--version` ляжет РЯДОМ со старой (версии
  хранятся поверсионно, поиск сам подберёт). Удаление устаревшей — `remove`.
- **Обычное обновление доков:** `refresh <lib>`.
- **Через агента (без рук):** у Pi/Hermes есть MCP-инструменты `scrape_docs`,
  `refresh_version`, `remove_docs` — можно попросить в чате.
- **Приоритет индексации (решение оператора):** версии фактических зависимостей
  (requirements.txt/pyproject/package-lock), не «всё подряд».

### Батники индексации
- [LOCALDOCS-INDEX.bat](../launch/LOCALDOCS-INDEX.bat) → StrataHub\LOCALDOCS-INDEX:
  очередь 1 (pandas/matplotlib/numpy), лог `launch\localdocs-index.log`.
- [LOCALDOCS-INDEX2.bat](../launch/LOCALDOCS-INDEX2.bat) → StrataHub\LOCALDOCS-INDEX2:
  18 библиотек тиров 1B/2/3, лог `launch\localdocs-index2.log`.
- **Гейт сериализации:** INDEX2 ждёт `=== all done` в логе очереди 1 — два скрепера
  никогда не пишут один SQLite одновременно.

### Ловушки URL
- `docs.pandas.pydata.org` из этой сети НЕДОСТУПЕН; `pandas.pydata.org/docs/` отдаёт
  ровно 3.1.0 — индексировать с меткой `--version 3.1.0`.
- python-dateutil: доки на `dateutil.readthedocs.io` (НЕ python-dateutil.readthedocs.io).
- pillow: `pillow.readthedocs.io/en/stable/`.
- Все URL кандидатов проверять `curl -sI` до скрапа.

---

## 2. pyapi — Python API Inspector (свой MCP, stdio)

| | |
|---|---|
| Что | `Strata-Hub\pyapi\pyapi_server.py` (W1 задания TASK-MCP-CONTOUR), чистый stdlib, newline-JSON-RPC: initialize / tools/list / tools/call / ping |
| Инструменты (О-1: ровно 3) | `package_info(package)` — версия/локация/зависимости фактического пакета; `inspect_symbol(qualname)` — точная сигнатура/типы/docstring установленной версии (напр. `pandas.DataFrame.groupby`); `find_examples(qualname, max_results)` — примеры использования из исходников пакета (только чтение) |
| Цель | **фактический peakstone-venv** (фикстура бенчмарка): pandas 3.1.0, numpy 2.5.4, matplotlib 3.11.2, networkx 3.7, more-itertools 11.2.1, sortedcontainers 2.4.0, pydantic 2.14.1, python-dateutil 4.0.0, rich 15.0.0, jsonschema 4.26.0 |
| Безопасность (О-2) | воркер в subprocess с `-I` (изоляция sys.path/env), cwd=%TEMP%, allowlist импортов **10 библиотек** `pandas,numpy,matplotlib,networkx,more_itertools,sortedcontainers,pydantic,dateutil,rich,jsonschema` (+stdlib; **по import-именам: дистрибутив `python-dateutil` = импорт `dateutil`**), таймаут воркера 15 с, кап вывода `--max-chars 7000` |
| Самотест | `pyapi\selftest.py` → **8/8 PASS** (вкл. dateutil.parser.parse, networkx deep path, pydantic, негативный контроль requests); [PYAPI-SELFTEST.bat](../launch/PYAPI-SELFTEST.bat), лог в launch\ |
| Подключение | Pi: глобальный `~/.pi/agent/mcp.json` (stdio: `python -I pyapi_server.py --python <venv-python> --allow pandas,numpy,matplotlib,networkx,more_itertools,sortedcontainers,pydantic,dateutil,rich,jsonschema`); Hermes: профиль pi-operator, `tools.include` = 3 |

Сервер stdio — не «запускается» отдельно: клиент (Pi/Hermes/agentgw) сам спавнит
процесс на каждый сезон. Для ручной проверки:
```
echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python -I pyapi\pyapi_server.py --python <venv> --allow pandas,numpy,matplotlib
```

---

## 3. Serena — семантический код-инструментарий (LSP)

| | |
|---|---|
| Что | `serena-agent` **2.1.0**, `uv tool install -p 3.13` → `C:\Users\shogu\.local\bin\serena.exe` |
| Запуск (как прописано в Hermes) | `serena start-mcp-server --context claude-code --agent-interface tools` |
| ЛОВУШКИ | (1) контекст `ide-assistant` УСТАРЕЛ → `claude-code` (23 tools, сам не дублирует файловые/терминальные инструменты клиента); (2) интерфейс по умолчанию — REPL (3 агрегатных инструмента) — нам нужен `--agent-interface tools`; (3) `serena project index` СПРАШИВАЕТ языковые серверы интерактивно — глушить пайпом «n»×8, иначе EOF-смерть |
| Инструменты (include-четвёрка в Hermes) | find_symbol, find_referencing_symbols, get_symbols_overview, get_diagnostics_for_file |
| Статус | **enabled: true** (03.11, приказ оператора; smoke верифицирован второй сессией — сырой stdio-прогон, tools/list сверен) |
| Прогрев LSP | `serena project index C:\Users\shogu\Strata-Hub\peakstone-repo` — сделан (холодный старт выносить из замеров, О-4); другие проекты индексируются лениво при первом использовании |
| Включение/выключение | `mcp_servers.serena.enabled` в профиле Hermes (см. §6) |

---

## 4. agentgw — tool-gateway для «Peakstone Agent» (свой, HTTP)

| | |
|---|---|
| Что | `Strata-Hub\agentgw\agentgw.py` — OpenAI-совместимый прокси **8109**: бенч говорит с ним как с моделью, внутри каждый запрос = tool-loop (до 8 итераций) к реальному 8107 + MCP (pyapi stdio + localdocs HTTP), ровно 7 схем инструментов (О-1), тянутся живьём из tools/list |
| Зачем | трек «Peakstone Agent» (О-5): те же 59 quick-челленджей, но с инструментами; скоринг/подпись бандлов — родные бенчевские; **результаты с one-shot лестницей НЕ смешивать** |
| Запуск | [AGENTGW-SERVE.bat](../launch/AGENTGW-SERVE.bat) → **StrataHub\AGENTGW** (schtasks, без /IT) |
| Предусловия | 8107 (NanoPlus) жив + 6280 (localdocs) жив |
| Самотест | `agentgw_selftest.py` → 7/7 PASS (без GPU); лог метрик `agentgw\agentgw.log` (JSONL: итерации, tool-calls, токены, finish) |
| Бенч-команда | `PEAKSTONE-NP3-AGENT.bat`: `--gateway http://127.0.0.1:8109 --models nanoplus-v3-agent --ids-file quick59_ids.txt --max-tokens 16384 --no-judge --bundle`; артефакты падают в КОРЕНЬ `runs\` (без --level!) — переносить в `runs\nanoplus-v3-agent16k\` |

**Три жёстких урока (проверены кровью):**
1. **Peakstone ходит ТОЛЬКО стримом** (`runner.py:561: client.stream = True` на каждом
   прогоне, детекция петель) — прокси обязан отвечать SSE (`_send_sse`: чанки +
   usage-чанк по `stream_options.include_usage` + `[DONE]`). Цельный JSON = бенч читает
   пустоту (первый Agent track 8,2% невалиден именно поэтому).
2. **UPSTREAM_TIMEOUT = 1800 с** — dp-01-класс на 16384 ток при ~20 т/с требует ~820 с;
   600 с убивал запросы TimeoutError'ом. TOOL_TIMEOUT = 60 с (тяжёлый FTS).
3. **Два PID с одинаковым CommandLine в CIM = venv-шим, а не два сервера**:
   `Scripts\python.exe` — лаунчер, который спавнит реальный интерпретатор-ребёнка
   с ТОЙ ЖЕ командной строкой; ранняя трактовка «SO_REUSEADDR позволяет двум процессам
   молча биндить один порт» была фантомом. `find_agentgw.ps1` покажет пару PID —
   это ОДИН сервер (убийство любого из них гасит обоих). Реальные двойники после
   повторных `/Run` всё же возможны — тогда убить ВСЕ PID пар и поднять один.
   wmic на Win11 удалён — только CIM.

**ВЕРДИКТ ПРИЁМКИ (04.11, контрольный протокол): КРИТЕРИЙ ПРОЙДЕН — 5 MCP-исправлений
против 1 регрессии; причинная оценка MCP = r2 − control = +4.87 балла на 59
(54.12 vs 49.25; one-shot 53.95 маскировал эффект). AgentGW ПРИНЯТ в контур.
**Финальное решение оператора (04.11): AgentGW GO, localdocs GO, pyapi GO; Serena —
оставить НЕЗАВИСИМО от этого бенчмарка (в AgentGW участвовали только pyapi+localdocs,
на Serena результат не переносится); MetaMCP/MCPJungle — не нужны; повторный Agent
A/B — для оценки воспроизводимости величины (+4.87 — предварительная оценка одной
серии), не для первичной приёмки. Для строгой воспроизводимости следующего A/B —
одна версия файла шлюза, различие только конфигурацией (AGENTGW_NO_LOCAL).**
Контроль: тот же шлюз с `AGENTGW_NO_LOCAL=1` (bench-инструменты включены, локальные
схемы не предлагаются; переключатель + лог-only tool_errors — единственные отличия
файла, хэши r2-era 64196c00d615c6fb / control 4223432d2129e5f9). Детали и разрезы —
§8.6 BENCHMARK_TOURNAMENT.md. Оговорка: повторений не было, воспроизводимость ±1-2 задачи.**
Хэш control-файла действует только для той редакции agentgw.py; любые правки = новый хэш.

**ИТОГОВАЯ ПРИЁМКА (04.11, вердикт оператора): STABLE / ACCEPTED — обязательных
незакрытых задач по контуру нет.** Финальное состояние: Pi MCP = 3 сервера/44
инструмента; Serena = только Hermes; pyapi = 10 библиотек, dateutil корректен;
AgentGW принят (+4.87/59 наблюдаемо); NanoPlus V3 = основная модель на llama.cpp;
Bonsai Q2_0 = длинноконтекстный компаньон; MiniPlus = быстрый резерв; strata-dense
и репаки = экспериментальные активы; MCP-оркестраторы не требуются.

**ПОВТОРНАЯ СЕРИЯ A/B (04.11 ночь, r3 → ctl-r2, один файл шлюза 4223432d2129e5f9
для обеих рук, различие только env; бандлы подписаны, артефакты валидированы
59 id 1:1): дельта2 = +0.20 (52.95 vs 52.75) против дельты1 +4.87. Уточнение
вердикта: воспроизводимое ядро MCP-эффекта = bcb/library-fluency срез
+2.20 (против +2.37 в серии 1; контролы байт-стабильны 9.75, флип bcb-0011
повторен дважды); tool-семейство +3.50 НЕ воспроизвелось (контроль первой серии
аномально провалил tool-задачи, контроль второй прошёл — логи ctl1 потеряны
штормом); агентная рука стабильна (54.12/52.95), нестабильна была контрольная
(49.25/52.75). Честная оценка: MCP стабильно даёт ≈ +2.2 балла на
library-fluency; общий композит от +0.20 до +4.87. Разрезы — §9
BENCHMARK_TOURNAMENT.md.**

---

## 5. mgimind — долговременная память (было до нас, работает)

`C:\Users\shogu\mgimind\bin\mgimind.exe mcp` (stdio, 2.8.2, Apache-2.0), 29 инструментов;
в Hermes фильтр `tools.include` = 7 (mind_search, mind_add, mind_fact, mind_outcome,
mind_session, mind_context, mind_skill). В Pi подключён целиком. Не трогаем — чужая зона.

---

## 6. Подключение клиентов

### Hermes (профиль pi-operator)
Файл: `C:\Users\shogu\AppData\Local\hermes\profiles\pi-operator\config.yaml`, секция
`mcp_servers`. Текущее состояние:

| Сервер | Транспорт | enabled | Фильтр |
|---|---|---|---|
| localdocs | HTTP `127.0.0.1:6280/mcp` | **true** | не ограничен |
| pyapi | stdio (venv python, allowlist) | **true** | `tools.include`: 3 |
| serena | stdio (`--context claude-code --agent-interface tools`) | **true** | `tools.include`: 4 |
| mgimind | stdio | **true** | `tools.include`: 7 |
| context7 | HTTP `mcp.context7.com` (облако) | не указано (запасной) | — |
| linear / notion / tinyfish | HTTP | **false** | — |

Правила правок: бэкап ДО правки (`config.yaml.bak-YYYYMMDD-<тег>`), YAML валидировать
после (`python -c "import yaml; yaml.safe_load(...)"`), сессия 0 не видит изменения —
**рестарт агента pi-operator за оператором**.

### Pi 1.5.0
Файл: `C:\Users\shogu\.pi\agent\mcp.json` (глобальный; проектный — `.pi/mcp.json`
в доверенной папке, флаг `-l`).
```
pi mcp add localdocs --url http://127.0.0.1:6280/mcp
pi mcp add pyapi -- <venv-python> -I C:\Users\shogu\Strata-Hub\pyapi\pyapi_server.py --python <venv-python> --allow pandas,numpy,matplotlib,networkx,more_itertools,sortedcontainers,pydantic,dateutil,rich,jsonschema
pi mcp list        REM статус + инструменты
pi mcp remove <имя>
```
Pi НЕ умеет per-tool фильтрацию (только целые серверы, `--no-mcp`, global vs `-l`).
У Pi сейчас (проверено по живому mcp.json 04.11): localdocs (12), pyapi (3),
mgimind (29) — **итого 44 схемы** (диета 85→44, tinyfish удалён 04.11 — облако
больше не подключается и из Pi). **Serena в Pi НЕТ** — она подключена только в
Hermes-профиле pi-operator (4 инструмента); в Pi не добавлялась.

### Порядок работы агента с API (методология, О-5/карточка)
1. `inspect_symbol`/`package_info` (pyapi) — ФАКТ установленной версии;
2. `search_docs` (localdocs) — объяснения/примеры/различия версий;
3. короткий тест в терминале — подтверждение поведения.
После написания кода: `uvx ruff check` → `uvx pyright` → `pytest -x -q` → правка → повтор
(карточка: `pyapi\CARD-AGENT-CHECKS.bat` + `launch\RUN-CHECKS.bat`).

---

## 7. Общие правила эксплуатации

1. **Долгоживущие сервисы — только schtasks** (создать задачу + /Run). Из шелла ZCode
   процессы умирают с концом хода (Job-дерево).
2. **Без `/IT`** (сессия 0, невидимое окно) для всего headless: окна schtasks-/IT
   закрываются случайно — три смерти цепочек 03.11. `schtasks /End` ненадёжен —
   убивать по PID (PowerShell CIM, `find_agentgw.ps1` как образец).
3. **Один GPU = один движок.** Пока жив NanoPlus (8107) — остальные не поднять, и наоборот.
4. **Порт без движения = отказ после ~6 минут** повторов; проверять `/health` перед работой.
5. Пуш вердиктов — `repo/sync.sh` после каждого вердикта (по приказу оператора).
6. Батники: ASCII-only, CRLF (после каждого редактирования грепать `\r`), без `pause`
   для schtasks, `PYTHONUTF8=1` для всего, что пишет UTF-8 текст.
7. `wmic` в Win11 удалён — процессы искать через PowerShell CIM.

## 8. Что НЕ ставлено и почему (О-8, 03.11)

Оркестраторы MCP (MetaMCP/MCPJungle/ToolHive/Docker Gateway/MCP Manager) — не ставим:
Docker Desktop на машине нет и не нужен; Hermes фильтрует per-server нативно; Pi —
выборочным подключением. **Триггер пересмотра:** 5+ серверов, профили чаще раза в день,
нехватка фильтрации Hermes → тогда MCPJungle (Go-бинарь, без Docker); MetaMCP — только
вместе с осознанным Docker Desktop. MCP Manager (progressive discovery: 3 hub-инструмента)
— кандидат эксперимента в треке Peakstone Agent (A/B против нативных ~26 схем), не прод.
