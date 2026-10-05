import csv
import json
from urllib.parse import urlencode
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from tenants.access import check_access, tenant_view

from . import pdf, queries, stock
from .daterange import get_range, range_context
from .forms import (InvoiceForm, ItemFormSet, LedgerEntryForm, PartyForm, PaymentForm, ProductForm, StockAdjustForm, StockTransferForm,
                    WarehouseForm)
from .models import Invoice, LedgerEntry, Party, Payment, Product, StockMove, Warehouse
from .reports import REPORTS

KIND_FEATURE = {Invoice.SALE: 'sales', Invoice.PURCHASE: 'purchases'}
KIND_LABEL = {Invoice.SALE: 'Sales', Invoice.PURCHASE: 'Purchases'}


def paginate(request, items, per_page=25):
    page = Paginator(items, per_page).get_page(request.GET.get('page'))
    keep = request.GET.copy()
    keep.pop('page', None)
    return page, keep.urlencode()


def file_response(content, filename, content_type):
    resp = HttpResponse(content, content_type=content_type)
    resp['Content-Disposition'] = f'inline; filename="{filename}"'
    return resp


# ---------- dashboard ----------

@tenant_view('dashboard')
def dashboard(request):
    rng = get_range(request, default='month')
    data = queries.dashboard_data(request.tenant, rng)
    t = request.tenant
    setup = {'parties': Party.objects.filter(tenant=t).exists(), 'products': Product.objects.filter(tenant=t).exists(),
             'invoices': Invoice.objects.filter(tenant=t).exists()}
    low = stock.low_stock(t) if 'stock' in t.enabled_features() else []
    return render(request, 'erp/dashboard.html', {**data, 'setup': setup, 'low_stock_items': low[:5], 'low_stock_total': len(low), 'setup_done': all(setup.values()), **range_context(request, rng), 'chart_json': data['chart']})


# ---------- generic tenant CRUD ----------

class TenantViewMixin:
    feature = None
    list_url = None
    title = ''

    def dispatch(self, request, *args, **kwargs):
        denied = check_access(request, self.feature)
        return denied or super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(tenant=self.request.tenant)

    def get_success_url(self):
        from django.urls import reverse
        return reverse(self.list_url)


class TenantFormMixin(TenantViewMixin):
    template_name = 'erp/form.html'

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw['tenant'] = self.request.tenant
        return kw

    def get_context_data(self, **kw):
        kw.setdefault('title', self.title)
        return super().get_context_data(cancel_url=self.list_url, **kw)

    def form_valid(self, form):
        messages.success(self.request, 'Saved.')
        return super().form_valid(form)


class TenantDelete(TenantViewMixin, DeleteView):
    template_name = 'erp/confirm_delete.html'

    def get_context_data(self, **kw):
        return super().get_context_data(cancel_url=self.list_url, **kw)

    def form_valid(self, form):
        try:
            resp = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, 'Cannot delete: it is used by other records. Mark it inactive instead where possible.')
            return redirect(self.get_success_url())
        messages.success(self.request, 'Deleted.')
        return resp


# ---------- parties ----------

class PartyList(TenantViewMixin, ListView):
    feature, model, template_name = 'parties', Party, 'erp/party_list.html'

    def get(self, request, *args, **kwargs):
        self.balances = {p.pk: p.closing for p in queries.party_balances(request.tenant)}
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        q, kind = self.request.GET.get('q'), self.request.GET.get('kind')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
        if kind in (Party.CUSTOMER, Party.VENDOR):
            qs = qs.filter(kind__in=[kind, Party.BOTH])
        return qs

    def get_context_data(self, **kw):
        page, qs = paginate(self.request, self.object_list)
        for p in page:
            p.balance = self.balances.get(p.pk, Decimal('0'))
            p.wa_text = (f'Assalam o Alaikum {p.name}, your outstanding balance with {self.request.tenant.name} is '
                         f'{self.request.tenant.currency_symbol} {p.balance:,.2f}. Please arrange payment. Thank you.') if p.balance > 0 else ''
        kind = self.request.GET.get('kind')
        title = {'customer': 'Customers', 'vendor': 'Vendors'}.get(kind, 'Customers & Vendors')
        return super().get_context_data(page=page, qs=qs, title=title, kind=kind, **kw)


