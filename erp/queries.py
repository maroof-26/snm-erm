from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.utils import timezone

from .models import Invoice, InvoiceItem, LedgerEntry, Party, Payment

ZERO = Value(Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2))


def with_paid(qs):
    """Annotate invoices with paid_total and due (total - paid)."""
    return qs.annotate(paid_total=Coalesce(Sum('payments__amount'), ZERO)).annotate(due=F('total') - F('paid_total'))


def party_balances(tenant, start=None, end=None):
    """Parties annotated with opening / debit / credit / closing for [start, end]. closing > 0 = receivable."""
    in_range = Q()
    if start:
        in_range &= Q(ledger__date__gte=start)
    if end:
        in_range &= Q(ledger__date__lte=end)
    before = Q(ledger__date__lt=start) if start else Q(pk__isnull=True)
    parties = Party.objects.filter(tenant=tenant).annotate(
        opening=Coalesce(Sum(F('ledger__debit') - F('ledger__credit'), filter=before), ZERO),
        debit=Coalesce(Sum('ledger__debit', filter=in_range), ZERO),
        credit=Coalesce(Sum('ledger__credit', filter=in_range), ZERO),
    )
    result = list(parties)
    for p in result:
        p.closing = p.opening + p.debit - p.credit
    return result


def receivable_payable(balances):
    receivable = sum((p.closing for p in balances if p.closing > 0), Decimal('0'))
    payable = -sum((p.closing for p in balances if p.closing < 0), Decimal('0'))
    return receivable, payable


def party_statement(party, rng):
    """Ledger statement with running balance between rng.start and rng.end."""
    entries = LedgerEntry.objects.filter(party=party).select_related('invoice', 'payment__invoice')
    opening = Decimal('0')
    if rng.start:
        prior = entries.filter(date__lt=rng.start).aggregate(d=Sum('debit'), c=Sum('credit'))
        opening = (prior['d'] or 0) - (prior['c'] or 0)
    rows, running = [], opening
    debit_total = credit_total = Decimal('0')
    for e in rng.apply(entries):
        running += e.debit - e.credit
        debit_total += e.debit
        credit_total += e.credit
        rows.append((e, running))
    return {'opening': opening, 'rows': rows, 'debit': debit_total, 'credit': credit_total, 'closing': running}


def _sum(qs, field='total'):
    return qs.aggregate(s=Coalesce(Sum(field), ZERO))['s']


def dashboard_data(tenant, rng):
    today = timezone.localdate()
    invoices = Invoice.objects.filter(tenant=tenant)
    sales = rng.apply(invoices.filter(kind=Invoice.SALE))
    purchases = rng.apply(invoices.filter(kind=Invoice.PURCHASE))
    payments = rng.apply(Payment.objects.filter(tenant=tenant))
    balances = party_balances(tenant)
    receivable, payable = receivable_payable(balances)

    trunc = TruncMonth if rng.is_long else TruncDay
    fmt = '%b %Y' if rng.is_long else '%d %b'

    def series(qs):
        return {row['p']: float(row['t']) for row in
                qs.annotate(p=trunc('date')).values('p').annotate(t=Sum('total'))}

    s_series, p_series = series(sales), series(purchases)
    periods = sorted(set(s_series) | set(p_series))

    overdue = with_paid(invoices.filter(kind=Invoice.SALE, due_date__lt=today)).filter(due__gt=0)
    return {
        'sales_total': _sum(sales), 'purchases_total': _sum(purchases),
        'received': _sum(payments.filter(direction=Payment.IN), 'amount'),
        'paid_out': _sum(payments.filter(direction=Payment.OUT), 'amount'),
        'receivable': receivable, 'payable': payable,
        'overdue_count': overdue.count(), 'overdue_total': _sum(overdue, 'due'),
        'chart': {'labels': [p.strftime(fmt) for p in periods],
                  'sales': [s_series.get(p, 0) for p in periods],
                  'purchases': [p_series.get(p, 0) for p in periods]},
        'top_customers': sales.values('party__name').annotate(t=Sum('total')).order_by('-t')[:5],
        'top_receivables': sorted((p for p in balances if p.closing > 0), key=lambda p: -p.closing)[:5],
        'recent_sales': with_paid(invoices.filter(kind=Invoice.SALE)).select_related('party', 'tenant')[:6],
    }


def item_summary(tenant, kind, rng):
    """Quantity and amount per product+unit for sales or purchases."""
    items = InvoiceItem.objects.filter(invoice__tenant=tenant, invoice__kind=kind)
    items = rng.apply(items, 'invoice__date')
    amount = ExpressionWrapper(F('quantity') * F('rate'), output_field=DecimalField(max_digits=18, decimal_places=2))
    return (items.values('product__name', 'unit__symbol')
            .annotate(q=Sum('quantity'), a=Sum(amount)).order_by('product__name'))
