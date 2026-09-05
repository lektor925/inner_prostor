# Раннбук: развёртывание справочника номенклатуры

Шаг 7 ADR (ADR-013), MVP-объём. Ставим на **чистую Windows-машину** в
локальной сети: PostgreSQL + Django под Waitress (служба через NSSM) +
три задачи Task Scheduler (импорт справочников, импорт остатков, бэкап).

**Вне объёма этого шага:** Nginx, TLS/HTTPS — отдельный будущий шаг,
когда прояснится статус AD CS (см. раздел 10). Пока сайт работает по
`http://` внутри локальной сети.

## Как пользоваться

Каждый раздел — команды + «как проверить, что сработало». Идти по
порядку. Если проверка не прошла — остановиться и разобраться, не
переходить дальше. Все команды PowerShell выполняются **от администратора**,
если не сказано иное.

Обозначения:
- `<REPO>` — папка репозитория на сервере, например `C:\spravochnik\app`.
- `<ENV>` — путь к прод-файлу окружения вне репозитория, например
  `C:\spravochnik\prod.env`.
- `<BACKUP>` — папка/шара для дампов БД, например `\\nas\backup\spravochnik`.

---

## 1. Подготовка Windows Server

### 1.1. Служебная учётная запись

Импорт из 1С читает папку выгрузки (ADR-009). Задачи планировщика должны
идти под отдельной служебной учёткой с доступом на чтение к этой папке —
**не под личной учёткой Владимира**.

- Если в организации есть AD: попросить администратора завести
  `CORP\svc-spravochnik` и выдать доступ на чтение к сетевой папке 1С.
- Если AD нет: локальная учётка на сервере
  `net user svc-spravochnik <пароль> /add`. На MVP папка выгрузки
  локальная (ADR-020), доступ к ней у локальной учётки есть по умолчанию.

Проверка: `Get-LocalUser svc-spravochnik` (локальная) либо вход под этой
учёткой на сервер удаётся.

### 1.2. Структура папок

```powershell
New-Item -ItemType Directory C:\spravochnik\app     -Force
New-Item -ItemType Directory C:\spravochnik\import  -Force
New-Item -ItemType Directory C:\spravochnik\app\logs -Force
```

Проверка: `Test-Path C:\spravochnik\import` → `True`.

---

## 2. PostgreSQL

### 2.1. Установка

```powershell
winget install PostgreSQL.PostgreSQL.17
```

(winget-установка PostgreSQL 17 уже проверена рабочей на машине разработки —
коммит «Исправлен поиск на реальном Postgres».)

Добавить папку `bin` PostgreSQL в системный `PATH` (нужно для `psql`,
`pg_dump`), затем открыть новую консоль:

```powershell
setx /M PATH "$($env:PATH);C:\Program Files\PostgreSQL\17\bin"
```

Проверка: в новой консоли `psql --version` и `pg_dump --version` печатают
версию 17.x.

### 2.2. База и роль

```powershell
$env:PGPASSWORD = '<пароль postgres, заданный при установке>'
psql -U postgres -h localhost -c "CREATE ROLE spravochnik LOGIN PASSWORD '<пароль_БД>';"
psql -U postgres -h localhost -c "CREATE DATABASE spravochnik OWNER spravochnik ENCODING 'UTF8';"
Remove-Item Env:PGPASSWORD
```

### 2.3. Расширения

`pg_trgm` обязателен (ADR-001), `unaccent` используется поиском.

```powershell
$env:PGPASSWORD = '<пароль postgres>'
psql -U postgres -h localhost -d spravochnik -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
psql -U postgres -h localhost -d spravochnik -c "CREATE EXTENSION IF NOT EXISTS unaccent;"
Remove-Item Env:PGPASSWORD
```

Проверка:

```powershell
$env:PGPASSWORD = '<пароль postgres>'
psql -U postgres -h localhost -d spravochnik -c "\dx"
Remove-Item Env:PGPASSWORD
```

В списке расширений должны быть `pg_trgm` и `unaccent`.

---

## 3. Репозиторий и виртуальное окружение

```powershell
git clone <URL репозитория> C:\spravochnik\app
cd C:\spravochnik\app
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Проверка:

```powershell
.\.venv\Scripts\python.exe -m pip show waitress whitenoise django psycopg2-binary
```

Все четыре пакета показывают версию (см. `requirements.txt`).

---

## 4. Файл окружения (не в git)

```powershell
Copy-Item C:\spravochnik\app\deploy\env.example C:\spravochnik\prod.env
notepad C:\spravochnik\prod.env
```

Заполнить все значения. `DJANGO_SECRET_KEY` сгенерировать:

```powershell
C:\spravochnik\app\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

