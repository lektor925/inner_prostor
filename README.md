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

MVP в разработке. Сделано (шаги 1-3 из ADR, раздел «Рекомендуемый порядок
разработки»): модели данных, `import_1c`, обработка-выгрузка 1С (код готов,
на реальной базе ещё не запускалась — см. `onec_export/README.md`). Впереди:
поиск, вьюхи/заявки, деплой.
