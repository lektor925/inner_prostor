"""
Пересчёт денормализованных полей Nomenclature.total_available / is_stale
после импорта остатков (ADR-005/ADR-012).

available = сумма (qty - reserved) по StockBalance на видимых складах,
не бывает отрицательной (перерезерв — аномалия учёта, не показываем как
"глубокий минус").

is_stale = available >= порог, порог берётся из ThresholdRule с приоритетом
nomenclature > folder > kind (ADR-004). Нет подходящего правила — не
залежалось (не с чем сравнивать).
"""

from decimal import Decimal

from django.db.models import Q, Sum

from catalog.models import Nomenclature, ThresholdRule


def recalculate_stock_flags(nomenclature_ids=None, batch_size=1000):
    """
    Пересчитывает total_available/is_stale для всей номенклатуры, либо для
    подмножества по id (используется после импорта остатков — но по
    умолчанию берётся вся номенклатура, т.к. импорт остатков — полный
    снимок и мог обнулить остатки для позиций, которых в файле не было).

    Возвращает число обновлённых строк.
    """
    qs = Nomenclature.objects.all()
    if nomenclature_ids is not None:
        qs = qs.filter(id__in=nomenclature_ids)

    qs = qs.annotate(
        stock_sum=Sum(
            'stock_balances__qty',
            filter=Q(stock_balances__warehouse__is_visible=True),
        ),
        reserved_sum=Sum(
            'stock_balances__reserved',
            filter=Q(stock_balances__warehouse__is_visible=True),
        ),
    ).only('id', 'kind_id', 'folder_id', 'total_available', 'is_stale')

    threshold_by_nomenclature = dict(
        ThresholdRule.objects.filter(nomenclature__isnull=False)
        .values_list('nomenclature_id', 'threshold')
    )
    threshold_by_folder = dict(
        ThresholdRule.objects.filter(folder__isnull=False)
        .values_list('folder_id', 'threshold')
    )
    threshold_by_kind = dict(
        ThresholdRule.objects.filter(kind__isnull=False)
        .values_list('kind_id', 'threshold')
    )

    to_update = []
    updated_count = 0
    for nom in qs.iterator(chunk_size=batch_size):
        available = (nom.stock_sum or Decimal('0')) - (nom.reserved_sum or Decimal('0'))
        if available < 0:
            available = Decimal('0')

        threshold = (
            threshold_by_nomenclature.get(nom.id)
            or threshold_by_folder.get(nom.folder_id)
            or threshold_by_kind.get(nom.kind_id)
        )
        is_stale = threshold is not None and available >= threshold

        if nom.total_available != available or nom.is_stale != is_stale:
            nom.total_available = available
            nom.is_stale = is_stale
            to_update.append(nom)

        if len(to_update) >= batch_size:
            Nomenclature.objects.bulk_update(
                to_update, ['total_available', 'is_stale']
            )
            updated_count += len(to_update)
            to_update = []

    if to_update:
        Nomenclature.objects.bulk_update(
            to_update, ['total_available', 'is_stale']
        )
        updated_count += len(to_update)

    return updated_count
