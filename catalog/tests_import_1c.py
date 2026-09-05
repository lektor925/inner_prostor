"""
Интеграционные тесты management-команды import_1c (ADR-009/ADR-010).

Гоняют команду целиком через call_command на временном IMPORT_DIR с
JSON-файлами, собранными строго по контракту из докстринга
catalog/management/commands/import_1c.py. Тем самым:

- фиксируют сам JSON-контракт (структура и имена полей), на который
  равняется onec_export/ВыгрузкаДляСправочника.bsl — если контракт
  поедет, красный тест поймает это раньше боевой базы 1С;
- покрывают рискованную логику: идемпотентность (skip по хэшу),
  guard на резкое падение объёма, lock-файл, построчные ошибки,
  полный снимок остатков с удалением исчезнувших строк, запуск
  пересчёта флагов остатков.
"""

import json
import os
import shutil
import tempfile
import time
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from catalog.models import (
    Folder,
    ImportLog,
    ImportRowError,
    Nomenclature,
    NomenclatureKind,
    StockBalance,
    ThresholdRule,
    Warehouse,
)


class ImportCommandTestCase(TestCase):
    """Общая обвязка: временный IMPORT_DIR, хелперы записи файла и запуска."""

    def setUp(self):
        self.import_dir = Path(tempfile.mkdtemp(prefix='import1c_test_'))
        self.addCleanup(shutil.rmtree, self.import_dir, ignore_errors=True)
        cm = override_settings(IMPORT_DIR=self.import_dir)
        cm.enable()
        self.addCleanup(cm.disable)

    def write_file(self, name, payload):
        path = self.import_dir / name
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        else:
            path.write_text(payload, encoding='utf-8')
        return path

    def run_import(self, kind, **kwargs):
        out = StringIO()
        call_command('import_1c', kind=kind, stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    # --- фабрики валидных payload'ов по контракту ---

    def reference_payload(self, nomenclature=None):
        return {
            'kinds': [
                {'code': 'K1', 'name': 'Товар'},
                {'code': 'K2', 'name': 'Материал'},
            ],
            'folders': [
                {'code': 'F1', 'name': 'Крепёж', 'parent_code': None},
                {'code': 'F2', 'name': 'Болты', 'parent_code': 'F1'},
            ],
            'nomenclature': nomenclature if nomenclature is not None else [
                {
                    'code': 'N1', 'name': 'Болт М10', 'article': 'BM10',
                    'base_unit': 'шт', 'kind_code': 'K1', 'folder_code': 'F2',
                    'is_active': True,
                },
                {
                    'code': 'N2', 'name': 'Гайка М10', 'article': '',
                    'base_unit': 'шт', 'kind_code': 'K1', 'folder_code': 'F2',
                    'is_active': True,
                },
                {
                    'code': 'N3', 'name': 'Лист 3мм', 'article': '',
                    'base_unit': 'м2', 'kind_code': 'K2', 'folder_code': None,
                    'is_active': False,
                },
            ],
        }

    def stock_payload(self, balances=None):
        return {
            'warehouses': [
                {'code': 'W1', 'name': 'Основной'},
                {'code': 'W2', 'name': 'Дальний'},
            ],
            'balances': balances if balances is not None else [
                {
                    'nomenclature_code': 'N1', 'warehouse_code': 'W1',
                    'qty': '10.000', 'reserved': '2.500',
                },
                {
                    'nomenclature_code': 'N2', 'warehouse_code': 'W1',
                    'qty': '5.000', 'reserved': '0.000',
                },
            ],
        }

    def import_reference_ok(self, payload=None):
        self.write_file('nomenclature.json', payload or self.reference_payload())
        return self.run_import('reference')


class ReferenceImportTests(ImportCommandTestCase):

    def test_creates_kinds_folders_and_nomenclature(self):
        self.import_reference_ok()

        self.assertEqual(NomenclatureKind.objects.count(), 2)
        self.assertEqual(Folder.objects.count(), 2)
        self.assertEqual(Nomenclature.objects.count(), 3)

        bolt = Nomenclature.objects.get(code_1c='N1')
        self.assertEqual(bolt.name, 'Болт М10')
        self.assertEqual(bolt.article, 'BM10')
        self.assertEqual(bolt.base_unit, 'шт')
        self.assertEqual(bolt.kind.code_1c, 'K1')
        self.assertEqual(bolt.folder.code_1c, 'F2')
        self.assertTrue(bolt.is_active)

    def test_is_active_false_is_respected(self):
        self.import_reference_ok()
        self.assertFalse(Nomenclature.objects.get(code_1c='N3').is_active)

    def test_links_folder_parent_by_code(self):
        self.import_reference_ok()

        child = Folder.objects.get(code_1c='F2')
        self.assertEqual(child.parent.code_1c, 'F1')
        self.assertEqual(child.full_path, 'Крепёж / Болты')

    def test_logs_success_with_row_counts(self):
        self.import_reference_ok()

        log = ImportLog.objects.get()
        self.assertEqual(log.kind, 'reference')
        self.assertEqual(log.status, ImportLog.Status.OK)
        self.assertEqual(log.rows_total, 3)          # len(nomenclature)
        self.assertEqual(log.rows_created, 2 + 2 + 3)  # kinds + folders + nom
        self.assertEqual(log.rows_failed, 0)
        self.assertIsNotNone(log.finished_at)
        self.assertNotEqual(log.file_hash, '')

    def test_second_run_updates_in_place_not_duplicates(self):
        self.import_reference_ok()

        payload = self.reference_payload()
        payload['nomenclature'][0]['name'] = 'Болт М10 оцинкованный'
        self.write_file('nomenclature.json', payload)
        self.run_import('reference')

        self.assertEqual(Nomenclature.objects.count(), 3)
        self.assertEqual(
            Nomenclature.objects.get(code_1c='N1').name, 'Болт М10 оцинкованный'
        )
        latest = ImportLog.objects.filter(status=ImportLog.Status.OK).latest('started_at')
        self.assertEqual(latest.rows_created, 0)
        self.assertEqual(latest.rows_updated, 2 + 2 + 3)

    def test_identical_file_second_run_is_skipped(self):
        out1 = self.import_reference_ok()
        self.assertNotIn('уже был', out1)

        out2 = self.run_import('reference')  # тот же файл, тот же хэш

        self.assertIn('уже был', out2)
        # skip происходит до создания второго лога
        self.assertEqual(ImportLog.objects.count(), 1)

    def test_row_without_name_is_counted_as_failed(self):
        payload = self.reference_payload(nomenclature=[
            {'code': 'N1', 'name': 'Болт', 'base_unit': 'шт'},
            {'code': 'N2', 'name': '', 'base_unit': 'шт'},
        ])
        self.write_file('nomenclature.json', payload)
        self.run_import('reference')

        self.assertFalse(Nomenclature.objects.filter(code_1c='N2').exists())
        log = ImportLog.objects.get()
        self.assertEqual(log.status, ImportLog.Status.PARTIAL)
        self.assertEqual(log.rows_failed, 1)
        err = ImportRowError.objects.get()
        self.assertEqual(err.import_log_id, log.id)
        self.assertIn('нет code/name', err.reason)

    def test_duplicate_code_in_file_second_row_fails(self):
        payload = self.reference_payload(nomenclature=[
            {'code': 'N1', 'name': 'Болт', 'base_unit': 'шт'},
            {'code': 'N1', 'name': 'Болт (дубль)', 'base_unit': 'шт'},
        ])
        self.write_file('nomenclature.json', payload)
        self.run_import('reference')

        self.assertEqual(Nomenclature.objects.get(code_1c='N1').name, 'Болт')
        self.assertIn(
            'дубликат кода',
            ImportRowError.objects.get().reason,
        )

    def test_unknown_kind_code_records_row_error_but_still_imports(self):
        # Текущее поведение: позиция создаётся с kind=None, ошибка строки
        # фиксируется, но rows_failed не растёт и статус остаётся ok.
        payload = self.reference_payload(nomenclature=[
            {'code': 'N1', 'name': 'Болт', 'base_unit': 'шт', 'kind_code': 'NOPE'},
        ])
        self.write_file('nomenclature.json', payload)
        self.run_import('reference')

        nom = Nomenclature.objects.get(code_1c='N1')
        self.assertIsNone(nom.kind)
        log = ImportLog.objects.get()
        self.assertEqual(log.status, ImportLog.Status.OK)
        self.assertEqual(log.rows_failed, 0)
        self.assertIn('вид номенклатуры NOPE не найден', ImportRowError.objects.get().reason)

    def test_malformed_json_raises_and_logs_error(self):
        self.write_file('nomenclature.json', '{ это не json ')

        with self.assertRaises(CommandError):
            self.run_import('reference')

        log = ImportLog.objects.get()
        self.assertEqual(log.status, ImportLog.Status.ERROR)
        self.assertIn('JSON', log.error_text)

    def test_missing_file_raises(self):
        with self.assertRaises(CommandError):
            self.run_import('reference')
        self.assertEqual(ImportLog.objects.count(), 0)

    def test_explicit_file_option_is_honoured(self):
        path = self.write_file('custom.json', self.reference_payload())
        self.run_import('reference', file=str(path))
        self.assertEqual(Nomenclature.objects.count(), 3)


class DropGuardTests(ImportCommandTestCase):

    def test_first_import_is_never_blocked(self):
        payload = self.reference_payload(nomenclature=[
            {'code': f'N{i}', 'name': f'Поз {i}', 'base_unit': 'шт'}
            for i in range(3)
        ])
        self.write_file('nomenclature.json', payload)
        self.run_import('reference')
        self.assertEqual(Nomenclature.objects.count(), 3)

    def test_shrunken_file_is_blocked_without_data_change(self):
        big = self.reference_payload(nomenclature=[
            {'code': f'N{i}', 'name': f'Поз {i}', 'base_unit': 'шт'}
            for i in range(10)
        ])
        self.write_file('nomenclature.json', big)
        self.run_import('reference')

        tiny = self.reference_payload(nomenclature=[
            {'code': 'N0', 'name': 'Поз 0', 'base_unit': 'шт'},
        ])
        self.write_file('nomenclature.json', tiny)
        with self.assertRaises(CommandError):
            self.run_import('reference')

        self.assertEqual(Nomenclature.objects.count(), 10)  # без изменений
        self.assertEqual(
            ImportLog.objects.filter(status=ImportLog.Status.ERROR).count(), 1
        )

    def test_moderate_shrink_within_threshold_passes(self):
        big = self.reference_payload(nomenclature=[
            {'code': f'N{i}', 'name': f'Поз {i}', 'base_unit': 'шт'}
            for i in range(10)
        ])
        self.write_file('nomenclature.json', big)
        self.run_import('reference')

        smaller = self.reference_payload(nomenclature=[
            {'code': f'N{i}', 'name': f'Поз {i} (обновлено)', 'base_unit': 'шт'}
            for i in range(9)  # -10%, порог -80% — guard пропускает
        ])
        self.write_file('nomenclature.json', smaller)
        self.run_import('reference')

        latest = ImportLog.objects.filter(
            kind='reference', status=ImportLog.Status.OK
        ).latest('started_at')
        self.assertEqual(latest.rows_total, 9)
        # Импорт справочников аддитивный (не снимок): N9 из первого файла
        # остаётся, 9 позиций обновлены на месте.
        self.assertEqual(Nomenclature.objects.count(), 10)
        self.assertEqual(
            Nomenclature.objects.get(code_1c='N0').name, 'Поз 0 (обновлено)'
        )


class LockFileTests(ImportCommandTestCase):

    def lock_path(self, kind='reference'):
        return self.import_dir / f'.import_{kind}.lock'

    def test_fresh_lock_blocks_run(self):
        self.write_file('nomenclature.json', self.reference_payload())
        self.lock_path().write_text('123')

        with self.assertRaises(CommandError):
            self.run_import('reference')
        self.assertEqual(Nomenclature.objects.count(), 0)

    def test_stale_lock_is_removed_and_run_proceeds(self):
        self.write_file('nomenclature.json', self.reference_payload())
        lock = self.lock_path()
        lock.write_text('123')
        old = time.time() - 7 * 3600  # STALE_LOCK_SECONDS = 6ч
        os.utime(lock, (old, old))

        out = self.run_import('reference')

        self.assertIn('снимаю', out)
        self.assertEqual(Nomenclature.objects.count(), 3)

    def test_lock_is_released_after_successful_run(self):
        self.import_reference_ok()
        self.assertFalse(self.lock_path().exists())


class StockImportTests(ImportCommandTestCase):

    def setUp(self):
        super().setUp()
        self.import_reference_ok()  # номенклатура для привязки остатков

    def test_creates_warehouses_and_balances(self):
        self.write_file('stock.json', self.stock_payload())
        self.run_import('stock')

        self.assertEqual(Warehouse.objects.count(), 2)
        self.assertEqual(StockBalance.objects.count(), 2)

        sb = StockBalance.objects.get(
            nomenclature__code_1c='N1', warehouse__code_1c='W1'
        )
        self.assertEqual(sb.qty, Decimal('10.000'))
        self.assertEqual(sb.reserved, Decimal('2.500'))

    def test_qty_as_string_keeps_precision(self):
        self.write_file('stock.json', self.stock_payload(balances=[
            {'nomenclature_code': 'N1', 'warehouse_code': 'W1',
             'qty': '123.456', 'reserved': '0.001'},
        ]))
        self.run_import('stock')

        sb = StockBalance.objects.get()
        self.assertEqual(sb.qty, Decimal('123.456'))
        self.assertEqual(sb.reserved, Decimal('0.001'))

    def test_invalid_qty_falls_back_to_zero(self):
        self.write_file('stock.json', self.stock_payload(balances=[
            {'nomenclature_code': 'N1', 'warehouse_code': 'W1',
             'qty': 'мусор', 'reserved': None},
        ]))
        self.run_import('stock')

        sb = StockBalance.objects.get()
        self.assertEqual(sb.qty, Decimal('0'))
        self.assertEqual(sb.reserved, Decimal('0'))

    def test_full_snapshot_deletes_rows_absent_from_new_file(self):
        # 5 строк в первом снимке, 4 во втором — падение 20%, в пределах
        # guard'а (порог 20%), поэтому удаление исчезнувшей строки реально
        # проверяется, а не маскируется отказом по guard.
        five = [
            {'nomenclature_code': 'N1', 'warehouse_code': 'W1', 'qty': '1.000', 'reserved': '0.000'},
            {'nomenclature_code': 'N1', 'warehouse_code': 'W2', 'qty': '2.000', 'reserved': '0.000'},
            {'nomenclature_code': 'N2', 'warehouse_code': 'W1', 'qty': '3.000', 'reserved': '0.000'},
            {'nomenclature_code': 'N2', 'warehouse_code': 'W2', 'qty': '4.000', 'reserved': '0.000'},
            {'nomenclature_code': 'N3', 'warehouse_code': 'W1', 'qty': '5.000', 'reserved': '0.000'},
        ]
        self.write_file('stock.json', self.stock_payload(balances=five))
        self.run_import('stock')
        self.assertEqual(StockBalance.objects.count(), 5)

        # новый снимок без строки N3@W1
        self.write_file('stock.json', self.stock_payload(balances=five[:4]))
        self.run_import('stock')

        self.assertEqual(StockBalance.objects.count(), 4)
        self.assertFalse(
            StockBalance.objects.filter(
                nomenclature__code_1c='N3', warehouse__code_1c='W1'
            ).exists()
        )

    def test_unknown_nomenclature_code_is_row_error(self):
        self.write_file('stock.json', self.stock_payload(balances=[
            {'nomenclature_code': 'НЕТ ТАКОЙ', 'warehouse_code': 'W1',
             'qty': '1.000', 'reserved': '0.000'},
        ]))
        self.run_import('stock')

        self.assertEqual(StockBalance.objects.count(), 0)
        log = ImportLog.objects.filter(kind='stock').get()
        self.assertEqual(log.status, ImportLog.Status.PARTIAL)
        self.assertEqual(log.rows_failed, 1)
        self.assertIn('номенклатура не найдена', ImportRowError.objects.get().reason)

    def test_unknown_warehouse_code_is_row_error(self):
        self.write_file('stock.json', self.stock_payload(balances=[
            {'nomenclature_code': 'N1', 'warehouse_code': 'НЕТ',
             'qty': '1.000', 'reserved': '0.000'},
        ]))
        self.run_import('stock')

        self.assertEqual(StockBalance.objects.count(), 0)
        self.assertIn('склад НЕТ не найден', ImportRowError.objects.get().reason)

    def test_recalculates_stock_flags_after_import(self):
        bolt = Nomenclature.objects.get(code_1c='N1')
        ThresholdRule.objects.create(nomenclature=bolt, threshold=Decimal('5.000'))

        self.write_file('stock.json', self.stock_payload(balances=[
            {'nomenclature_code': 'N1', 'warehouse_code': 'W1',
             'qty': '20.000', 'reserved': '3.000'},
        ]))
        out = self.run_import('stock')

        bolt.refresh_from_db()
        self.assertEqual(bolt.total_available, Decimal('17.000'))  # 20 - 3
        self.assertTrue(bolt.is_stale)                             # 17 >= 5
        self.assertIn('Пересчитаны флаги остатков', out)

    def test_reference_drop_guard_and_stock_guard_are_independent(self):
        # импорт остатков не должен влиять на guard справочников и наоборот
        self.write_file('stock.json', self.stock_payload())
        self.run_import('stock')

        ref_log = ImportLog.objects.filter(
            kind='reference', status=ImportLog.Status.OK
        ).get()
        stock_log = ImportLog.objects.filter(
            kind='stock', status=ImportLog.Status.OK
        ).get()
        self.assertEqual(ref_log.rows_total, 3)
        self.assertEqual(stock_log.rows_total, 2)
