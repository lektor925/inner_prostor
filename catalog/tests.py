import unittest

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Folder,
    ImportLog,
    Nomenclature,
    NomenclatureKind,
    Request,
    StockBalance,
    Warehouse,
)
from catalog.forms import RequestForm
from catalog.views import NomenclatureListView, folders_by_hierarchy

User = get_user_model()


class HomeViewTests(TestCase):
    def test_renders_hero_at_site_root(self):
        response = self.client.get(reverse('catalog:home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'catalog/home.html')
        self.assertContains(response, 'Единый поиск по всей номенклатуре предприятия')

    def test_nomenclature_list_moved_to_catalog_path(self):
        self.assertEqual(reverse('catalog:home'), '/')
        self.assertEqual(reverse('catalog:nomenclature_list'), '/catalog/')

    def test_search_form_points_to_nomenclature_list(self):
        response = self.client.get(reverse('catalog:home'))

        self.assertContains(
            response, f'action="{reverse("catalog:nomenclature_list")}"'
        )

    def test_total_count_counts_only_active(self):
        Nomenclature.objects.create(code_1c='1', name='Активная', is_active=True)
        Nomenclature.objects.create(code_1c='2', name='Снятая', is_active=False)

        response = self.client.get(reverse('catalog:home'))

        self.assertEqual(response.context['total_count'], 1)

    def test_hero_shows_illustration(self):
        response = self.client.get(reverse('catalog:home'))

        self.assertContains(response, 'catalog/man.jpg')
        self.assertContains(response, 'hm-hero__figure')

    def test_renders_on_empty_catalog(self):
        response = self.client.get(reverse('catalog:home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_count'], 0)

    def test_last_import_uses_latest_successful_reference_run(self):
        old = ImportLog.objects.create(
            kind=ImportLog.Kind.REFERENCE, source_file='old.json',
            status=ImportLog.Status.OK,
        )
        newer = ImportLog.objects.create(
            kind=ImportLog.Kind.REFERENCE, source_file='new.json',
            status=ImportLog.Status.PARTIAL,
        )
        ImportLog.objects.create(
            kind=ImportLog.Kind.REFERENCE, source_file='broken.json',
            status=ImportLog.Status.ERROR,
        )

        response = self.client.get(reverse('catalog:home'))

        self.assertEqual(response.context['last_import'], newer)
        self.assertNotEqual(response.context['last_import'], old)

    def test_sync_line_falls_back_to_started_at_when_not_finished(self):
        ImportLog.objects.create(
            kind=ImportLog.Kind.REFERENCE, source_file='running.json',
            status=ImportLog.Status.OK, finished_at=None,
        )

        response = self.client.get(reverse('catalog:home'))

        self.assertContains(response, 'Синхронизация с 1С —')
        self.assertNotContains(response, 'ещё не выполнялась')


class ChromeTests(TestCase):
    """Общая обвязка шаблонов: верхнее меню и хлебные крошки."""

    def test_top_nav_shows_all_sections_always(self):
        response = self.client.get(reverse('catalog:home'))

        for url_name in [
            'catalog:home', 'catalog:nomenclature_list',
            'catalog:request_list', 'catalog:request_create',
        ]:
            self.assertContains(response, f'href="{reverse(url_name)}"')

    def test_top_nav_visible_to_anonymous(self):
        response = self.client.get(reverse('catalog:nomenclature_list'))

        self.assertContains(response, reverse('catalog:request_create'))
        self.assertContains(response, 'Войти')

    def test_breadcrumbs_on_list_page(self):
        response = self.client.get(reverse('catalog:nomenclature_list'))

        self.assertContains(response, 'prostor-breadcrumbs')
        self.assertContains(response, f'href="{reverse("catalog:home")}"')

    def test_breadcrumbs_on_detail_page(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_detail', args=['1'])
        )

        self.assertContains(response, 'prostor-breadcrumbs')
        self.assertContains(response, f'href="{reverse("catalog:nomenclature_list")}"')

    def test_prostor_stylesheet_linked(self):
        response = self.client.get(reverse('catalog:home'))

        self.assertContains(response, 'catalog/prostor.css')

    def test_footer_present_on_every_page(self):
        for url in [
            reverse('catalog:home'),
            reverse('catalog:nomenclature_list'),
            reverse('login'),
        ]:
            response = self.client.get(url)
            self.assertContains(response, 'prostor-footer')
            self.assertContains(response, 'ПРОСТОР-Л')


class NomenclatureListViewTests(TestCase):
    def test_lists_active_nomenclature(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10', is_active=True)

        response = self.client.get(reverse('catalog:nomenclature_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Болт М10')

    def test_ignores_malformed_kind_param(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'kind': 'abc'}
        )

        self.assertEqual(response.status_code, 200)

    def test_ignores_malformed_folder_param(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'folder': 'abc'}
        )

        self.assertEqual(response.status_code, 200)

    def test_hides_inactive_nomenclature(self):
        Nomenclature.objects.create(
            code_1c='2', name='Снятая позиция', is_active=False,
        )

        response = self.client.get(reverse('catalog:nomenclature_list'))

        self.assertNotContains(response, 'Снятая позиция')

    def test_filters_by_kind(self):
        krepezh = NomenclatureKind.objects.create(code_1c='k1', name='Крепёж')
        metrazh = NomenclatureKind.objects.create(code_1c='k2', name='Метраж')
        Nomenclature.objects.create(code_1c='1', name='Болт М10', kind=krepezh)
        Nomenclature.objects.create(code_1c='2', name='Кабель ВВГ', kind=metrazh)

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'kind': krepezh.id}
        )

        self.assertContains(response, 'Болт М10')
        self.assertNotContains(response, 'Кабель ВВГ')

    def test_filters_by_stale(self):
        Nomenclature.objects.create(code_1c='1', name='Залежалый лист', is_stale=True)
        Nomenclature.objects.create(code_1c='2', name='Свежий лист', is_stale=False)

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'stale': '1'}
        )

        self.assertContains(response, 'Залежалый лист')
        self.assertNotContains(response, 'Свежий лист')

    def test_filters_by_in_stock(self):
        Nomenclature.objects.create(code_1c='1', name='В наличии', total_available=5)
        Nomenclature.objects.create(code_1c='2', name='Нет в наличии', total_available=0)

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'in_stock': '1'}
        )

        self.assertContains(response, 'В наличии')
        self.assertNotContains(response, 'Нет в наличии')

    def test_search_matches_name_substring(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')
        Nomenclature.objects.create(code_1c='2', name='Кабель ВВГ')

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'q': 'болт'}
        )

        self.assertContains(response, 'Болт М10')
        self.assertNotContains(response, 'Кабель ВВГ')

    def test_search_matches_article(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10', article='BM10-Z')
        Nomenclature.objects.create(code_1c='2', name='Кабель ВВГ', article='VVG-3x2.5')

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'q': 'BM10'}
        )

        self.assertContains(response, 'Болт М10')
        self.assertNotContains(response, 'Кабель ВВГ')

    def test_search_combined_with_kind_facet(self):
        krepezh = NomenclatureKind.objects.create(code_1c='k1', name='Крепёж')
        metrazh = NomenclatureKind.objects.create(code_1c='k2', name='Метраж')
        Nomenclature.objects.create(code_1c='1', name='Болт М10', kind=krepezh)
        Nomenclature.objects.create(code_1c='2', name='Болт фундаментный', kind=metrazh)

        response = self.client.get(
            reverse('catalog:nomenclature_list'),
            {'q': 'болт', 'kind': krepezh.id},
        )

        self.assertContains(response, 'Болт М10')
        self.assertNotContains(response, 'Болт фундаментный')

    def test_search_no_matches_shows_empty_message(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'q': 'несуществующий текст'}
        )

        self.assertContains(response, 'Ничего не найдено.')

    def test_paginates_results(self):
        for i in range(NomenclatureListView.paginate_by + 1):
            Nomenclature.objects.create(code_1c=str(i), name=f'Позиция {i:03d}')

        page1 = self.client.get(reverse('catalog:nomenclature_list'))
        page2 = self.client.get(reverse('catalog:nomenclature_list'), {'page': 2})

        self.assertEqual(
            len(page1.context['nomenclature_list']),
            NomenclatureListView.paginate_by,
        )
        self.assertEqual(len(page2.context['nomenclature_list']), 1)

    def test_filters_by_folder(self):
        krepezh_folder = Folder.objects.create(code_1c='f1', name='Крепёж')
        drugoe_folder = Folder.objects.create(code_1c='f2', name='Прочее')
        Nomenclature.objects.create(code_1c='1', name='Болт М10', folder=krepezh_folder)
        Nomenclature.objects.create(code_1c='2', name='Кабель ВВГ', folder=drugoe_folder)

        response = self.client.get(
            reverse('catalog:nomenclature_list'), {'folder': krepezh_folder.id}
        )

        self.assertContains(response, 'Болт М10')
        self.assertNotContains(response, 'Кабель ВВГ')


