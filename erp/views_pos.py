"""Point of sale: shifts, the sale screen, checkout and receipts."""
import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from tenants.access import tenant_view

from . import pos, stock
from .models import Invoice, Party, Payment, PosSession, Product


def _money(v):
    try:
        return pos._dec(v, pos.CENT, 'Amount')
    except pos.CheckoutError:
        return None


@tenant_view('pos')
def pos_open(request):
    """Start a shift (opening cash), or go straight to the till if one is already open."""
    if pos.open_session(request.tenant, request.user):
        return redirect('pos_home')
    if request.method == 'POST':
        cash = _money(request.POST.get('opening_cash') or '0')
        if cash is None or cash < 0:
            messages.error(request, 'Enter the cash in the drawer as a number.')
        else:
            PosSession.objects.create(tenant=request.tenant, user=request.user, opening_cash=cash)
            return redirect('pos_home')
    last = PosSession.objects.filter(tenant=request.tenant, closed_at__isnull=False).first()
    return render(request, 'erp/pos_open.html', {'last': last})


@tenant_view('pos')
@ensure_csrf_cookie
def pos_home(request):
    tenant = request.tenant
    session = pos.open_session(tenant, request.user)
    if session is None:
        return redirect('pos_open')
    levels = {}
    if 'stock' in tenant.enabled_features():
        levels = {p.pk: float(p.on_hand) for p in stock.stock_summary(tenant)}
    products = [{'id': p.pk, 'n': p.name, 's': p.sku, 'b': p.barcode, 'p': float(p.price), 'u': p.unit.symbol, 'q': levels.get(p.pk)}
                for p in Product.objects.filter(tenant=tenant, is_active=True).select_related('unit').order_by('name')]
    customers = [{'id': c.pk, 'n': c.name, 'ph': c.phone}
                 for c in Party.objects.filter(tenant=tenant, is_active=True, kind__in=[Party.CUSTOMER, Party.BOTH]).exclude(name=pos.WALK_IN).order_by('name')]
    return render(request, 'erp/pos.html', {
        'session': session, 'products': products, 'customers': customers,
        'methods': Payment.METHODS, 'currency': tenant.currency_symbol, 'allow_negative': tenant.allow_negative_stock})


@tenant_view('pos')
@require_POST
def pos_checkout(request):
    session = pos.open_session(request.tenant, request.user)
    if session is None:
        return JsonResponse({'ok': False, 'error': 'Your shift is closed. Open a shift to keep selling.'}, status=400)
    try:
        payload = json.loads(request.body or '{}')
        invoice, change = pos.checkout(request.tenant, request.user, session, payload)
    except (pos.CheckoutError, ValueError) as e:
        return JsonResponse({'ok': False, 'error': str(e) if isinstance(e, pos.CheckoutError) else 'Bad request.'}, status=400)
    from django.urls import reverse
    return JsonResponse({'ok': True, 'id': invoice.pk, 'code': invoice.code, 'total': str(invoice.total), 'change': str(change),
                         'receipt': reverse('pos_receipt', args=[invoice.pk])})


@tenant_view('pos')
def pos_receipt(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('party', 'tenant', 'created_by', 'pos_session'), pk=pk, tenant=request.tenant, kind=Invoice.SALE)
    payments = list(invoice.payments.all())
    return render(request, 'erp/pos_receipt.html', {
        'invoice': invoice, 'items': invoice.items.select_related('product', 'unit'), 'payments': payments, 'subtotal': invoice.total + invoice.discount,
        'paid': sum((p.amount for p in payments), 0), 'change': request.GET.get('change'), 'print': request.GET.get('print') == '1'})


@tenant_view('pos')
def pos_shift(request):
    """Current shift summary with the close form, plus recent shifts."""
    current = pos.open_session(request.tenant, request.user)
    if current and request.method == 'POST':
        counted = _money(request.POST.get('closing_cash'))
        if counted is None or counted < 0:
            messages.error(request, 'Enter the cash you counted as a number.')
        else:
            current.closing_cash, current.closed_at, current.note = counted, timezone.now(), request.POST.get('note', '')[:200]
            current.save()
            messages.success(request, 'Shift closed.')
            return redirect('pos_shift_detail', pk=current.pk)
    recent = list(PosSession.objects.filter(tenant=request.tenant).select_related('user')[:15])
    for s in recent:
        s.sum = s.summary()
    return render(request, 'erp/pos_shift.html', {'current': current, 'cur_sum': current.summary() if current else None, 'recent': recent})


@tenant_view('pos')
def pos_shift_detail(request, pk):
    session = get_object_or_404(PosSession.objects.select_related('user'), pk=pk, tenant=request.tenant)
    return render(request, 'erp/pos_shift_detail.html', {'s': session, 'sum': session.summary(), 'sales': session.sales.select_related('party', 'tenant').order_by('number')})
