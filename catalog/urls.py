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
    path('requests/', views.RequestListView.as_view(), name='request_list'),
    path('requests/new/', views.RequestCreateView.as_view(), name='request_create'),
    path(
        'requests/similar/',
        views.similar_nomenclature,
        name='similar_nomenclature',
    ),
]