class PartyCreate(TenantFormMixin, CreateView):
    feature, model, form_class, list_url, title = 'parties', Party, PartyForm, 'party_list', 'New customer / vendor'

    def get_initial(self):
        kind = self.request.GET.get('kind')
        return {'kind': kind} if kind in (Party.CUSTOMER, Party.VENDOR) else {}

    def get_success_url(self):
        nxt = self.request.GET.get('next')
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={self.request.get_host()}):
            return nxt
        return super().get_success_url()

    def get_context_data(self, **kw):
        kind = self.request.GET.get('kind')
        title = {'customer': 'New customer', 'vendor': 'New vendor'}.get(kind, self.title)
        return super().get_context_data(**{**kw, 'title': title})


class PartyUpdate(TenantFormMixin, UpdateView):
    feature, model, form_class, list_url, title = 'parties', Party, PartyForm, 'party_list', 'Edit customer / vendor'


class PartyDelete(TenantDelete):
    feature, model, list_url = 'parties', Party, 'party_list'


# ---------- products ----------

class ProductList(TenantViewMixin, ListView):
    feature, model, template_name = 'products', Product, 'erp/product_list.html'

    def get_queryset(self):
        qs = super().get_queryset().select_related('unit')
        q = self.request.GET.get('q')
        return qs.filter(Q(name__icontains=q) | Q(sku__icontains=q)) if q else qs

    def get_context_data(self, **kw):
        page, qs = paginate(self.request, self.object_list)
        levels = {}
        if 'stock' in self.request.tenant.enabled_features():
            levels = {p.pk: p for p in stock.stock_summary(self.request.tenant)}
        for p in page:
            p.stock_row = levels.get(p.pk)
        return super().get_context_data(page=page, qs=qs, show_stock=bool(levels) or 'stock' in self.request.tenant.enabled_features(), **kw)


class ProductCreate(TenantFormMixin, CreateView):
    feature, model, form_class, list_url, title = 'products', Product, ProductForm, 'product_list', 'New product'


class ProductUpdate(TenantFormMixin, UpdateView):
    feature, model, form_class, list_url, title = 'products', Product, ProductForm, 'product_list', 'Edit product'


class ProductDelete(TenantDelete):
    feature, model, list_url = 'products', Product, 'product_list'


# ---------- payments ----------

class PaymentList(TenantViewMixin, ListView):
    feature, model, template_name = 'payments', Payment, 'erp/payment_list.html'

    def get_queryset(self):
        rng = self.rng = get_range(self.request, default='all')
        qs = rng.apply(super().get_queryset().select_related('party', 'invoice', 'invoice__tenant'))
        if self.request.GET.get('direction') in (Payment.IN, Payment.OUT):
            qs = qs.filter(direction=self.request.GET['direction'])
        if self.request.GET.get('party'):
            qs = qs.filter(party_id=self.request.GET['party'])
        return qs

    def get_context_data(self, **kw):
        page, qs = paginate(self.request, self.object_list)
        totals = {d: self.object_list.filter(direction=d).aggregate(s=Sum('amount'))['s'] or 0 for d in (Payment.IN, Payment.OUT)}
        parties = Party.objects.filter(tenant=self.request.tenant)
        return super().get_context_data(page=page, qs=qs, totals=totals, parties=parties,
                                        **range_context(self.request, self.rng), **kw)


class PaymentCreate(TenantFormMixin, CreateView):
    feature, model, form_class, list_url, title = 'payments', Payment, PaymentForm, 'payment_list', 'Record payment'

    def get_context_data(self, **kw):
        d = self.request.GET.get('direction')
        title = {'in': 'Receive money from customer', 'out': 'Pay money to vendor'}.get(d, self.title)
        return super().get_context_data(**{**kw, 'title': title})

    def get_initial(self):
        initial = {}
        if self.request.GET.get('direction') in (Payment.IN, Payment.OUT):
            initial['direction'] = self.request.GET['direction']
        inv = Invoice.objects.filter(pk=self.request.GET.get('invoice') or 0, tenant=self.request.tenant).first()
        if inv:
            initial.update(invoice=inv, party=inv.party, amount=max(inv.balance, 0),
                           direction=Payment.IN if inv.kind == Invoice.SALE else Payment.OUT)
        elif self.request.GET.get('party'):
            initial['party'] = self.request.GET['party']
        return initial


class PaymentUpdate(TenantFormMixin, UpdateView):
    feature, model, form_class, list_url, title = 'payments', Payment, PaymentForm, 'payment_list', 'Edit payment'


class PaymentDelete(TenantDelete):
    feature, model, list_url = 'payments', Payment, 'payment_list'


