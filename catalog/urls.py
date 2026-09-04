from django.urls import path

from . import views

app_name = 'catalog'

urlpatterns = [
    path('', views.NomenclatureListView.as_view(), name='nomenclature_list'),
    path(
        'item/<str:code_1c>/',
        views.NomenclatureDetailView.as_view(),
        name='nomenclature_detail',
    ),
]