Обязательно проверить в `prod.env`:

- `DJANGO_DEBUG=False` — строго.
- `DJANGO_SECRET_KEY` — заполнен сгенерированным значением, не пустой,
  без префикса `django-insecure-`.
- `DJANGO_ALLOWED_HOSTS` — перечислены hostname сервера, его FQDN и IP,
  по которым будут открывать сайт. Пустое значение = 400 на каждый запрос.
- `DATABASE_PASSWORD` — тот же `<пароль_БД>`, что задан в разделе 2.2.
- `IMPORT_DIR` — папка из раздела 1.2 (`C:\spravochnik\import`).

Проверка (значения читаются, DEBUG выключен):

```powershell
cd C:\spravochnik\app
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env diffsettings --output hash | Select-String "DEBUG|ALLOWED_HOSTS"
```

`DEBUG = False`, `ALLOWED_HOSTS` — непустой список.

---

## 5. Миграции, статика, суперпользователь

```powershell
cd C:\spravochnik\app
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env check --deploy
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env migrate
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env collectstatic --noinput
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env createsuperuser
```

Про `check --deploy`: предупреждения security.W004/W008/W012/W016
(HSTS, SSL-redirect, secure-cookies) и mail.E001 ожидаемы на этом шаге —
они про TLS и почту, которые вне объёма MVP (разделы 10, «вне объёма»).
Останавливаться нужно только на ошибках про `SECRET_KEY`, `ALLOWED_HOSTS`
или `DEBUG`.

Проверка:

- `migrate` завершается без ошибок, повторный запуск пишет
  `No migrations to apply`.
- после `collectstatic` существует `C:\spravochnik\app\staticfiles\staticfiles.json`.
- `createsuperuser` завёл учётку (запомнить логин/пароль).

---

## 6. Служба Waitress (NSSM)

### 6.1. Установка NSSM

```powershell
winget install NSSM.NSSM
```

Открыть новую консоль, проверка: `nssm version`.

### 6.2. Регистрация службы

```powershell
cd C:\spravochnik\app
.\deploy\install_service.ps1 -EnvFile C:\spravochnik\prod.env
```

По умолчанию: имя службы `spravochnik`, порт `8000`, venv
`C:\spravochnik\app\.venv`, логи `C:\spravochnik\app\logs`. Другой порт —
параметр `-Port`.

Скрипт идемпотентный: при повторном запуске (после `git pull` или правки
`prod.env`) он останавливает службу, переприменяет конфигурацию и
запускает снова.

Проверка (скрипт делает это сам, но перепроверить):

```powershell
Get-Service spravochnik                       # Status = Running
Invoke-WebRequest http://localhost:8000/ -UseBasicParsing | Select-Object StatusCode
```

`StatusCode` 200 либо 302 (редирект на логин) — оба означают, что Django
отвечает. Если служба не стартует — смотреть `C:\spravochnik\app\logs\service.err.log`.

---

## 7. Задачи Task Scheduler

```powershell
cd C:\spravochnik\app
.\deploy\schedule_tasks.ps1 -EnvFile C:\spravochnik\prod.env -RunAsUser <служебная учётка из 1.1>
```

Скрипт запросит пароль служебной учётки. Создаются три задачи
(идемпотентно — при повторе перерегистрируются):

| Задача | Что делает | Расписание (ADR-009) |
|---|---|---|
| `Spravochnik-ImportReference` | `import_1c --kind reference` | ежедневно 03:00 |
| `Spravochnik-ImportStock` | `import_1c --kind stock` | каждые 3 ч, 07:00–19:00 |
| `Spravochnik-Backup` | `deploy\backup.ps1` | ежедневно 03:30 |

> `Spravochnik-Backup` требует параметр `-BackupDir`. Скрипт
> `schedule_tasks.ps1` регистрирует её на путь по умолчанию из
> `backup.ps1` — **после регистрации** открыть задачу в планировщике и
> дописать в аргумент `-BackupDir <BACKUP>`, либо перерегистрировать
> задачу вручную (см. раздел 5 спеки). Проще: один раз отредактировать
> действие задачи `Spravochnik-Backup`, добавив `-BackupDir \\nas\backup\spravochnik`.

Проверка:

```powershell
Get-ScheduledTask -TaskName "Spravochnik-*" | Select-Object TaskName, State
```

Все три задачи в состоянии `Ready`. Тестовый прогон (нужен файл выгрузки
1С в `IMPORT_DIR` для импорта; для бэкапа — доступная `<BACKUP>`):