# ---------- invoices (sales & purchases) ----------

def _invoice_qs(request, kind):
    return Invoice.objects.filter(tenant=request.tenant, kind=kind)


def invoice_list(request, kind):
    denied = check_access(request, KIND_FEATURE[kind])
    if denied:
        return denied
    rng = get_range(request, default='all')
    qs = queries.with_paid(rng.apply(_invoice_qs(request, kind))).select_related('party', 'tenant').order_by('-date', '-number')
    q, status, party = request.GET.get('q'), request.GET.get('status'), request.GET.get('party')
    if q:
        qs = qs.filter(Q(party__name__icontains=q) | Q(notes__icontains=q) | Q(number__icontains=q.lstrip('#').split('-')[-1]))
    if party:
        qs = qs.filter(party_id=party)
    if status == 'unpaid':
        qs = qs.filter(due__gt=0)
    elif status == 'paid':
        qs = qs.filter(due__lte=0)
    totals = {'total': sum(i.total for i in qs), 'paid': sum(i.paid_total for i in qs)}
    totals['due'] = totals['total'] - totals['paid']
    page, keep = paginate(request, qs)
    kinds = [Party.CUSTOMER if kind == Invoice.SALE else Party.VENDOR, Party.BOTH]
    clear_qs = urlencode({k: request.GET[k] for k in ('range', 'from', 'to') if request.GET.get(k)})
    return render(request, 'erp/invoice_list.html', {
        'filters_active': bool(party) + bool(status), 'clear_qs': clear_qs,
        'kind': kind, 'title': KIND_LABEL[kind], 'page': page, 'qs': keep, 'totals': totals,
        'parties': Party.objects.filter(tenant=request.tenant, kind__in=kinds), **range_context(request, rng)})


def invoice_detail(request, kind, pk):
    denied = check_access(request, KIND_FEATURE[kind])
    if denied:
        return denied
    invoice = get_object_or_404(_invoice_qs(request, kind).select_related('party', 'tenant'), pk=pk)
    cur = request.tenant.currency_symbol
    wa_text = (f'Assalam o Alaikum {invoice.party.name}, {request.tenant.name} - {invoice.code}: '
               f'Total {cur} {invoice.total:,.2f}, Paid {cur} {invoice.paid:,.2f}, Balance {cur} {invoice.balance:,.2f}. Thank you.')
    return render(request, 'erp/invoice_detail.html', {
        'kind': kind, 'invoice': invoice, 'items': invoice.items.select_related('product', 'unit'),
        'payments': invoice.payments.all(), 'wa_text': wa_text})


def invoice_pdf(request, kind, pk):
    denied = check_access(request, KIND_FEATURE[kind])
    if denied:
        return denied
    invoice = get_object_or_404(_invoice_qs(request, kind).select_related('party', 'tenant'), pk=pk)
    return file_response(pdf.invoice_pdf(invoice), f'{invoice.code}.pdf', 'application/pdf')


def invoice_form(request, kind, pk=None):
    denied = check_access(request, KIND_FEATURE[kind])
    if denied:
        return denied
    tenant = request.tenant
    if pk:
        invoice = get_object_or_404(_invoice_qs(request, kind).select_related('tenant'), pk=pk)
    else:
        invoice = Invoice(tenant=tenant, kind=kind, created_by=request.user)
    form = InvoiceForm(request.POST or None, instance=invoice, tenant=tenant)
    formset = ItemFormSet(request.POST or None, instance=invoice, form_kwargs={'tenant': tenant})
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        total = sum((f.cleaned_data['quantity'] * f.cleaned_data['rate'] for f in formset.forms
                     if f.cleaned_data and not f.cleaned_data.get('DELETE')), Decimal('0'))
        paid = form.cleaned_data.get('paid_now') or 0
        short = []
        if kind == Invoice.SALE and 'stock' in tenant.enabled_features():
            needs = {}
            for f in formset.forms:
                cd = f.cleaned_data
                if cd and not cd.get('DELETE') and cd['product'].track_stock:
                    needs[cd['product']] = needs.get(cd['product'], Decimal('0')) + cd['quantity']
            wh = form.cleaned_data.get('warehouse') or invoice.warehouse or stock.default_warehouse(tenant)
            short = stock.shortages(tenant, wh, needs, exclude_invoice=invoice)
            if short and not tenant.allow_negative_stock:
                form.add_error(None, 'Not enough stock: ' + '; '.join(
                    f'{p.name} (have {have.normalize():f} {p.unit.symbol}, need {need.normalize():f})' for p, have, need in short))
        if paid > total:
            form.add_error('paid_now', 'Cannot exceed the invoice total.')
        if not form.errors:
            with transaction.atomic():
                invoice = form.save()
                formset.instance = invoice
                formset.save()
                invoice.recalculate()
                if paid:
                    Payment.objects.create(
                        tenant=tenant, party=invoice.party, invoice=invoice, amount=paid, date=invoice.date,
                        direction=Payment.IN if kind == Invoice.SALE else Payment.OUT,
                        method=form.cleaned_data.get('method') or 'cash')
            messages.success(request, f'{invoice.code} saved.')
            if short:
                messages.warning(request, 'Stock is now below zero for: ' + ', '.join(p.name for p, _, _ in short) + '. Record a purchase or add stock.')
            return redirect(f'{kind}_detail', pk=invoice.pk)
    products = {p.pk: {'rate': float(p.price), 'unit': p.unit_id} for p in Product.objects.filter(tenant=tenant)}
    return render(request, 'erp/invoice_form.html', {
        'kind': kind, 'invoice': invoice, 'form': form, 'formset': formset,
        'products_json': json.dumps(products), 'title': ('Edit ' if pk else 'New ') + KIND_LABEL[kind].lower()[:-1]})