class NomenclatureDetailViewTests(TestCase):
    def test_shows_nomenclature_name(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_detail', args=['1'])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Болт М10')

    def test_shows_stock_breakdown_for_visible_warehouses_only(self):
        nom = Nomenclature.objects.create(code_1c='1', name='Болт М10')
        visible = Warehouse.objects.create(
            code_1c='w1', name='ПРОСТОР-Л МАТЕРИАЛЫ', is_visible=True,
        )
        hidden = Warehouse.objects.create(
            code_1c='w2', name='Салехард', is_visible=False,
        )
        StockBalance.objects.create(
            nomenclature=nom, warehouse=visible, qty=100, reserved=30,
        )
        StockBalance.objects.create(
            nomenclature=nom, warehouse=hidden, qty=5000, reserved=0,
        )

        response = self.client.get(
            reverse('catalog:nomenclature_detail', args=['1'])
        )

        self.assertContains(response, 'ПРОСТОР-Л МАТЕРИАЛЫ')
        self.assertNotContains(response, 'Салехард')

    def test_inactive_nomenclature_accessible_by_direct_link(self):
        Nomenclature.objects.create(
            code_1c='1', name='Снятая позиция', is_active=False,
        )

        response = self.client.get(
            reverse('catalog:nomenclature_detail', args=['1'])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не актуальна')


class FoldersByHierarchyTests(TestCase):
    def test_orders_children_under_their_parent_with_depth(self):
        root = Folder.objects.create(code_1c='f1', name='Сырьё')
        child_b = Folder.objects.create(code_1c='f3', name='Метраж', parent=root)
        child_a = Folder.objects.create(code_1c='f2', name='Крепёж', parent=root)
        other_root = Folder.objects.create(code_1c='f4', name='Готовая продукция')

        result = folders_by_hierarchy()

        self.assertEqual(
            [(f.id, depth) for f, depth in result],
            [
                (other_root.id, 0),
                (root.id, 0),
                (child_a.id, 1),
                (child_b.id, 1),
            ],
        )


class RequestListViewTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('catalog:request_list'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_shows_requests_from_all_authors(self):
        alice = User.objects.create_user('alice')
        bob = User.objects.create_user('bob')
        Request.objects.create(author=alice, description='Нужен уголок 50x50')
        Request.objects.create(author=bob, description='Нужна труба 20x20')

        self.client.force_login(alice)
        response = self.client.get(reverse('catalog:request_list'))

        self.assertContains(response, 'Нужен уголок 50x50')
        self.assertContains(response, 'Нужна труба 20x20')

    def test_filters_by_status(self):
        alice = User.objects.create_user('alice')
        Request.objects.create(
            author=alice, description='Новая заявка', status=Request.Status.NEW,
        )
        Request.objects.create(
            author=alice, description='Закрытая заявка', status=Request.Status.CLOSED,
        )

        self.client.force_login(alice)
        response = self.client.get(
            reverse('catalog:request_list'), {'status': Request.Status.NEW}
        )

        self.assertContains(response, 'Новая заявка')
        self.assertNotContains(response, 'Закрытая заявка')

    def test_ignores_invalid_status_param(self):
        alice = User.objects.create_user('alice')
        Request.objects.create(author=alice, description='Заявка без статуса')

        self.client.force_login(alice)
        response = self.client.get(
            reverse('catalog:request_list'), {'status': 'not-a-real-status'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Заявка без статуса')

    def test_paginates_results(self):
        alice = User.objects.create_user('alice')
        for i in range(51):
            Request.objects.create(author=alice, description=f'Заявка {i:03d}')

        self.client.force_login(alice)
        page1 = self.client.get(reverse('catalog:request_list'))
        page2 = self.client.get(reverse('catalog:request_list'), {'page': 2})

        self.assertEqual(len(page1.context['request_list']), 50)
        self.assertEqual(len(page2.context['request_list']), 1)
        self.assertContains(page1, 'Вперёд')


class RequestFormTests(TestCase):
    def test_valid_without_shown_candidates(self):
        form = RequestForm(data={'description': 'Нужен уголок 50x50'})

        self.assertTrue(form.is_valid(), form.errors)

    def test_accepts_structural_fields(self):
        kind = NomenclatureKind.objects.create(code_1c='k1', name='Крепёж')

        form = RequestForm(data={
            'description': 'Нужен уголок 50x50',
            'guessed_kind': kind.id,
            'unit': 'шт',
            'gost_or_article': 'ГОСТ 8509-93',
            'analog_url': 'https://example.com/item',
        })

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save(commit=False)
        self.assertEqual(saved.guessed_kind_id, kind.id)
        self.assertEqual(saved.unit, 'шт')
        self.assertEqual(saved.gost_or_article, 'ГОСТ 8509-93')
        self.assertEqual(saved.analog_url, 'https://example.com/item')

    def test_rejects_shown_candidate_not_marked(self):
        candidate = Nomenclature.objects.create(code_1c='1', name='Уголок 50x50')

        form = RequestForm(data={
            'description': 'Нужен уголок 50x50',
            'shown_candidate_ids': str(candidate.id),
        })

        self.assertFalse(form.is_valid())

    def test_accepts_when_shown_candidate_marked_not_matching(self):
        candidate = Nomenclature.objects.create(code_1c='1', name='Уголок 50x50')

        form = RequestForm(data={
            'description': 'Нужен уголок 50x50',
            'shown_candidate_ids': str(candidate.id),
            'reviewed_not_matching': [candidate.id],
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_accepts_when_shown_candidate_marked_as_similar_to(self):
        candidate = Nomenclature.objects.create(code_1c='1', name='Уголок 50x50')

        form = RequestForm(data={
            'description': 'Нужен уголок 50x50',
            'shown_candidate_ids': str(candidate.id),
            'similar_to': candidate.id,
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_accepts_candidate_marked_both_similar_to_and_reviewed(self):
        candidate = Nomenclature.objects.create(code_1c='1', name='Уголок 50x50')

        form = RequestForm(data={
            'description': 'Нужен уголок 50x50',
            'shown_candidate_ids': str(candidate.id),
            'similar_to': candidate.id,
            'reviewed_not_matching': [candidate.id],
        })

        self.assertTrue(form.is_valid(), form.errors)


class RequestCreateViewTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('catalog:request_create'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_authenticated_user_sees_form(self):
        alice = User.objects.create_user('alice')
        self.client.force_login(alice)

        response = self.client.get(reverse('catalog:request_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<form')

    def test_creates_request_with_current_user_as_author(self):
        alice = User.objects.create_user('alice')
        self.client.force_login(alice)

        response = self.client.post(
            reverse('catalog:request_create'),
            {'description': 'Нужен уголок 50x50'},
        )

        self.assertEqual(response.status_code, 302)
        created = Request.objects.get()
        self.assertEqual(created.author, alice)
        self.assertEqual(created.description, 'Нужен уголок 50x50')

    def test_ignores_spoofed_author_and_status_in_post(self):
        alice = User.objects.create_user('alice')
        mallory = User.objects.create_user('mallory')
        self.client.force_login(alice)

        self.client.post(
            reverse('catalog:request_create'),
            {
                'description': 'Нужен уголок 50x50',
                'author': mallory.id,
                'status': Request.Status.APPROVED,
            },
        )

        created = Request.objects.get()
        self.assertEqual(created.author, alice)
        self.assertEqual(created.status, Request.Status.NEW)


class SimilarNomenclatureViewTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('catalog:similar_nomenclature'), {'q': 'болт'})

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_returns_matching_names_as_json(self):
        alice = User.objects.create_user('alice')
        self.client.force_login(alice)
        Nomenclature.objects.create(code_1c='1', name='Болт М10')
        Nomenclature.objects.create(code_1c='2', name='Кабель ВВГ')

        response = self.client.get(
            reverse('catalog:similar_nomenclature'), {'q': 'болт'}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = [item['name'] for item in payload['results']]
        self.assertIn('Болт М10', names)
        self.assertNotIn('Кабель ВВГ', names)

    def test_empty_query_returns_no_results(self):
        alice = User.objects.create_user('alice')
        self.client.force_login(alice)
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(reverse('catalog:similar_nomenclature'), {'q': ''})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'results': []})


@unittest.skipUnless(
    connection.vendor == 'postgresql',
    'требует реальный Postgres (pg_trgm/FTS) — недоступен на sqlite',
)
class PostgresSearchTests(TestCase):
    """
    Тесты, проверяющие сам Postgres-путь поиска (ADR-006/ADR-007) — ту
    часть, которую sqlite-фолбэк не покрывает вообще. Раньше это было
    задокументированным пробелом; теперь, когда Postgres доступен в среде
    разработки, эти тесты реально гоняются и ловят регрессии в реальном
    FTS/pg_trgm поведении (см. коммит, где ts_rank давал ложный >0 при
    отсутствии совпадения — этот класс существует, чтобы больше не
    полагаться на ручную проверку такого рода).
    """

    def test_garbage_query_finds_nothing(self):
        Nomenclature.objects.create(code_1c='1', name='Болт М10')

        response = self.client.get(
            reverse('catalog:nomenclature_list'),
            {'q': 'совершенно случайный несуществующий набор слов'},
        )

        self.assertContains(response, 'Ничего не найдено.')

    def test_typo_matches_long_full_name(self):
        # Триграммное сходство короткого запроса с длинным полным именем
        # занижается обычной similarity() из-за разницы в длине строк —
        # нужен TrigramWordSimilarity, не TrigramSimilarity.
        Nomenclature.objects.create(
            code_1c='1', name='Болт М10 оцинкованный увеличенной прочности',
        )
        self.client.force_login(User.objects.create_user('alice'))

        response = self.client.get(
            reverse('catalog:similar_nomenclature'), {'q': 'болд м10'}
        )

        names = [item['name'] for item in response.json()['results']]
        self.assertIn('Болт М10 оцинкованный увеличенной прочности', names)
