"""
management-команда: import_1c (ADR-009/ADR-010).

Забирает JSON-файл выгрузки 1С из settings.IMPORT_DIR, применяет его к
моделям catalog идемпотентным update_or_create по code_1c, пишет ImportLog
+ ImportRowError, защищена lock-файлом и хэшем от повторного импорта.

Контракт JSON (задаётся здесь, стороне 1С — ориентир для обработки-выгрузки,
поля см. также ADR-014 — валидированные запросы 1С):

--kind reference (файл nomenclature.json по умолчанию):
{
  "kinds": [{"code": "00000001", "name": "Товар"}, ...],
  "folders": [
      {"code": "00000005", "name": "Крепёж", "parent_code": null}, ...
  ],
  "nomenclature": [
      {
        "code": "00000123", "name": "...", "article": "...",
        "base_unit": "шт", "kind_code": "00000001",
        "folder_code": "00000005", "is_active": true
      }, ...
  ]
}

--kind stock (файл stock.json по умолчанию):
{
  "warehouses": [{"code": "000000007", "name": "ПРОСТОР-Л МАТЕРИАЛЫ"}, ...],
  "balances": [
      {
        "nomenclature_code": "00000123", "warehouse_code": "000000007",
        "qty": "10.000", "reserved": "0.000"
      }, ...
  ]
}

qty/reserved — строкой (сохраняет точность при парсинге в Decimal).
Остатки — полный снимок: StockBalance, не попавшие в файл, удаляются
(значит, остаток стал нулевым).
"""

import hashlib
import json
import os
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog.models import (
    Folder,
    ImportLog,
    ImportRowError,
    Nomenclature,
    NomenclatureKind,
    StockBalance,
    Warehouse,
)
from catalog.services.stock import recalculate_stock_flags

# Резкое падение объёма выгрузки против прошлого успешного импорта того же
# типа — сигнал битого/неполного файла, а не реальных массовых удалений.
MAX_DROP_RATIO = 0.2

# Лock старше этого — считаем зависшим от прежнего аварийно прерванного
# запуска и снимаем автоматически (Windows Task Scheduler не гарантирует
# cleanup при убийстве процесса).
STALE_LOCK_SECONDS = 6 * 3600

DEFAULT_FILENAMES = {
    'reference': 'nomenclature.json',
    'stock': 'stock.json',
}


def to_decimal(value, default='0'):
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


@contextmanager
def import_lock(kind, stdout):
    lock_path = Path(settings.IMPORT_DIR) / f'.import_{kind}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        age = time.time() - lock_path.stat().st_mtime
        if age < STALE_LOCK_SECONDS:
            raise CommandError(
                f'Импорт "{kind}" уже выполняется (lock-файл {lock_path}, '
                f'возраст {age:.0f}с). Если это не так — удалите файл вручную.'
            )
        stdout.write(
            f'Lock-файл {lock_path} старше {STALE_LOCK_SECONDS}с — '
            'считаю зависшим, снимаю.'
        )
        lock_path.unlink(missing_ok=True)

    lock_path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