def invoice_delete(request, kind, pk):
    denied = check_access(request, KIND_FEATURE[kind])
    if denied:
        return denied
    invoice = get_object_or_404(_invoice_qs(request, kind).select_related('tenant'), pk=pk)
    if request.method == 'POST':
        invoice.delete()
        messages.success(request, 'Deleted.')
        return redirect(f'{kind}_list')
    return render(request, 'erp/confirm_delete.html', {'object': invoice, 'cancel_url': f'{kind}_detail', 'cancel_pk': pk})


# ---------- ledger ----------

@tenant_view('ledger')
def ledger_index(request):
    rng = get_range(request, default='all')
    rows = queries.party_balances(request.tenant, rng.start, rng.end)
    q = (request.GET.get('q') or '').lower()
    rows = [p for p in rows if (p.opening or p.debit or p.credit or p.closing) and q in p.name.lower()]
    receivable, payable = queries.receivable_payable(rows)
    return render(request, 'erp/ledger_index.html', {'rows': rows, 'receivable': receivable, 'payable': payable, **range_context(request, rng)})


@tenant_view('ledger')
def ledger_party(request, pk):
    party = get_object_or_404(Party, pk=pk, tenant=request.tenant)
    rng = get_range(request, default='all')
    report = REPORTS['party-ledger'].build(request.tenant, rng, party)
    if request.GET.get('format') == 'pdf':
        return file_response(pdf.report_pdf(request.tenant, report), f'ledger-{party.pk}.pdf', 'application/pdf')
    st = queries.party_statement(party, rng)
    feats = request.tenant.enabled_features()
    for e, _ in st['rows']:
        inv = e.invoice or (e.payment.invoice if e.payment_id else None)
        e.ref_code, e.detail_url = '', ''
        if inv:
            inv.tenant = request.tenant
            e.ref_code = inv.code
            if KIND_FEATURE[inv.kind] in feats:
                e.detail_url = reverse(f'{inv.kind}_detail', args=[inv.pk])
        e.type_label = ({'opening': 'Opening balance', 'manual': 'Manual entry'}.get(e.kind)
                        or (('Sale invoice' if e.debit else 'Purchase bill') if e.kind == 'invoice'
                            else ('Money paid' if e.debit else 'Money received')))
    cur = request.tenant.currency_symbol
    wa_text = (f'Assalam o Alaikum {party.name}, your outstanding balance with {request.tenant.name} is {cur} {st["closing"]:,.2f}. '
               f'Please arrange payment. Thank you.') if st['closing'] > 0 else ''
    return render(request, 'erp/ledger_party.html', {'party': party, 'st': st, 'wa_text': wa_text, **range_context(request, rng)})


@tenant_view('ledger')
def ledger_entry_new(request):
    form = LedgerEntryForm(request.POST or None, tenant=request.tenant, initial={'party': request.GET.get('party')})
    if request.method == 'POST' and form.is_valid():
        entry = form.save(commit=False)
        entry.kind = LedgerEntry.MANUAL
        entry.save()
        messages.success(request, 'Entry added.')
        return redirect('ledger_party', pk=entry.party_id)
    return render(request, 'erp/form.html', {'form': form, 'title': 'Manual ledger entry', 'cancel_url': 'ledger'})


