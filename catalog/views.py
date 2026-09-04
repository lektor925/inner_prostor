import difflib

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView

from .forms import RequestForm
from .models import Folder, Nomenclature, NomenclatureKind, Request, StockBalance


def folders_by_hierarchy():
    """
    Плоский список папок (ADR-002: до сотен строк — рекурсия в Python, а не
    в SQL, не узкое место), упорядоченный обходом дерева: корни по имени,
    под каждым — его дети по имени, и так рекурсивно. Нужен для фасета
    «папка» в списке номенклатуры (ADR-003) — плоский алфавитный список без
    учёта иерархии не даёт понять, куда вложена нужная папка.

    Возвращает список пар (folder, depth).
    """
    folders = list(Folder.objects.select_related('parent').order_by('name'))
    children_by_parent = {}
    for folder in folders:
        children_by_parent.setdefault(folder.parent_id, []).append(folder)

    result = []

    def walk(parent_id, depth):
        for folder in children_by_parent.get(parent_id, []):
            result.append((folder, depth))
            walk(folder.id, depth + 1)

    walk(None, 0)
    return result


class NomenclatureListView(ListView):
    model = Nomenclature
    template_name = 'catalog/nomenclature_list.html'
    context_object_name = 'nomenclature_list'
    paginate_by = 50

    def get_queryset(self):
        qs = Nomenclature.objects.filter(is_active=True)

        kind_id = self.request.GET.get('kind')
        if kind_id and kind_id.isdigit():
            qs = qs.filter(kind_id=kind_id)

        folder_id = self.request.GET.get('folder')
        if folder_id and folder_id.isdigit():
            qs = qs.filter(folder_id=folder_id)

        if self.request.GET.get('stale'):
            qs = qs.filter(is_stale=True)

        if self.request.GET.get('in_stock'):
            qs = qs.filter(total_available__gt=0)

        query = self.request.GET.get('q', '').strip()
        if query:
            qs = self._search(qs, query)
        else:
            qs = qs.order_by('name')

        return qs

    def get_context_data(self, **kwargs):
        # Фасеты (ADR-006): значения для выбора фильтров + текущее состояние
        # GET-параметров, чтобы шаблон мог вернуть форму в прежнем виде и
        # сохранить фильтры в ссылках пагинации.
        context = super().get_context_data(**kwargs)
        context['kinds'] = NomenclatureKind.objects.all()
        context['folders'] = [
            (folder, '— ' * depth + folder.name)
            for folder, depth in folders_by_hierarchy()
        ]
        context['current'] = {
            'q': self.request.GET.get('q', ''),
            'kind': self.request.GET.get('kind', ''),
            'folder': self.request.GET.get('folder', ''),
            'stale': self.request.GET.get('stale', ''),
            'in_stock': self.request.GET.get('in_stock', ''),
        }
        # Querystring для ссылок пагинации: те же фильтры, но без текущего
        # `page` — иначе он дублируется при переходе между страницами.
        querystring = self.request.GET.copy()
        querystring.pop('page', None)
        context['querystring'] = querystring.urlencode()
        return context

    def _search(self, qs, query):
        """
        Поиск по названию и артикулу (ADR-006).

        На Postgres — полнотекстовый поиск (русская морфология) + триграммное
        сходство для опечаток/иных написаний, сортировка по релевантности.
        На остальных бэкендах (sqlite — только для локальной разработки без
        Postgres, см. config/settings.py) — упрощённый `icontains`, без
        ранжирования: FTS/pg_trgm там недоступны.
        """
        if connection.vendor != 'postgresql':
            # sqlite LIKE folds regистр только для ASCII — кириллицу нужно
            # сравнивать вручную, чтобы поиск не зависел от регистра ввода.
            query_lower = query.lower()
            matching_ids = [
                obj.pk for obj in qs.only('pk', 'name', 'article')
                if query_lower in obj.name.lower()
                or query_lower in obj.article.lower()
            ]
            return qs.filter(pk__in=matching_ids).order_by('name')

        from django.contrib.postgres.search import (
            SearchQuery,
            SearchRank,
            SearchVector,
            TrigramSimilarity,
        )

        vector = SearchVector('name', 'article', config='russian')
        search_query = SearchQuery(query, config='russian')

        return (
            qs.annotate(
                rank=SearchRank(vector, search_query),
                similarity=TrigramSimilarity('name', query),
            )
            .filter(Q(rank__gt=0) | Q(similarity__gt=0.2))
            .order_by('-rank', '-similarity')
        )


class NomenclatureDetailView(DetailView):
    model = Nomenclature
    template_name = 'catalog/nomenclature_detail.html'
    context_object_name = 'nomenclature'
    slug_field = 'code_1c'
    slug_url_kwarg = 'code_1c'

    def get_queryset(self):
        # ADR-009: is_active=False не выпадает из выборки — карточка
        # доступна по прямой ссылке, только скрыта из списка/поиска.
        visible_balances = StockBalance.objects.filter(
            warehouse__is_visible=True
        ).select_related('warehouse')
        return Nomenclature.objects.select_related('kind', 'folder').prefetch_related(
            Prefetch('stock_balances', queryset=visible_balances)
        )


class RequestListView(LoginRequiredMixin, ListView):
    # ADR-012: все конструкторы видят все заявки (защита от параллельных
    # дублей между людьми), автор не скрыт.
    model = Request
    template_name = 'catalog/request_list.html'
    context_object_name = 'request_list'
    paginate_by = 50

    def get_queryset(self):
        qs = Request.objects.select_related('author').order_by('-created_at')

        status = self.request.GET.get('status')
        if status in Request.Status.values:
            qs = qs.filter(status=status)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['statuses'] = Request.Status.choices
        context['current_status'] = self.request.GET.get('status', '')
        querystring = self.request.GET.copy()
        querystring.pop('page', None)
        context['querystring'] = querystring.urlencode()
        return context


class RequestCreateView(LoginRequiredMixin, CreateView):
    form_class = RequestForm
    template_name = 'catalog/request_form.html'
    success_url = reverse_lazy('catalog:request_list')

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)


@login_required
def similar_nomenclature(request):
    """
    Живой поиск похожих позиций для антидубль-шага формы заявки (ADR-007).

    На Postgres — триграммное сходство (`pg_trgm`), на sqlite — фолбэк на
    `difflib` (тот же дух: без точного текстового совпадения, только по
    похожести написания), см. NomenclatureListView._search про причины
    фолбэка для локальной разработки без Postgres.
    """
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})

    active = Nomenclature.objects.filter(is_active=True)

    if connection.vendor == 'postgresql':
        from django.contrib.postgres.search import TrigramSimilarity

        matches = list(
            active.annotate(similarity=TrigramSimilarity('name', query))
            .filter(similarity__gt=0.2)
            .order_by('-similarity')[:10]
        )
    else:
        query_lower = query.lower()
        scored = [
            (difflib.SequenceMatcher(None, query_lower, obj.name.lower()).ratio(), obj)
            for obj in active.only('id', 'name', 'article')
        ]
        scored = [(score, obj) for score, obj in scored if score > 0.4]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        matches = [obj for _, obj in scored[:10]]

    return JsonResponse({
        'results': [
            {'id': obj.id, 'name': obj.name, 'article': obj.article}
            for obj in matches
        ]
    })
