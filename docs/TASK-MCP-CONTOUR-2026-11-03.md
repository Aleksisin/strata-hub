# ЗАДАНИЕ: MCP-контур агента — фаза 2 (после Grounded Docs)

**Дата:** 03.11.2026. **Заказ оператора:** «составь задание как опытный специалист на основании
отчёта; где надо — разработай мнение человека-оператора; выполни рекомендованные решения».
**Исполнитель:** ZCode (GLM), сессия draft-458c6683. **Статус:** ВЫПОЛНЯЕТСЯ, итоги в §6.

## 1. Цель и рамки

Дать тестируемой модели (NanoPlus V3 8107 — дефолт, Bonsai Q2_0 8108 — контекст 256K) инструменты
**проверки собственных решений**, а не новые источники информации. Прицел — слабейшая измеренная
ось: **lib-knowledge Bonsai 6,84/15 (0,456)**, dsv41 8,2/15 (0,547); lib-ось НЕ поднимается
one-shot'ом — только отдельным tool-augmented прогоном (зафиксировано в handover'е, не смешивать).

Вне рамок: one-shot QUICK-лестница (NanoPlus V3 51,06 / 86,5% и пр.) не пересматривается.

## 2. Факты контура (проверено 03.11)

| Объект | Значение |
|---|---|
| Strata-Hub | `C:\Users\shogu\Strata-Hub` (launch\, reports\, peakstone-venv\, localdocs\) |
| Grounded Docs | arabold/docs-mcp-server 3.4.1, HTTP `127.0.0.1:6280/mcp`, LOCALDOCS-SERVE/INDEX.bat |
| Pi | 1.5.0, глобальный `~/.pi/agent/mcp.json` (tinyfish, mgimind, localdocs) |
| Hermes | v0.22.6, профиль `pi-operator`: `AppData\Local\hermes\profiles\pi-operator\config.yaml` |
| Ограничение инструментов | Pi: `pi -p -t "…"`, `--no-mcp`; Hermes: `mcp_servers.<x>.tools.include` (mgimind 7/29) |
| peakstone-venv | Python 3.14.7; **pandas 3.1.0 / numpy 2.5.4 / matplotlib 3.11.2** (сверено живьём) |
| Инструменталка | uv 0.13.5, node 24.19, py 3.14/3.11; ruff/pyright/pytest НЕ установлены |
| Проверено внешне | serena-agent 2.1.0 на PyPI (py 3.11–3.14), `serena init` есть; mcpls существует; SQLite reference MCP архивирован |

## 3. Решения оператора (разработаны за оператора, по его зафиксированным принципам)

- **О-1. Поверхность инструментов минимальна.** PYAPI = ровно 3 инструмента
  (`package_info`, `inspect_symbol`, `find_examples`). Никаких Memory/SequentialThinking.
- **О-2. Инспектор смотрит в ФАКТИЧЕСКИЙ peakstone-venv** (те версии, что проиндексированы в
  localdocs). Allowlist импортов: pandas, numpy, matplotlib + stdlib. Остальное — отказ.
- **О-3. Тест-контур — без отдельного MCP.** Pi/Hermes уже имеют bash/terminal. Ценность даёт
  протокол флагов (карточка §5), а не обёртка. ruff/pyright — только через `uvx` (эфемерно);
  **peakstone-venv не мутировать** — он индексируется как фикстура бенчмарка.
- **О-4. Serena — установить и верифицировать, в профиль подключить `enabled: false`**
  с `tools.include` (4 инструмента), включение — только по приказу после smoke-прогона.
- **О-5. Замер — отдельный трек «Peakstone Agent»**: те же 59 quick-челленджей, judge OFF,
  подписанные бандлы, инструменты = localdocs + pyapi (+serena после smoke). Результаты НЕ
  смешивать с QUICK-лестницей. Логировать: решено/59, tool-calls по инструментам, итерации
  правок, токены, wall, tok/s.
- **О-6. Отложено:** Git MCP (терминал покрывает), SQLite MCP (IBF/TGCollector не в контуре
  турнира), Playwright MCP (веб-проекты вне текущей задачи).
- **О-7. Пуш вердиктов в Aleksisin/strata (repo/sync.sh) — не запускать**, только по приказу.
- **О-8. MCP-оркестраторы — не ставить** (вердикт второй сессии 03.11, проверено: Docker на
  машине нет, только WSL2 ради Unsloth; per-server `tools.include` Hermes живой; у Pi per-tool
  фильтра нет — закрывается global/`-l` mcp.json + `--no-mcp`). При 26 видимых инструментах
  (~2–4K токенов) измеримой выгоды ноль. Триггер для пересмотра: серверов 5+, задачные профили
  переключаются чаще раза в день, фильтрации Hermes стало мало → тогда MCPJungle (Go-бинарник,
  без Docker); MetaMCP — только вместе с осознанной установкой Docker Desktop. Подробнее §8.

## 4. Работы

| # | Работа | Приёмка |
|---|---|---|
| W1 | PYAPI MCP: `Strata-Hub\pyapi\pyapi_server.py` (stdio, только stdlib, Windows-safe: воркер в subprocess с `-I`, таймаут, allowlist, JSON-капсы) | selftest зелёный на фактических версиях |
| W2 | Самотест: `pyapi\selftest.py` + `launch\PYAPI-SELFTEST.bat` (лог в launch\) | pandas 3.1.0: package_info + сигнатура + примеры |
| W3 | Подключение: Pi `mcp.json` (backup) + Hermes pi-operator (backup, tools.include 3) | `pi mcp list` без ошибок; YAML профиля парсится |
| W4 | Карточка проверок `pyapi\CARD-AGENT-CHECKS.md` + `launch\RUN-CHECKS.bat` (uvx ruff/pyright, pytest — если есть в venv проекта) | карточка и бат соответствуют О-3 |
| W5 | Serena: `uv tool install`, `serena --help` (проверка `start-mcp-server`), черновик конфига enabled:false | CLI верифицирован; конфиг-черновик в профиле |
| W6 | Указатель в `reports\BENCHMARK_TOURNAMENT.md` (§9) на это задание | одна секция, без правок истории |