@tenant_view('ledger')
def ledger_entry_delete(request, pk):
    entry = get_object_or_404(LedgerEntry, pk=pk, tenant=request.tenant, kind=LedgerEntry.MANUAL)
    party_id = entry.party_id
    if request.method == 'POST':
        entry.delete()
        messages.success(request, 'Entry deleted.')
        return redirect('ledger_party', pk=party_id)
    return render(request, 'erp/confirm_delete.html', {'object': entry, 'cancel_url': 'ledger_party', 'cancel_pk': party_id})


# ---------- reports ----------

@tenant_view('reports')
def report_index(request):
    feats = request.tenant.enabled_features()
    return render(request, 'erp/report_index.html', {'reports': [r for r in REPORTS.values() if not r.feature or r.feature in feats]})


@tenant_view('reports')
def report_view(request, slug):
    definition = REPORTS.get(slug)
    if not definition or (definition.feature and definition.feature not in request.tenant.enabled_features()):
        raise Http404
    rng = get_range(request, default='month')
    party = None
    if definition.needs_party and request.GET.get('party'):
        party = Party.objects.filter(pk=request.GET['party'], tenant=request.tenant).first()
    report = definition.build(request.tenant, rng, party) if (party or not definition.needs_party) else None
    fmt = request.GET.get('format')
    if report and fmt == 'pdf':
        return file_response(pdf.report_pdf(request.tenant, report), f'{slug}-{timezone.localdate()}.pdf', 'application/pdf')
    if report and fmt == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="{slug}-{timezone.localdate()}.csv"'
        writer = csv.writer(resp)
        writer.writerow(report.columns)
        writer.writerows(report.rows)
        if report.totals:
            writer.writerow(report.totals)
        return resp
    table = None
    if report:
        right = [a == 'r' for a in report.aligns]
        table = {'head': list(zip(report.columns, right)), 'rows': [list(zip(r, right)) for r in report.rows],
                 'totals': list(zip(report.totals, right)) if report.totals else None}
    return render(request, 'erp/report_view.html', {
        'definition': definition, 'report': report, 'table': table, 'party': party,
        'parties': Party.objects.filter(tenant=request.tenant) if definition.needs_party else None,
        **range_context(request, rng)})


# ---------- stock ----------

def _wh_param(request):
    wid = request.GET.get('warehouse')
    return Warehouse.objects.filter(tenant=request.tenant, pk=wid).first() if wid and wid.isdigit() else None


def _decorate_moves(request, moves):
    """Attach a reference code and detail link to each move (invoice moves link to the invoice)."""
    feats = request.tenant.enabled_features()
    for m in moves:
        m.ref_code, m.detail_url = '', ''
        if m.invoice_id:
            m.invoice.tenant = request.tenant
            m.ref_code = m.invoice.code
            if KIND_FEATURE[m.invoice.kind] in feats:
                m.detail_url = reverse(f'{m.invoice.kind}_detail', args=[m.invoice_id])
    return moves


@tenant_view('stock')
def stock_overview(request):
    tenant = request.tenant
    wh, q = _wh_param(request), request.GET.get('q')
    status = request.GET.get('status') if request.GET.get('status') in ('low', 'out') else ''
    base = stock.stock_summary(tenant, wh)
    rows = stock.stock_summary(tenant, wh, q, status or None) if (q or status) else base
    if status == 'low':
        rows = sorted(rows, key=lambda p: p.on_hand / p.reorder_level if p.reorder_level else 0)
    stats = {'items': len(base), 'value': sum((p.value for p in base), Decimal('0')),
             'low': sum(1 for p in base if p.status == 'low'), 'out': sum(1 for p in base if p.status == 'out')}
    page, keep = paginate(request, rows)
    return render(request, 'erp/stock_overview.html', {
        'page': page, 'qs': keep, 'stats': stats, 'status': status, 'wh': wh, 'many': stock.has_many_warehouses(tenant),
        'warehouses': stock.active_warehouses(tenant), 'has_products': Product.objects.filter(tenant=tenant, track_stock=True).exists()})