class Command(BaseCommand):
    help = 'Импорт выгрузки 1С (справочники или остатки) в справочник номенклатуры.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--kind', required=True, choices=['reference', 'stock'],
            help='Тип выгрузки: reference (номенклатура/виды/папки) или stock (остатки).',
        )
        parser.add_argument(
            '--file', default=None,
            help='Путь к файлу выгрузки. По умолчанию — settings.IMPORT_DIR/<стандартное имя>.',
        )

    def handle(self, *args, **options):
        kind = options['kind']
        file_path = Path(options['file']) if options['file'] else (
            Path(settings.IMPORT_DIR) / DEFAULT_FILENAMES[kind]
        )

        if not file_path.exists():
            raise CommandError(f'Файл не найден: {file_path}')

        with import_lock(kind, self.stdout):
            self._run(kind, file_path)

    def _run(self, kind, file_path):
        raw = file_path.read_bytes()
        file_hash = hashlib.sha256(raw).hexdigest()

        already_ok = ImportLog.objects.filter(
            kind=kind, file_hash=file_hash, status=ImportLog.Status.OK,
        ).exists()
        if already_ok:
            self.stdout.write(self.style.WARNING(
                f'Файл {file_path.name} (hash {file_hash[:12]}…) уже был '
                'успешно импортирован — пропускаю.'
            ))
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._log_hard_failure(kind, file_path, file_hash, f'Битый JSON: {exc}')
            raise CommandError(f'Битый JSON в {file_path}: {exc}')

        if kind == 'reference':
            new_count = len(data.get('nomenclature', []))
        else:
            new_count = len(data.get('balances', []))

        guard_error = self._check_drop_guard(kind, new_count)
        if guard_error:
            self._log_hard_failure(kind, file_path, file_hash, guard_error)
            raise CommandError(guard_error)

        log = ImportLog.objects.create(
            kind=kind, source_file=str(file_path), file_hash=file_hash,
        )

        try:
            with transaction.atomic():
                if kind == 'reference':
                    stats, row_errors = self._import_reference(data)
                else:
                    stats, row_errors = self._import_stock(data)
        except Exception as exc:  # noqa: BLE001 — фиксируем в лог и падаем
            log.finished_at = timezone.now()
            log.status = ImportLog.Status.ERROR
            log.error_text = str(exc)
            log.save()
            raise CommandError(f'Импорт "{kind}" прерван: {exc}')

        log.finished_at = timezone.now()
        # rows_total — та же метрика, что использовалась в guard-проверке
        # объёма (len(nomenclature) / len(balances)), а не сумма всех
        # подсписков файла — иначе следующий guard сравнивает несопоставимые
        # числа и ложно срабатывает.
        log.rows_total = new_count
        log.rows_created = stats['created']
        log.rows_updated = stats['updated']
        log.rows_failed = stats['failed']
        log.status = ImportLog.Status.PARTIAL if stats['failed'] else ImportLog.Status.OK
        log.save()

        if row_errors:
            ImportRowError.objects.bulk_create([
                ImportRowError(import_log=log, row_code=code, reason=reason)
                for code, reason in row_errors
            ])

        if kind == 'stock':
            updated = recalculate_stock_flags()
            self.stdout.write(f'Пересчитаны флаги остатков у {updated} позиций.')

        self.stdout.write(self.style.SUCCESS(
            f'Импорт "{kind}" завершён: {stats["created"]} создано, '
            f'{stats["updated"]} обновлено, {stats["failed"]} с ошибками.'
        ))

    def _check_drop_guard(self, kind, new_count):
        prev = (
            ImportLog.objects
            .filter(kind=kind, status=ImportLog.Status.OK)
            .order_by('-started_at')
            .first()
        )
        if not prev or prev.rows_total == 0:
            return None
        min_allowed = prev.rows_total * (1 - MAX_DROP_RATIO)
        if new_count < min_allowed:
            return (
                f'Объём выгрузки резко упал: было {prev.rows_total}, '
                f'сейчас {new_count} (порог {min_allowed:.0f}). '
                'Похоже на неполный/битый файл — импорт остановлен без изменений.'
            )
        return None

    def _log_hard_failure(self, kind, file_path, file_hash, message):
        ImportLog.objects.create(
            kind=kind,
            source_file=str(file_path),
            file_hash=file_hash,
            finished_at=timezone.now(),
            status=ImportLog.Status.ERROR,
            error_text=message,
        )

    def _import_reference(self, data):
        row_errors = []
        created = updated = failed = 0

        kinds = data.get('kinds', [])
        folders = data.get('folders', [])
        nomenclature = data.get('nomenclature', [])
        total = len(kinds) + len(folders) + len(nomenclature)

        # --- виды ---
        for k in kinds:
            code, name = k.get('code'), k.get('name')
            if not code or not name:
                failed += 1
                row_errors.append((str(code), 'вид номенклатуры: нет code/name'))
                continue
            _, was_created = NomenclatureKind.objects.update_or_create(
                code_1c=code, defaults={'name': name},
            )
            created += was_created
            updated += not was_created

        # --- папки: сначала все узлы, потом связи parent ---
        for f in folders:
            code, name = f.get('code'), f.get('name')
            if not code or not name:
                failed += 1
                row_errors.append((str(code), 'папка: нет code/name'))
                continue
            _, was_created = Folder.objects.update_or_create(
                code_1c=code, defaults={'name': name},
            )
            created += was_created
            updated += not was_created

        folder_by_code = {f.code_1c: f for f in Folder.objects.all()}
        for f in folders:
            code, parent_code = f.get('code'), f.get('parent_code')
            if not parent_code or code not in folder_by_code:
                continue
            parent = folder_by_code.get(parent_code)
            if parent is None:
                failed += 1
                row_errors.append((code, f'родительская папка {parent_code} не найдена'))
                continue
            folder_obj = folder_by_code[code]
            if folder_obj.parent_id != parent.id:
                folder_obj.parent = parent
                folder_obj.save(update_fields=['parent'])

        # --- номенклатура ---
        kind_by_code = {k.code_1c: k for k in NomenclatureKind.objects.all()}
        seen_codes = set()
        for n in nomenclature:
            code, name = n.get('code'), n.get('name')
            if not code or not name:
                failed += 1
                row_errors.append((str(code), 'номенклатура: нет code/name'))
                continue
            if code in seen_codes:
                failed += 1
                row_errors.append((code, 'дубликат кода внутри файла'))
                continue
            seen_codes.add(code)

            kind_code = n.get('kind_code')
            folder_code = n.get('folder_code')
            kind_obj = kind_by_code.get(kind_code) if kind_code else None
            folder_obj = folder_by_code.get(folder_code) if folder_code else None

            _, was_created = Nomenclature.objects.update_or_create(
                code_1c=code,
                defaults={
                    'name': name,
                    'article': n.get('article') or '',
                    'base_unit': n.get('base_unit') or '',
                    'kind': kind_obj,
                    'folder': folder_obj,
                    'is_active': bool(n.get('is_active', True)),
                },
            )
            created += was_created
            updated += not was_created

            if kind_code and kind_obj is None:
                row_errors.append((code, f'вид номенклатуры {kind_code} не найден'))
            if folder_code and folder_obj is None:
                row_errors.append((code, f'папка {folder_code} не найдена'))

        return {'total': total, 'created': created, 'updated': updated, 'failed': failed}, row_errors

    def _import_stock(self, data):
        row_errors = []
        created = updated = failed = 0

        warehouses = data.get('warehouses', [])
        balances = data.get('balances', [])
        total = len(warehouses) + len(balances)

        for w in warehouses:
            code, name = w.get('code'), w.get('name')
            if not code or not name:
                failed += 1
                row_errors.append((str(code), 'склад: нет code/name'))
                continue
            _, was_created = Warehouse.objects.update_or_create(
                code_1c=code, defaults={'name': name},
            )
            created += was_created
            updated += not was_created

        warehouse_by_code = {w.code_1c: w for w in Warehouse.objects.all()}
        nomenclature_by_code = {
            n.code_1c: n for n in Nomenclature.objects.only('id', 'code_1c').all()
        }

        touched_ids = []
        for b in balances:
            ncode = b.get('nomenclature_code')
            wcode = b.get('warehouse_code')
            if not ncode or not wcode:
                failed += 1
                row_errors.append((str(ncode), 'остаток: нет nomenclature_code/warehouse_code'))
                continue

            nom = nomenclature_by_code.get(ncode)
            wh = warehouse_by_code.get(wcode)
            if nom is None:
                failed += 1
                row_errors.append((ncode, f'номенклатура не найдена (склад {wcode})'))
                continue
            if wh is None:
                failed += 1
                row_errors.append((ncode, f'склад {wcode} не найден'))
                continue

            obj, was_created = StockBalance.objects.update_or_create(
                nomenclature=nom, warehouse=wh,
                defaults={
                    'qty': to_decimal(b.get('qty')),
                    'reserved': to_decimal(b.get('reserved')),
                },
            )
            created += was_created
            updated += not was_created
            touched_ids.append(obj.id)

        # Полный снимок: то, чего не было в файле, считаем нулевым остатком.
        StockBalance.objects.exclude(id__in=touched_ids).delete()

        return {'total': total, 'created': created, 'updated': updated, 'failed': failed}, row_errors