```powershell
Start-ScheduledTask -TaskName Spravochnik-Backup
Start-Sleep 20
Get-ScheduledTaskInfo -TaskName Spravochnik-Backup    # LastTaskResult = 0
Get-ChildItem <BACKUP>\spravochnik-*.dump             # появился файл дампа
```

---

## 8. Firewall

Открыть входящий порт Waitress только для локальной сети:

```powershell
New-NetFirewallRule -DisplayName "Spravochnik HTTP 8000" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 `
    -Profile Domain,Private
```

(порт заменить, если в разделе 6.2 использовался `-Port`.)

Проверка: с **другого компьютера в сети**

```powershell
Invoke-WebRequest http://<IP или hostname сервера>:8000/ -UseBasicParsing | Select-Object StatusCode
```

200 или 302. Если висит/таймаут — правило firewall или профиль сети
(Domain/Private vs Public).

---

## 9. DNS — два варианта

Чтобы открывать сайт по имени, а не по IP.

### Вариант A: есть AD с DNS

Администратор AD добавляет A-запись:
`spravochnik.<домен>` → IP сервера.

Проверка: `Resolve-DnsName spravochnik.<домен>` возвращает IP сервера;
`http://spravochnik.<домен>:8000/` открывается.
Добавить это имя в `DJANGO_ALLOWED_HOSTS` в `prod.env` и перезапустить
службу (`.\deploy\install_service.ps1 -EnvFile ...`).

### Вариант B: AD нет / статус неизвестен

Временное решение до прояснения статуса AD (см. handoff):

- статическая A-запись на роутере/`dnsmasq`, если он раздаёт DNS в сети;
- либо записи в `hosts` на машинах конструкторов (`C:\Windows\System32\drivers\etc\hosts`):
  `10.0.0.10   spravochnik`.

Проверка: `ping spravochnik` резолвится в IP сервера с машины
конструктора. Имя так же добавить в `DJANGO_ALLOWED_HOSTS`.

> Пометка: это решение временное. Постоянное — A-запись в AD DNS, когда
> подтвердится статус AD.

---

## 10. TLS — не в этом шаге

Сейчас трафик идёт по `http://` внутри локальной сети. Это сознательное
решение MVP (ADR-013 помечает Nginx/TLS как «впереди»).

План на будущее (отдельная сессия, когда подтвердится AD Certificate
Services): выпустить сертификат на `spravochnik.<домен>` в AD CS,
поставить Nginx как терминатор TLS перед Waitress (`proxy_pass` на
`http://127.0.0.1:8000`), закрыть порт 8000 в firewall для всех, кроме
localhost, включить в Django `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`. Конкретных команд здесь нет —
выполнять нечего, пока не подтверждён AD CS. Ссылка: ADR-013.

---

## 11. Критерии «деплой успешен»

Отметить все пункты:

- [ ] `Get-Service spravochnik` → `Status: Running`.
- [ ] Страница поиска открывается с другого компьютера в сети по
      IP/hostname сервера (200 или 302 на `/`, после входа —
      список номенклатуры).
- [ ] Вход под суперпользователем из раздела 5 работает, админка
      `/admin/` открывается.
- [ ] `Get-ScheduledTask -TaskName "Spravochnik-*"` показывает все три
      задачи в состоянии `Ready`.
- [ ] `Start-ScheduledTask -TaskName Spravochnik-Backup` → через
      `Get-ScheduledTaskInfo` `LastTaskResult = 0`, в `<BACKUP>` появился
      файл `spravochnik-<дата>.dump`, в `<BACKUP>\backup.log` строка `[OK]`.
- [ ] После перезагрузки сервера служба поднимается сама
      (`Start SERVICE_AUTO_START`), сайт снова доступен без ручных действий.

---

## Обслуживание

**Обновление кода:**

```powershell
cd C:\spravochnik\app
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env migrate
.\deploy\run_manage.ps1 -EnvFile C:\spravochnik\prod.env collectstatic --noinput
.\deploy\install_service.ps1 -EnvFile C:\spravochnik\prod.env   # перезапуск службы
```

**Логи:**
- служба — `C:\spravochnik\app\logs\service.out.log` / `service.err.log`
  (ротация по 10 МБ, включена в `install_service.ps1`);
- импорт — `ImportLog` / `ImportRowError` в БД (админка) + вывод задачи в
  «Журнале» планировщика;
- бэкап — `<BACKUP>\backup.log`.

**Восстановление из дампа** (формат custom, `-Fc`):

```powershell
$env:PGPASSWORD = '<пароль postgres>'
# при полном восстановлении: пересоздать БД пустой (см. 2.2), затем
pg_restore -U spravochnik -h localhost -d spravochnik --no-owner <BACKUP>\spravochnik-<дата>.dump
Remove-Item Env:PGPASSWORD
```