@tenant_view('stock')
def stock_product(request, pk):
    tenant = request.tenant
    product = get_object_or_404(Product.objects.select_related('unit'), pk=pk, tenant=tenant)
    rng, wh = get_range(request, default='all'), _wh_param(request)
    card = stock.stock_card(product, rng, wh)
    _decorate_moves(request, [m for m, _ in card['rows']])
    summary = next((p for p in stock.stock_summary(tenant, wh) if p.pk == product.pk), None)
    return render(request, 'erp/stock_product.html', {
        'product': product, 'card': card, 's': summary, 'wh': wh, 'many': stock.has_many_warehouses(tenant),
        'warehouses': stock.active_warehouses(tenant), 'breakdown': stock.warehouse_breakdown(product), **range_context(request, rng)})


@tenant_view('stock')
def stock_moves(request):
    tenant = request.tenant
    rng, wh = get_range(request, default='month'), _wh_param(request)
    qs = rng.apply(StockMove.objects.filter(tenant=tenant).select_related('product__unit', 'warehouse', 'invoice').order_by('-date', '-id'))
    pid, kind = request.GET.get('product'), request.GET.get('kind')
    if pid and pid.isdigit():
        qs = qs.filter(product_id=pid)
    if kind in dict(StockMove.KINDS):
        qs = qs.filter(kind=kind)
    if wh:
        qs = qs.filter(warehouse=wh)
    page, keep = paginate(request, qs)
    _decorate_moves(request, list(page))
    return render(request, 'erp/stock_moves.html', {
        'page': page, 'qs': keep, 'wh': wh, 'many': stock.has_many_warehouses(tenant), 'warehouses': stock.active_warehouses(tenant),
        'products': Product.objects.filter(tenant=tenant, track_stock=True), 'kinds': StockMove.KINDS, **range_context(request, rng)})


@tenant_view('stock')
def stock_adjust(request):
    tenant = request.tenant
    initial = {'product': request.GET.get('product')} if request.GET.get('product') else {}
    form = StockAdjustForm(request.POST or None, tenant=tenant, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        move = form.save()
        messages.success(request, 'Stock updated.')
        return redirect('stock_product', pk=move.product_id)
    levels = {p.pk: {'qty': float(p.on_hand), 'unit': p.unit.symbol} for p in stock.stock_summary(tenant)}
    return render(request, 'erp/stock_adjust.html', {'form': form, 'title': 'Adjust stock', 'cancel_url': 'stock_overview', 'levels_json': json.dumps(levels)})


@tenant_view('stock')
def stock_transfer(request):
    tenant = request.tenant
    if not stock.has_many_warehouses(tenant):
        messages.info(request, 'Add a second warehouse first, then you can move stock between them.')
        return redirect('warehouse_list')
    form = StockTransferForm(request.POST or None, tenant=tenant, user=request.user, initial={'product': request.GET.get('product')})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Stock moved.')
        return redirect('stock_product', pk=form.cleaned_data['product'].pk)
    return render(request, 'erp/form.html', {'form': form, 'title': 'Move stock between warehouses', 'cancel_url': 'stock_overview'})


@tenant_view('stock')
def stock_move_delete(request, pk):
    move = get_object_or_404(StockMove, pk=pk, tenant=request.tenant, kind__in=StockMove.MANUAL_KINDS)
    if request.method == 'POST':
        pid = move.product_id
        (StockMove.objects.filter(tenant=request.tenant, transfer_ref=move.transfer_ref) if move.transfer_ref else StockMove.objects.filter(pk=move.pk)).delete()
        messages.success(request, 'Stock entry deleted.')
        return redirect('stock_product', pk=pid)
    return render(request, 'erp/confirm_delete.html', {'object': f'{move.get_kind_display()}: {move.product} ({move.quantity.normalize():f})',
                                                       'cancel_url': 'stock_product', 'cancel_pk': move.product_id})


class WarehouseList(TenantViewMixin, ListView):
    feature, model, template_name = 'stock', Warehouse, 'erp/warehouse_list.html'

    def get_queryset(self):
        stock.default_warehouse(self.request.tenant)
        return super().get_queryset()


class WarehouseCreate(TenantFormMixin, CreateView):
    feature, model, form_class, list_url, title = 'stock', Warehouse, WarehouseForm, 'warehouse_list', 'New warehouse'


class WarehouseUpdate(TenantFormMixin, UpdateView):
    feature, model, form_class, list_url, title = 'stock', Warehouse, WarehouseForm, 'warehouse_list', 'Edit warehouse'


class WarehouseDelete(TenantDelete):
    feature, model, list_url = 'stock', Warehouse, 'warehouse_list'