## 5. Карточка агент-методологии (сводка; полная — pyapi\CARD-AGENT-CHECKS.md)

Порядок при вопросе об API: `inspect_symbol`/`package_info` (факт установленной версии) →
`search_docs` (localdocs: объяснение/примеры/версии) → короткий тест (запуск в терминале).
Порядок после написания кода: `ruff check --output-format concise` → `pyright --outputjson`
(отбор ошибок ошибок/атрибутов) → `pytest -x -q --tb=short` → правка → повтор. Вывод капсить.

## 6. Статус выполнения (03.11, ночь)

| # | Статус | Примечание |
|---|---|---|
| W1 | ✅ ВЫПОЛНЕНО | `Strata-Hub\pyapi\pyapi_server.py` (stdio, stdlib-only, воркер `-I`+allowlist+таймаут) |
| W2 | ✅ ВЫПОЛНЕНО | selftest **8/8 PASS** (версии фикстуры сверены; allowlist-отказ проверен); `launch\PYAPI-SELFTEST.bat` |
| W3 | ✅ ВЫПОЛНЕНО | Pi: `pyapi connected, 3 tools` (mcp.json.bak-20261103-prepyapi); Hermes pi-operator: YAML валиден (bak-20261103-prepyapi), pyapi enabled, serena enabled:false |
| W4 | ✅ ВЫПОЛНЕНО | `pyapi\CARD-AGENT-CHECKS.md` + `launch\RUN-CHECKS.bat` (uvx ruff/pyright; pytest — только venv проекта) |
| W5 | ✅ ВЫПОЛНЕНО/ЧЕРНОВИК | serena-agent 2.1.0 (`uv tool install -p 3.13` → py 3.13.17); stdio-протокол проверен сырым прогоном; smoke: контекст `claude-code` + `--agent-interface tools` = 23 tools, include-четвёрка (find_symbol, find_referencing_symbols, get_symbols_overview, get_diagnostics_for_file) сверена с tools/list; контекст `ide-assistant` из статьи устарел (→ `claude-code`); в профиле enabled:false — включение по приказу после smoke-прогона |
| W6 | ✅ ВЫПОЛНЕНО | §9-указатель добавлен в reports\BENCHMARK_TOURNAMENT.md |

Откат: восстановить бэкапы `mcp.json.bak-20261103-prepyapi` и
`profiles/pi-operator/config.yaml.bak-20261103-prepyapi`; каталог `Strata-Hub\pyapi` и запись
`uv tool uninstall serena-agent` — удалением.

## 7. Риски и откат

- Все правки конфигов — после бэкапа: `~/.pi/agent/mcp.json.bak-20261103-prepyapi`,
  `profiles/pi-operator/config.yaml.bak-20261103-prepyapi` (конвенция бэкапов стенда).
- PYAPI исполняет `import` целевых пакетов (код пакета запускается) — воркер изолирован:
  `-I`, allowlist, таймаут 15 с, cwd=temp. Остаточный риск принят, пакеты — из замороженного venv.
- Ловушка портов: Bonsai на 8107 молча перехватит дефолт Hermes — не трогать START-баты.
- Serena тянет LSP-индексацию: холодный старт выносить из замеров (`serena project index` заранее).

## 8. Оркестраторы MCP — вердикт и A/B-замер (03.11, вторая сессия)

Проверено второй сессией: Docker на машине отсутствует; per-server `tools.include` в Hermes
работает (mgimind 7/29); у Pi per-tool фильтра нет (`pi mcp add`: только --url/--env/--cwd/--local) —
дыра закрывается дёшево: global vs `-l` (проектный mcp.json) + `--no-mcp` на чатах без инструментов.

| Вариант | Вердикт |
|---|---|
| MetaMCP | **Не сейчас** — цена входа = Docker Desktop + Postgres + контейнеры (~1–2 ГБ RAM) на машине, где Docker не было; при 26 инструментах и одном операторе выгоды ноль измеримых |
| MCP Manager (progressive disclosure) | **Да — как эксперимент**, вариант B замера трека Peakstone Agent (О-5), не в прод |
| MCPJungle | Запасной путь при срабатывании триггера оркестратора: один Go-бинарник, группы инструментов, без Docker |
| Docker MCP Gateway / ToolHive | Мимо — контейнерная изоляция ценна там, где контейнеры уже есть |

**A/B-замер (по приборам О-5, вторая сессия):** те же 59 quick, judge OFF, подписанные бандлы.
Вариант A (прод-путь): native localdocs + PYAPI + serena, ~26 инструментов сразу.
Вариант B (эксперимент): MCP Manager progressive — 3 hub-инструмента + search→describe→call.
Вопрос замера: умеет ли 27B выбирать инструмент через посредника. Результаты НЕ смешивать
друг с другом и с one-shot QUICK-лестницей.

Дубли не допускаются: Test-блок (pytest/ruff/pyright) закрыт О-3 (терминал + uvx, без обёртки),
Inspector — уже построен (W1, `pyapi_server.py`). Из диаграмм второй сессии реально недостаёт
только Serena — и она закрыта W5. **Сейчас ничего не ставить; контур остаётся: localdocs +
PYAPI + serena через нативную фильтрацию, оркестратор — в ожидании триггера О-8.**
