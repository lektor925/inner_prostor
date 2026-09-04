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
  импорта (ADR-008/ADR-014).

## Статус

MVP в разработке. Текущий шаг — модели данных (шаг 1 из ADR, раздел
«Рекомендуемый порядок разработки»). Management-команда `import_1c`,
поиск и заявки — впереди.
