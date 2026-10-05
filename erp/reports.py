"""Report registry. Each builder returns a Report used for HTML, PDF and CSV output."""
from dataclasses import dataclass, field

from .formatting import money, qty


def d(x):
    return x.strftime('%d %b %Y')

from . import stock
from .models import Invoice, Payment, StockMove
from .queries import item_summary, party_balances, party_statement, with_paid


@dataclass
class Report:
    title: str
    subtitle: str
    columns: list
    aligns: str  # one char per column: l / r
    rows: list
    totals: list | None = None
    landscape: bool = False
    weights: list | None = None


@dataclass
class ReportDef:
    slug: str
    title: str
    description: str
    build: callable
    needs_party: bool = False
    feature: str = ''


def _register(kind, title):
    def build(tenant, rng, party=None):
        qs = with_paid(rng.apply(Invoice.objects.filter(tenant=tenant, kind=kind))).select_related('party', 'tenant').order_by('date', 'number')
        rows = [[d(i.date), i.code, i.party.name, money(i.total), money(i.paid_total), money(i.due)] for i in qs]
        who = 'Customer' if kind == Invoice.SALE else 'Vendor'
        paid = 'Received' if kind == Invoice.SALE else 'Paid'
        totals = ['Total', '', '', money(sum(i.total for i in qs)), money(sum(i.paid_total for i in qs)), money(sum(i.due for i in qs))]
        return Report(title, rng.label, ['Date', 'No.', who, 'Total', paid, 'Balance'], 'lllrrr', rows, totals, weights=[1.5, 1.4, 3, 1, 1, 1])
    return build


def _by_product(kind, title):
    def build(tenant, rng, party=None):
        data = list(item_summary(tenant, kind, rng))
        rows = [[d['product__name'], d['unit__symbol'], qty(d['q']), money(d['a'])] for d in data]
        totals = ['Total amount', '', '', money(sum(d['a'] for d in data))]
        return Report(title, rng.label, ['Product', 'Unit', 'Quantity', 'Amount'], 'llrr', rows, totals, weights=[4, 1, 1, 1])
    return build


def _party_ledger(tenant, rng, party=None):
    st = party_statement(party, rng)
    rows = [['', 'Opening balance', '', '', money(st['opening'])]]
    rows += [[d(e.date), e.description, money(e.debit) if e.debit else '', money(e.credit) if e.credit else '', money(run)] for e, run in st['rows']]
    totals = ['Total', '', money(st['debit']), money(st['credit']), money(st['closing'])]
    sub = f'{party.name} | {rng.label} | Positive = party owes you'
    return Report(f'Ledger: {party.name}', sub, ['Date', 'Description', 'Debit', 'Credit', 'Balance'], 'llrrr', rows, totals, weights=[1.5, 4, 1, 1, 1])


def _outstanding(receivable):
    def build(tenant, rng, party=None):
        sign = 1 if receivable else -1
        rows_src = sorted((p for p in party_balances(tenant, None, rng.end) if p.closing * sign > 0), key=lambda p: -p.closing * sign)
        rows = [[p.name, p.phone, money(p.closing * sign)] for p in rows_src]
        totals = ['Total', '', money(sum(p.closing * sign for p in rows_src))]
        title = 'Receivables (owed to you)' if receivable else 'Payables (you owe)'
        return Report(title, f'As of {rng.end or "today"}', ['Party', 'Phone', 'Amount'], 'llr', rows, totals, weights=[4, 2, 1])
    return build


def _payments(tenant, rng, party=None):
    qs = rng.apply(Payment.objects.filter(tenant=tenant)).select_related('party', 'invoice', 'invoice__tenant').order_by('date', 'id')
    rows = [[d(p.date), p.party.name, p.get_method_display(), p.reference, p.invoice.code if p.invoice else '',
             money(p.amount) if p.direction == Payment.IN else '', money(p.amount) if p.direction == Payment.OUT else ''] for p in qs]
    tin = sum(p.amount for p in qs if p.direction == Payment.IN)
    tout = sum(p.amount for p in qs if p.direction == Payment.OUT)
    return Report('Payments register', rng.label, ['Date', 'Party', 'Method', 'Reference', 'Invoice', 'Received', 'Paid'],
                  'lllllrr', rows, ['Total', '', '', '', '', money(tin), money(tout)], landscape=True, weights=[1.5, 3, 1.2, 2, 1.4, 1, 1])


