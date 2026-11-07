# Strata Supervisor v1

Реализация задания `C:\Users\shogu\Strata-Hub\reports\TASK-SUPERVISOR-2026-11-04.md`
(утверждено оператором 04.11). Один владелец GPU-слота, файловая очередь,
завершение ПО АРТЕФАКТАМ (не по exit code), изоляция деревьев через Job Objects.

## Запуск / остановка

- Старт: `START-SUPERVISOR.bat` (регистрирует задачу `StrataHub\SUPERVISOR`
  с триггером 01/01/2000 — никогда не срабатывает сама, только `/Run` — урок
  шторма 23:59) → сервер живёт в сессии 0, вне ZCode-job-дерева.
- Живость: `state\supervisor.json` (pid, pulse_utc, текущее задание).
- Мягкая остановка: создать `state\stop.flag` — доработает текущее задание и выйдет.
- Жёсткая остановка: `taskkill /F /PID <pid из heartbeat>` — дети погибнут через
  KILL_ON_JOB_CLOSE их per-run Job (контейнирование сбоя по построению).
- Убить ОДНО задание: положить пустой файл `state\kill\<run_id>.flag`.
  Статус будет INTERRUPTED/FAILED_INVALID — убитое задание НИКОГДА не получает DONE.
- Одиночный экземпляр: lock `state\supervisor.lock` (второй отклоняется).

## Очередь

Задание = JSON в `queue\pending\<run_id>.json`:

```json
{
  "run_id": "20261104-abrep-r3",
  "kind": "peakstone",
  "cmd": ["C:\\...\\peakstone-bench.exe", "--out", "C:\\...\\runs\\<run_id>\\", ...],
  "workdir": "C:\\Users\\shogu\\Strata-Hub\\peakstone-repo",
  "run_dir": "C:\\Users\\shogu\\Strata-Hub\\peakstone-repo\\runs\\<run_id>",
  "timeout_s": 7200,
  "validate": {"kind": "peakstone59", "expected_ids": 59,
               "ids_file": "C:\\...\\quick59_ids.txt", "bundle_signature": true},
  "manifest": {"model": {"name": "...", "gguf_path": "C:\\...\\model.gguf"},
               "engine": {"name": "llama.cpp", "port": 8107, "binary_path": "..."},
               "inference": {"context": 131072, "max_tokens": 16384,
                             "temperature": 0.2, "seed": 42},
               "tools": {"profile": "agent-full", "gateway_path": "C:\\...\\agentgw.py",
                         "allowlist_path": "C:\\...\\allowlist.json"},
               "benchmark": {"suite_path": "C:\\...\\suite.json",
                             "ids_file": "C:\\...\\quick59_ids.txt"}}
}
```

Приём: атомарный rename pending→active, создание run_dir ДО старта, manifest.json
в run_dir (SHA256 фактических файлов) до старта, results-секция дописывается после
валидации. Бенч пишет `--out` прямо в run_dir — этап «move» отсутствует.
Дубликат run_id / битый JSON / неизвестный валидатор → `queue\rejected\` с причиной.

## Состояния (`state\<run_id>.json`)

```
QUEUED → RUNNING → VALIDATING → DONE            (артефакты прошли валидацию)
                     ├→ FAILED_INVALID          (артефакты есть, не прошли)
                     └→ FAILED_TIMEOUT          (убит по timeout_s; валидация выполнена)
RUNNING → INTERRUPTED                           (процесс мёртв, артефактов нет / убит)
```

- DONE ТОЛЬКО после валидации (`peakstone59`: ровно N результатов, id 1:1 с
  ids_file без дублей, числовой final_score у каждого, bundle.json подписан
  bundle_hash). Exit code не участвует.
- Автоповторов НЕТ. Повтор = новое задание с новым run_id.
- Recovery при старте: RUNNING + живой pid → re-attach; мёртв → валидация →
  DONE/FAILED_INVALID/INTERRUPTED; pending остаётся в очереди.

## Ограничения v1 (осознанные)

- Re-attach к чужому процессу: ожидание по pid (OpenProcess SYNCHRONIZE);
  принудительный kill чужого дерева — TerminateProcess по pid, изоляция детей
  через Job Object в этом случае не гарантирована (для заданий, запущенных этим
  экземпляром — гарантирована через TerminateJobObject).
- `timeout_s` без поля = без ограничения.
- Часы таймаута при re-attach отсчитываются заново.
- Один GPU-слот = один RUNNING; второй ждёт в QUEUED (приёмочный тест 1).

## Приёмочные испытания v1

1. Два одновременных запуска: второй остаётся QUEUED до освобождения слота.
2. TerminateJobObject на середине прогона → не DONE (INTERRUPTED/FAILED_INVALID),
   артефакты сохранены как есть.
3. Перезапуск supervisor: очередь и существующие результаты не теряются.
4. Дубликат run_id отклоняется; у прогонов разные run_dir — перезапись невозможна.
5. Уничтожение одного задания не задевает Hermes/Pi/шлюзы/движки (health
   8107/8109/6280 до/после).

Скрипт испытаний: `tests\ACCEPTANCE.bat` + dummy-джоба `tests\dummy_job.py`
(валидатор kind=dummy: `out/result.json` = {"ok": true, "n": N}).

Журнал supervisor: `logs\supervisor.log`; stdout/stderr задания:
`logs\<run_id>.log` (наследуются, PYTHONUNBUFFERED=1 потомку).
