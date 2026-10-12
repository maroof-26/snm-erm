from django.urls import path

from . import views as v
from . import views_orders as vo
from . import views_pos as vp

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

    path('pos/', vp.pos_home, name='pos_home'),
    path('pos/open/', vp.pos_open, name='pos_open'),
    path('pos/checkout/', vp.pos_checkout, name='pos_checkout'),
    path('pos/receipt/<int:pk>/', vp.pos_receipt, name='pos_receipt'),
    path('pos/shift/', vp.pos_shift, name='pos_shift'),
    path('pos/shift/<int:pk>/', vp.pos_shift_detail, name='pos_shift_detail'),

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

# quotations / sales orders / purchase orders
for kind, prefix in (('quotation', 'quotations'), ('sales_order', 'sales-orders'), ('purchase_order', 'purchase-orders')):
    urlpatterns += [
        path(f'{prefix}/', vo.order_list, {'kind': kind}, name=f'{kind}_list'),
        path(f'{prefix}/new/', vo.order_form, {'kind': kind}, name=f'{kind}_new'),
        path(f'{prefix}/<int:pk>/', vo.order_detail, {'kind': kind}, name=f'{kind}_detail'),
        path(f'{prefix}/<int:pk>/edit/', vo.order_form, {'kind': kind}, name=f'{kind}_edit'),
        path(f'{prefix}/<int:pk>/delete/', vo.order_delete, {'kind': kind}, name=f'{kind}_delete'),
        path(f'{prefix}/<int:pk>/pdf/', vo.order_pdf, {'kind': kind}, name=f'{kind}_pdf'),
        path(f'{prefix}/<int:pk>/status/', vo.order_status, {'kind': kind}, name=f'{kind}_status'),
        path(f'{prefix}/<int:pk>/convert/', vo.order_convert, {'kind': kind}, name=f'{kind}_convert'),
    ]
