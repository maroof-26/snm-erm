"""Quotations, sales orders and purchase orders."""
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from tenants.access import check_access

from . import orders, pdf, stock
from .daterange import get_range, range_context
from .forms import OrderForm, OrderItemFormSet
from .models import Invoice, Order, Party
from .views import file_response, paginate

ORDER_FEATURE = {Order.QUOTATION: 'quotations', Order.SALES_ORDER: 'sales_orders', Order.PURCHASE_ORDER: 'purchase_orders'}
ORDER_PLURAL = {Order.QUOTATION: 'Quotations', Order.SALES_ORDER: 'Sales orders', Order.PURCHASE_ORDER: 'Purchase orders'}
INVOICE_URL = {Order.QUOTATION: 'sale_new', Order.SALES_ORDER: 'sale_new', Order.PURCHASE_ORDER: 'purchase_new'}
INVOICE_FEATURE = {Order.QUOTATION: 'sales', Order.SALES_ORDER: 'sales', Order.PURCHASE_ORDER: 'purchases'}
STATUS_FILTERS = {
    Order.QUOTATION: [('draft', 'Draft'), ('sent', 'Sent'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('converted', 'Converted'), ('expired', 'Expired')],
    Order.SALES_ORDER: [('open', 'Open'), ('partial', 'Partial'), ('completed', 'Completed'), ('cancelled', 'Cancelled')],
    Order.PURCHASE_ORDER: [('open', 'Open'), ('partial', 'Partial'), ('completed', 'Completed'), ('cancelled', 'Cancelled')],
}
QUOTE_STATUS_CHANGES = {Order.DRAFT, Order.SENT, Order.ACCEPTED, Order.REJECTED}


def _qs(request, kind):
    return Order.objects.filter(tenant=request.tenant, kind=kind)


def _get(request, kind, pk):
    return get_object_or_404(_qs(request, kind).select_related('party', 'tenant', 'source'), pk=pk)


def order_list(request, kind):
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    rng = get_range(request, default='all')
    qs = rng.apply(_qs(request, kind)).select_related('party', 'tenant').prefetch_related('items')
    q, status, party = request.GET.get('q'), request.GET.get('status'), request.GET.get('party')
    if q:
        qs = qs.filter(Q(party__name__icontains=q) | Q(notes__icontains=q) | Q(number__icontains=q.lstrip('#').split('-')[-1]))
    if party:
        qs = qs.filter(party_id=party)
    rows = list(qs)
    if status:
        rows = [o for o in rows if o.display_status.lower() == status]
    total = sum((o.total for o in rows), Decimal('0'))
    page, keep = paginate(request, rows)
    kinds = [Party.VENDOR if kind == Order.PURCHASE_ORDER else Party.CUSTOMER, Party.BOTH]
    return render(request, 'erp/order_list.html', {
        'kind': kind, 'title': ORDER_PLURAL[kind], 'page': page, 'qs': keep, 'total': total, 'count': len(rows),
        'status_options': STATUS_FILTERS[kind], 'parties': Party.objects.filter(tenant=request.tenant, kind__in=kinds),
        'filters_active': bool(party) + bool(status), 'party_label': 'Vendor' if kind == Order.PURCHASE_ORDER else 'Customer', **range_context(request, rng)})


def order_detail(request, kind, pk):
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    order = _get(request, kind, pk)
    feats = request.tenant.enabled_features()
    items = list(order.items.select_related('product', 'unit'))
    track = kind == Order.SALES_ORDER and 'stock' in feats
    for it in items:
        it.available = stock.on_hand(it.product) if track and it.product.track_stock else None
        it.short = it.available is not None and it.available < it.remaining
    cur = request.tenant.currency_symbol
    wa_text = (f'Assalam o Alaikum {order.party.name}, {order.get_kind_display().lower()} {order.code} from {request.tenant.name}: '
               f'Total {cur} {order.total:,.2f}.' + (f' Valid until {order.due_date:%d %b %Y}.' if order.due_date and order.is_quote else '') + ' Thank you.')
    can_invoice = (INVOICE_FEATURE[kind] in feats and order.status not in (Order.CANCELLED, Order.REJECTED, Order.CONVERTED)
                   and any(i.remaining > 0 for i in items))
    return render(request, 'erp/order_detail.html', {
        'kind': kind, 'order': order, 'items': items, 'wa_text': wa_text, 'can_invoice': can_invoice, 'track': track,
        'can_make_order': order.is_quote and 'sales_orders' in feats and order.status not in (Order.REJECTED, Order.CONVERTED),
        'invoice_url': INVOICE_URL[kind], 'invoices': order.invoices.select_related('tenant').all(), 'children': order.children.select_related('tenant').all(),
        'quote_statuses': [(s, l) for s, l in Order.STATUSES if s in QUOTE_STATUS_CHANGES and s != order.status]})


def order_form(request, kind, pk=None):
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    tenant = request.tenant
    order = _get(request, kind, pk) if pk else Order(tenant=tenant, kind=kind, created_by=request.user)
    form = OrderForm(request.POST or None, instance=order, tenant=tenant)
    formset = OrderItemFormSet(request.POST or None, instance=order, form_kwargs={'tenant': tenant})
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save()
            formset.instance = order
            formset.save()
            order.recalculate()
            orders.sync_progress(order)
        messages.success(request, f'{order.code} saved.')
        return redirect(f'{kind}_detail', pk=order.pk)
    from .models import Product
    products = {p.pk: {'rate': float(p.price), 'unit': p.unit_id} for p in Product.objects.filter(tenant=tenant)}
    import json
    return render(request, 'erp/order_form.html', {
        'kind': kind, 'order': order, 'form': form, 'formset': formset, 'products_json': json.dumps(products),
        'title': ('Edit ' if pk else 'New ') + order.get_kind_display().lower()})


def order_delete(request, kind, pk):
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    order = _get(request, kind, pk)
    if request.method == 'POST':
        order.delete()
        messages.success(request, 'Deleted.')
        return redirect(f'{kind}_list')
    return render(request, 'erp/confirm_delete.html', {'object': order, 'cancel_url': f'{kind}_detail', 'cancel_pk': pk})


def order_pdf(request, kind, pk):
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    order = _get(request, kind, pk)
    return file_response(pdf.order_pdf(order), f'{order.code}.pdf', 'application/pdf')


def order_status(request, kind, pk):
    """POST: change a quotation's status, or cancel / re-open an order."""
    denied = check_access(request, ORDER_FEATURE[kind])
    if denied:
        return denied
    order = _get(request, kind, pk)
    new = request.POST.get('status')
    allowed = QUOTE_STATUS_CHANGES if order.is_quote else {Order.CANCELLED, Order.OPEN}
    if request.method == 'POST' and new in allowed and order.status != Order.CONVERTED:
        order.status = new
        order.save(update_fields=['status'])
        messages.success(request, f'{order.code} marked {order.get_status_display().lower()}.')
    return redirect(f'{kind}_detail', pk=pk)


def order_convert(request, kind, pk):
    """POST: turn an accepted quotation into a sales order."""
    denied = check_access(request, 'sales_orders')
    if denied:
        return denied
    quote = _get(request, Order.QUOTATION, pk) if kind == Order.QUOTATION else None
    if quote is None or request.method != 'POST' or quote.status in (Order.REJECTED, Order.CONVERTED):
        raise Http404
    so = orders.convert_quote_to_sales_order(quote, request.user)
    messages.success(request, f'{so.code} created from {quote.code}.')
    return redirect('sales_order_detail', pk=so.pk)