def _stock_summary(tenant, rng, party=None):
    rows_src = stock.stock_summary(tenant)
    rows = [[p.name, p.unit.symbol, qty(p.on_hand), qty(p.reorder_level) if p.reorder_level else '-',
             money(p.avg_cost) if p.avg_cost is not None else '-', money(p.value)] for p in rows_src]
    totals = ['Total value', '', '', '', '', money(sum(p.value for p in rows_src))]
    return Report('Stock summary', 'Quantity in hand and value (average purchase cost)', ['Item', 'Unit', 'In stock', 'Low at', 'Avg cost', 'Value'],
                  'llrrrr', rows, totals, weights=[4, 1, 1.2, 1.2, 1.4, 1.5])


def _low_stock(tenant, rng, party=None):
    src = stock.low_stock(tenant)
    rows = [[p.name, p.unit.symbol, qty(p.on_hand), qty(p.reorder_level) if p.reorder_level else '-', qty(p.short_by) if p.reorder_level else '-',
             'Out of stock' if p.status == 'out' else 'Low'] for p in src]
    return Report('Low stock items', 'Items at or below their low-stock level', ['Item', 'Unit', 'In stock', 'Low at', 'Need at least', 'Status'],
                  'llrrrl', rows, None, weights=[4, 1, 1.2, 1.2, 1.5, 1.5])


def _stock_moves(tenant, rng, party=None):
    qs = rng.apply(StockMove.objects.filter(tenant=tenant).select_related('product__unit', 'warehouse', 'invoice')).order_by('date', 'id')
    rows = []
    for m in qs:
        if m.invoice_id:
            m.invoice.tenant = tenant
        ref = m.invoice.code if m.invoice_id else (m.reason or m.note)
        rows.append([d(m.date), m.product.name, m.get_kind_display(), qty(m.quantity) if m.quantity > 0 else '', qty(-m.quantity) if m.quantity < 0 else '',
                     m.warehouse.name, ref])
    return Report('Stock movements', rng.label, ['Date', 'Item', 'Type', 'In', 'Out', 'Warehouse', 'Reference'], 'lllrrll', rows, None,
                  landscape=True, weights=[1.4, 3, 1.8, 1, 1, 1.6, 2])


REPORTS = {r.slug: r for r in [
    ReportDef('sales', 'Sales register', 'All sale invoices with received amount and balance.', _register(Invoice.SALE, 'Sales register')),
    ReportDef('purchases', 'Purchase register', 'All purchase bills with paid amount and balance.', _register(Invoice.PURCHASE, 'Purchase register')),
    ReportDef('sales-by-product', 'Sales by product', 'Quantity and amount sold per product and unit.', _by_product(Invoice.SALE, 'Sales by product')),
    ReportDef('purchases-by-product', 'Purchases by product', 'Quantity and amount bought per product and unit.', _by_product(Invoice.PURCHASE, 'Purchases by product')),
    ReportDef('party-ledger', 'Party ledger', 'Statement of one customer or vendor with running balance.', _party_ledger, needs_party=True),
    ReportDef('receivables', 'Receivables', 'Customers who owe you, as of the end date.', _outstanding(True)),
    ReportDef('payables', 'Payables', 'Vendors you owe, as of the end date.', _outstanding(False)),
    ReportDef('payments', 'Payments register', 'Money received and paid.', _payments),
    ReportDef('stock-summary', 'Stock summary', 'Quantity in hand and value for every item.', _stock_summary, feature='stock'),
    ReportDef('low-stock', 'Low stock items', 'Items at or below their low-stock level.', _low_stock, feature='stock'),
    ReportDef('stock-moves', 'Stock movements', 'Every stock in and out in the chosen dates.', _stock_moves, feature='stock'),
]}
