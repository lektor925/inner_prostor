from django.contrib import admin

from .models import (
    Folder,
    ImportLog,
    ImportRowError,
    Nomenclature,
    NomenclatureKind,
    Request,
    StockBalance,
    ThresholdRule,
    Warehouse,
)


@admin.register(NomenclatureKind)
class NomenclatureKindAdmin(admin.ModelAdmin):
    list_display = ('name', 'code_1c')
    search_fields = ('name', 'code_1c')


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'code_1c', 'parent')
    search_fields = ('name', 'code_1c')
    list_filter = ('parent',)


class StockBalanceInline(admin.TabularInline):
    model = StockBalance
    extra = 0
    readonly_fields = ('warehouse', 'qty', 'reserved', 'updated_at')
    can_delete = False


@admin.register(Nomenclature)
class NomenclatureAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'code_1c', 'article', 'kind', 'folder',
        'total_available', 'is_stale', 'is_active',
    )
    list_filter = ('is_active', 'is_stale', 'kind', 'folder')
    search_fields = ('name', 'article', 'code_1c')
    readonly_fields = ('total_available', 'is_stale', 'updated_at')
    inlines = [StockBalanceInline]


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'code_1c', 'is_visible')
    list_editable = ('is_visible',)
    search_fields = ('name', 'code_1c')


@admin.register(ThresholdRule)
class ThresholdRuleAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'kind', 'folder', 'nomenclature', 'threshold')
    autocomplete_fields = ('nomenclature',)


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'author', 'status', 'created_at', 'resolved_at')
    list_filter = ('status', 'created_at')
    search_fields = ('description', 'gost_or_article', 'author__username')
    autocomplete_fields = ('similar_to', 'reviewed_not_matching', 'guessed_kind')
    readonly_fields = ('created_at',)


class ImportRowErrorInline(admin.TabularInline):
    model = ImportRowError
    extra = 0
    readonly_fields = ('row_code', 'reason')
    can_delete = False


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = (
        'kind', 'started_at', 'finished_at', 'status',
        'rows_total', 'rows_created', 'rows_updated', 'rows_failed',
    )
    list_filter = ('kind', 'status')
    readonly_fields = [f.name for f in ImportLog._meta.fields]
    inlines = [ImportRowErrorInline]
