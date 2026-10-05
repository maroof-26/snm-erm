from django.urls import path

from . import views as v

urlpatterns = [
    path('', v.dashboard, name='dashboard'),

    path('parties/', v.PartyList.as_view(), name='party_list'),
    path('parties/new/', v.PartyCreate.as_view(), name='party_new'),
    path('parties/<int:pk>/edit/', v.PartyUpdate.as_view(), name='party_edit'),
    path('parties/<int:pk>/delete/', v.PartyDelete.as_view(), name='party_delete'),

    path('products/', v.ProductList.as_view(), name='product_list'),
    path('products/new/', v.ProductCreate.as_view(), name='product_new'),
    path('products/<int:pk>/edit/', v.ProductUpdate.as_view(), name='product_edit'),
    path('products/<int:pk>/delete/', v.ProductDelete.as_view(), name='product_delete'),

    path('payments/', v.PaymentList.as_view(), name='payment_list'),
    path('payments/new/', v.PaymentCreate.as_view(), name='payment_new'),
    path('payments/<int:pk>/edit/', v.PaymentUpdate.as_view(), name='payment_edit'),
    path('payments/<int:pk>/delete/', v.PaymentDelete.as_view(), name='payment_delete'),

    path('stock/', v.stock_overview, name='stock_overview'),
    path('stock/moves/', v.stock_moves, name='stock_moves'),
    path('stock/adjust/', v.stock_adjust, name='stock_adjust'),
    path('stock/transfer/', v.stock_transfer, name='stock_transfer'),
    path('stock/moves/<int:pk>/delete/', v.stock_move_delete, name='stock_move_delete'),
    path('stock/warehouses/', v.WarehouseList.as_view(), name='warehouse_list'),
    path('stock/warehouses/new/', v.WarehouseCreate.as_view(), name='warehouse_new'),
    path('stock/warehouses/<int:pk>/edit/', v.WarehouseUpdate.as_view(), name='warehouse_edit'),
    path('stock/warehouses/<int:pk>/delete/', v.WarehouseDelete.as_view(), name='warehouse_delete'),
    path('stock/<int:pk>/', v.stock_product, name='stock_product'),

    path('ledger/', v.ledger_index, name='ledger'),
    path('ledger/entry/new/', v.ledger_entry_new, name='ledger_entry_new'),
    path('ledger/entry/<int:pk>/delete/', v.ledger_entry_delete, name='ledger_entry_delete'),
    path('ledger/<int:pk>/', v.ledger_party, name='ledger_party'),

    path('reports/', v.report_index, name='report_index'),
    path('reports/<slug:slug>/', v.report_view, name='report_view'),
]

# sale_* and purchase_* routes share the same views
for kind, prefix in (('sale', 'sales'), ('purchase', 'purchases')):
    urlpatterns += [
        path(f'{prefix}/', v.invoice_list, {'kind': kind}, name=f'{kind}_list'),
        path(f'{prefix}/new/', v.invoice_form, {'kind': kind}, name=f'{kind}_new'),
        path(f'{prefix}/<int:pk>/', v.invoice_detail, {'kind': kind}, name=f'{kind}_detail'),
        path(f'{prefix}/<int:pk>/edit/', v.invoice_form, {'kind': kind}, name=f'{kind}_edit'),
        path(f'{prefix}/<int:pk>/delete/', v.invoice_delete, {'kind': kind}, name=f'{kind}_delete'),
        path(f'{prefix}/<int:pk>/pdf/', v.invoice_pdf, {'kind': kind}, name=f'{kind}_pdf'),
    ]
