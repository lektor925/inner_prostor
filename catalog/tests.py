from django.test import TestCase
from django.urls import reverse

from catalog.models import Folder, Nomenclature, NomenclatureKind, StockBalance, Warehouse
from catalog.views import NomenclatureListView, folders_by_hierarchy


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
        self.assertContains(response, 'не актуально')


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
