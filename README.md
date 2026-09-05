# Справочник номенклатуры

Внутренний read-only справочник номенклатуры 1С:УПП для конструкторов
ООО «ПРОСТОР-Л». Контекст и архитектурные решения — см.
[assets/handoff-spravochnik-nomenklatury.md](assets/handoff-spravochnik-nomenklatury.md)
и [assets/adr-spravochnik-nomenklatury.md](assets/adr-spravochnik-nomenklatury.md).

## Стек

Django + PostgreSQL (обязателен `pg_trgm`, см. ADR-001).

## Локальный запуск

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
```

Без переменной `DATABASE_NAME` проект падает на sqlite (для быстрых прогонов
без Postgres — GIN-индексы pg_trgm на sqlite не создаются, `makemigrations`
всё равно работает). Для реальной разработки задать Postgres:

```bash
export DATABASE_NAME=spravochnik
export DATABASE_USER=postgres
export DATABASE_PASSWORD=...
export DATABASE_HOST=localhost
export DATABASE_PORT=5432
```

Дальше — как обычно:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Приложения

- `catalog` — модели номенклатуры, остатков, порогов, заявок и логов
  импорта (ADR-008/ADR-014), management-команда `import_1c` (ADR-009/ADR-010).
- `onec_export/` — код выгрузки для 1С:УПП (см. её отдельный
  [README](onec_export/README.md)), контракт JSON общий с `import_1c`.

## Статус

MVP в разработке. Сделано (шаги 1-6 из ADR, раздел «Рекомендуемый порядок
разработки»): модели данных, `import_1c`, обработка-выгрузка 1С (код готов,
на реальной базе ещё не запускалась — см. `onec_export/README.md`), поиск
с фасетами и карточка позиции (ADR-006/ADR-005), базовый вход и заявки на
новые позиции с антидубль-шагом (ADR-007/ADR-011/ADR-012), пересчёт
остатков и админка порогов/складов. Поиск проверен и на sqlite-фолбэке,
и на реальном Postgres 17 с `pg_trgm`.

Шаг 7 (деплой, MVP-объём без Nginx/TLS) — материалы готовы в `deploy/`
(`RUNBOOK.md`, скрипты службы Waitress/NSSM, задачи Task Scheduler,
бэкап `pg_dump`). На целевом сервере ещё не прогонялись — ждём доступа.
Проверено локально: тесты, `collectstatic` в прод-режиме, parse-only
PowerShell-скриптов.
