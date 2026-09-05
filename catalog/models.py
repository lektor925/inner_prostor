"""
Модели справочника номенклатуры.

Схема соответствует ADR-008 (assets/adr-spravochnik-nomenklatury.md) с
поправками ADR-014 по итогам валидации на живых данных копии `UPP_PL_VLAD`:

- единая единица измерения (`base_unit`) — БазоваяЕдиницаИзмерения и
  ЕдиницаХраненияОстатков в этой базе всегда совпадают;
- папки — не отдельный справочник 1С, а элементы Справочник.Номенклатура с
  ЭтоГруппа = Истина (реквизит Родитель); в Django всё равно отдельная
  модель Folder — на стороне Django природа источника не важна.
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models


class NomenclatureKind(models.Model):
    """Вид номенклатуры (1С: Справочник.ВидыНоменклатуры)."""

    code_1c = models.CharField('код 1С', max_length=20, unique=True)
    name = models.CharField('наименование', max_length=255)

    class Meta:
        verbose_name = 'вид номенклатуры'
        verbose_name_plural = 'виды номенклатуры'
        ordering = ['name']

    def __str__(self):
        return self.name


class Folder(models.Model):
    """
    Папка справочника «Номенклатура» (ADR-003: элемент того же справочника
    1С с ЭтоГруппа = Истина, здесь — самостоятельная модель).
    """

    code_1c = models.CharField('код 1С', max_length=20, unique=True)
    name = models.CharField('наименование', max_length=255)
    parent = models.ForeignKey(
        'self',
        verbose_name='родительская папка',
        null=True,
        blank=True,
        related_name='children',
        on_delete=models.PROTECT,
    )

    class Meta:
        verbose_name = 'папка номенклатуры'
        verbose_name_plural = 'папки номенклатуры'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def full_path(self):
        """
        Путь от корня: «Оборудование / Холодильное / Компрессоры». Идёт вверх
        по `parent`; глубина дерева 1С — единицы уровней (ADR-003). Обход в
        Python, как и folders_by_hierarchy в views: сотни папок — не узкое
        место. Для карточки позиции: `select_related('folder__parent__…')` в
        NomenclatureDetailView гасит запросы на типовой глубине.
        """
        names = []
        node = self
        seen = set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            names.append(node.name)
            node = node.parent
        return ' / '.join(reversed(names))


class Nomenclature(models.Model):
    """Позиция номенклатуры (1С: Справочник.Номенклатура, не группа)."""

    code_1c = models.CharField(
        'код 1С', max_length=20, unique=True, db_index=True
    )
    name = models.CharField('наименование', max_length=255)
    article = models.CharField('артикул', max_length=100, blank=True)
    base_unit = models.CharField('единица измерения', max_length=50, blank=True)

    kind = models.ForeignKey(
        NomenclatureKind,
        verbose_name='вид номенклатуры',
        null=True,
        blank=True,
        related_name='items',
        on_delete=models.PROTECT,
    )
    folder = models.ForeignKey(
        Folder,
        verbose_name='папка',
        null=True,
        blank=True,
        related_name='items',
        on_delete=models.PROTECT,
    )

    is_active = models.BooleanField(
        'актуальна', default=True,
        help_text='False — помечена на удаление в 1С (ADR-009)',
    )
    updated_at = models.DateTimeField('обновлено при импорте', auto_now=True)

    # Денормализация по остаткам (ADR-005/ADR-012), пересчитывается по итогу
    # импорта остатков management-командой import_1c.
    total_available = models.DecimalField(
        'доступный остаток (сумма по видимым складам)',
        max_digits=14, decimal_places=3, default=0,
    )
    is_stale = models.BooleanField(
        'залежалось',
        default=False,
        help_text='total_available >= порогу по ThresholdRule (ADR-004)',
    )

    class Meta:
        verbose_name = 'номенклатура'
        verbose_name_plural = 'номенклатура'
        ordering = ['name']
        indexes = [
            GinIndex(
                name='nomenclature_name_trgm',
                fields=['name'],
                opclasses=['gin_trgm_ops'],
            ),
            GinIndex(
                name='nomenclature_article_trgm',
                fields=['article'],
                opclasses=['gin_trgm_ops'],
            ),
            models.Index(fields=['is_active']),
            models.Index(fields=['is_stale']),
        ]

    def __str__(self):
        return f'{self.name} ({self.code_1c})'


class Warehouse(models.Model):
    """Склад (1С: Справочник.Склады)."""

    code_1c = models.CharField('код 1С', max_length=20, unique=True)
    name = models.CharField('наименование', max_length=255)
    is_visible = models.BooleanField(
        'показывать конструкторам',
        default=True,
        help_text=(
            'ADR-005/ADR-009: исключить недоступные площадки '
            '(например, удалённые стройки) из витрины остатков'
        ),
    )

    class Meta:
        verbose_name = 'склад'
        verbose_name_plural = 'склады'
        ordering = ['name']

    def __str__(self):
        return self.name


class StockBalance(models.Model):
    """
    Остаток номенклатуры на складе (ADR-005): физический остаток и резерв
    раздельно, доступный остаток = qty - reserved считается в Django.
    """

    nomenclature = models.ForeignKey(
        Nomenclature, verbose_name='номенклатура',
        related_name='stock_balances', on_delete=models.CASCADE,
    )
    warehouse = models.ForeignKey(
        Warehouse, verbose_name='склад',
        related_name='stock_balances', on_delete=models.CASCADE,
    )
    qty = models.DecimalField('остаток', max_digits=14, decimal_places=3, default=0)
    reserved = models.DecimalField(
        'в резерве', max_digits=14, decimal_places=3, default=0,
    )
    updated_at = models.DateTimeField('обновлено при импорте', auto_now=True)

    class Meta:
        verbose_name = 'остаток на складе'
        verbose_name_plural = 'остатки на складах'
        constraints = [
            models.UniqueConstraint(
                fields=['nomenclature', 'warehouse'],
                name='unique_stock_balance_per_warehouse',
            )
        ]

    def __str__(self):
        return f'{self.nomenclature} @ {self.warehouse}: {self.qty}'

    @property
    def available(self):
        return self.qty - self.reserved


class ThresholdRule(models.Model):
    """
    Порог «залежавшегося» остатка (ADR-004). Ровно одно из трёх полей
    (kind / folder / nomenclature) заполнено. Приоритет применения:
    nomenclature > folder > kind — реализуется на стороне логики пересчёта
    is_stale, не в модели.
    """

    kind = models.ForeignKey(
        NomenclatureKind, verbose_name='вид номенклатуры',
        null=True, blank=True, related_name='threshold_rules',
        on_delete=models.CASCADE,
    )
    folder = models.ForeignKey(
        Folder, verbose_name='папка',
        null=True, blank=True, related_name='threshold_rules',
        on_delete=models.CASCADE,
    )
    nomenclature = models.ForeignKey(
        Nomenclature, verbose_name='номенклатура',
        null=True, blank=True, related_name='threshold_rules',
        on_delete=models.CASCADE,
    )
    threshold = models.DecimalField(
        'порог (в базовой ЕИ)', max_digits=14, decimal_places=3,
    )

    class Meta:
        verbose_name = 'правило порога остатков'
        verbose_name_plural = 'правила порога остатков'
        constraints = [
            models.CheckConstraint(
                name='threshold_rule_exactly_one_target',
                condition=(
                    (
                        models.Q(kind__isnull=False)
                        & models.Q(folder__isnull=True)
                        & models.Q(nomenclature__isnull=True)
                    )
                    | (
                        models.Q(kind__isnull=True)
                        & models.Q(folder__isnull=False)
                        & models.Q(nomenclature__isnull=True)
                    )
                    | (
                        models.Q(kind__isnull=True)
                        & models.Q(folder__isnull=True)
                        & models.Q(nomenclature__isnull=False)
                    )
                ),
            )
        ]

    def clean(self):
        targets = [self.kind_id, self.folder_id, self.nomenclature_id]
        if sum(1 for t in targets if t is not None) != 1:
            raise ValidationError(
                'Заполните ровно одно из полей: вид, папка или номенклатура.'
            )

    def __str__(self):
        target = self.nomenclature or self.folder or self.kind
        return f'{target}: {self.threshold}'


class Request(models.Model):
    """Заявка конструктора на новую позицию номенклатуры (ADR-007/ADR-012)."""

    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        APPROVED = 'approved', 'Одобрена'
        ENTERED = 'entered', 'Заведена в 1С'
        CLOSED = 'closed', 'Закрыта'
        REJECTED = 'rejected', 'Отклонена'

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='автор',
        related_name='requests', on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField('создана', auto_now_add=True)

    description = models.TextField('описание: что нужно и зачем')

    # Антидубль-шаг формы (ADR-007): позиция, отмеченная «да, это оно».
    similar_to = models.ForeignKey(
        Nomenclature, verbose_name='похожая позиция (нашлась)',
        null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
    )
    # Позиции, отмеченные «просмотрел, не подходит» — доказательство, что
    # конструктор реально проверил похожие перед подачей заявки.
    reviewed_not_matching = models.ManyToManyField(
        Nomenclature, verbose_name='просмотрено и не подошло',
        blank=True, related_name='+',
    )

    # Структурные поля — снижают число уточняющих переписок с Владимиром.
    guessed_kind = models.ForeignKey(
        NomenclatureKind, verbose_name='предполагаемый вид',
        null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
    )
    unit = models.CharField('единица измерения', max_length=50, blank=True)
    gost_or_article = models.CharField(
        'ГОСТ / артикул', max_length=255, blank=True,
    )
    analog_url = models.URLField('ссылка на аналог у поставщика', blank=True)

    status = models.CharField(
        'статус', max_length=20, choices=Status.choices, default=Status.NEW,
    )
    resolution_comment = models.TextField('комментарий к решению', blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='кто принял решение',
        null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
    )
    resolved_at = models.DateTimeField('решение принято', null=True, blank=True)

    class Meta:
        verbose_name = 'заявка'
        verbose_name_plural = 'заявки'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заявка №{self.pk} от {self.author}: {self.description[:50]}'


class ImportLog(models.Model):
    """Лог запуска импорта из 1С (ADR-009/ADR-014)."""

    class Kind(models.TextChoices):
        REFERENCE = 'reference', 'Справочники (номенклатура/виды/папки)'
        STOCK = 'stock', 'Остатки'

    class Status(models.TextChoices):
        OK = 'ok', 'Успешно'
        PARTIAL = 'partial', 'Частично'
        ERROR = 'error', 'Ошибка'

    started_at = models.DateTimeField('запущен', auto_now_add=True)
    finished_at = models.DateTimeField('завершён', null=True, blank=True)
    kind = models.CharField('тип', max_length=20, choices=Kind.choices)
    source_file = models.CharField('файл-источник', max_length=500)
    file_hash = models.CharField(
        'хэш файла', max_length=64, blank=True,
        help_text='защита от повторного импорта того же файла',
    )
    status = models.CharField(
        'статус', max_length=20, choices=Status.choices, default=Status.OK,
    )
    rows_total = models.PositiveIntegerField('строк всего', default=0)
    rows_created = models.PositiveIntegerField('создано', default=0)
    rows_updated = models.PositiveIntegerField('обновлено', default=0)
    rows_failed = models.PositiveIntegerField('не легло', default=0)
    error_text = models.TextField('текст ошибки', blank=True)

    class Meta:
        verbose_name = 'лог импорта'
        verbose_name_plural = 'логи импорта'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.get_kind_display()} {self.started_at:%Y-%m-%d %H:%M} — {self.status}'


class ImportRowError(models.Model):
    """Построчная ошибка импорта — для разбора, какие позиции не легли."""

    import_log = models.ForeignKey(
        ImportLog, verbose_name='импорт',
        related_name='row_errors', on_delete=models.CASCADE,
    )
    row_code = models.CharField('код позиции', max_length=50)
    reason = models.TextField('причина')

    class Meta:
        verbose_name = 'ошибка строки импорта'
        verbose_name_plural = 'ошибки строк импорта'

    def __str__(self):
        return f'{self.row_code}: {self.reason[:80]}'
