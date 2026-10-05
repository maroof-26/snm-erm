"""Stock logic: on-hand quantities, valuation, low-stock, and the moves created by invoices and adjustments."""
import uuid
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import Invoice, Product, StockMove, Warehouse

ZERO3 = Value(Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=3))
ZERO = Value(Decimal('0'), output_field=DecimalField(max_digits=22, decimal_places=5))


# ------------------------------------------------------------------ warehouses
def default_warehouse(tenant):
    """The tenant's default warehouse (created as "Main" on first use)."""
    wh = Warehouse.objects.filter(tenant=tenant, is_active=True).order_by('-is_default', 'id').first()
    if wh is None:
        wh = Warehouse.objects.create(tenant=tenant, name='Main', is_default=True)
    return wh


def active_warehouses(tenant):
    default_warehouse(tenant)
    return Warehouse.objects.filter(tenant=tenant, is_active=True)


def has_many_warehouses(tenant):
    return active_warehouses(tenant).count() > 1


# ------------------------------------------------------------------ moves created by invoices
def sync_invoice_stock(inv):
    """Rebuild the stock moves of one invoice from its items (sale = stock out, purchase = stock in)."""
    inv.stock_moves.all().delete()
    wh = inv.warehouse or default_warehouse(inv.tenant)
    sale = inv.kind == Invoice.SALE
    moves = [StockMove(tenant=inv.tenant, product=it.product, warehouse=wh, date=inv.date,
                       quantity=-it.quantity if sale else it.quantity, unit_cost=None if sale else it.rate,
                       kind=StockMove.SALE if sale else StockMove.PURCHASE, invoice=inv)
             for it in inv.items.select_related('product') if it.product.track_stock]
    StockMove.objects.bulk_create(moves)


# ------------------------------------------------------------------ quantities
def on_hand(product, warehouse=None, exclude_invoice=None):
    qs = StockMove.objects.filter(product=product)
    if warehouse:
        qs = qs.filter(warehouse=warehouse)
    if exclude_invoice is not None and exclude_invoice.pk:
        qs = qs.exclude(invoice=exclude_invoice)
    return qs.aggregate(s=Coalesce(Sum('quantity'), ZERO3))['s']


def status_of(on_hand_qty, reorder_level):
    if on_hand_qty <= 0:
        return 'out'
    return 'low' if reorder_level > 0 and on_hand_qty <= reorder_level else 'ok'


def stock_summary(tenant, warehouse=None, q=None, status=None):
    """Tracked products annotated with on_hand, avg_cost, value, status (and short_by)."""
    wf = Q(moves__warehouse=warehouse) if warehouse else Q()
    inbound = Q(moves__quantity__gt=0, moves__unit_cost__isnull=False) & wf
    cost_expr = ExpressionWrapper(F('moves__quantity') * F('moves__unit_cost'), output_field=DecimalField(max_digits=22, decimal_places=5))
    qs = (Product.objects.filter(tenant=tenant, track_stock=True, is_active=True).select_related('unit')
          .annotate(on_hand_q=Coalesce(Sum('moves__quantity', filter=wf if warehouse else None), ZERO3),
                    cost_qty=Coalesce(Sum('moves__quantity', filter=inbound), ZERO3),
                    cost_total=Coalesce(Sum(cost_expr, filter=inbound), ZERO)))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    rows = []
    for p in qs:
        p.on_hand = p.on_hand_q
        p.avg_cost = (p.cost_total / p.cost_qty) if p.cost_qty > 0 else None
        p.value = (max(p.on_hand, Decimal('0')) * p.avg_cost) if p.avg_cost is not None else Decimal('0')
        p.status = status_of(p.on_hand, p.reorder_level)
        p.short_by = max(p.reorder_level - p.on_hand, Decimal('0'))
        rows.append(p)
    if status in ('low', 'out'):
        rows = [p for p in rows if p.status == status] if status == 'out' else [p for p in rows if p.status in ('low', 'out')]
    return rows


def low_stock(tenant):
    """Products at or below their low-stock level (including out of stock), most urgent first."""
    rows = [p for p in stock_summary(tenant) if p.status in ('low', 'out')]
    return sorted(rows, key=lambda p: (p.on_hand / p.reorder_level) if p.reorder_level > 0 else Decimal('-1'))


def low_stock_count(tenant):
    return Product.objects.filter(tenant=tenant, track_stock=True, is_active=True).annotate(
        oh=Coalesce(Sum('moves__quantity'), ZERO3)).filter(oh__lte=F('reorder_level')).count()


def warehouse_breakdown(product):
    rows = (StockMove.objects.filter(product=product).values('warehouse__name').annotate(q=Sum('quantity')).order_by('warehouse__name'))
    return [r for r in rows if r['q']]


def stock_card(product, rng, warehouse=None):
    """Stock moves of one product in a date range with a running balance."""
    moves = StockMove.objects.filter(product=product).select_related('warehouse', 'invoice')
    if warehouse:
        moves = moves.filter(warehouse=warehouse)
    opening = Decimal('0')
    if rng.start:
        opening = moves.filter(date__lt=rng.start).aggregate(s=Coalesce(Sum('quantity'), ZERO3))['s']
    rows, running = [], opening
    total_in = total_out = Decimal('0')
    for m in rng.apply(moves):
        running += m.quantity
        total_in += m.quantity if m.quantity > 0 else 0
        total_out += -m.quantity if m.quantity < 0 else 0
        rows.append((m, running))
    return {'opening': opening, 'rows': rows, 'total_in': total_in, 'total_out': total_out, 'closing': running}


def shortages(tenant, warehouse, needs, exclude_invoice=None):
    """needs = {product: qty}; returns [(product, available, needed)] where stock would go below zero."""
    out = []
    for product, qty in needs.items():
        available = on_hand(product, warehouse, exclude_invoice)
        if available - qty < 0:
            out.append((product, available, qty))
    return out


# ------------------------------------------------------------------ manual stock operations
def add_move(tenant, product, warehouse, quantity, kind, *, date=None, unit_cost=None, reason='', note='', user=None, transfer_ref=''):
    kw = dict(tenant=tenant, product=product, warehouse=warehouse, quantity=quantity, kind=kind, unit_cost=unit_cost,
              reason=reason, note=note, created_by=user, transfer_ref=transfer_ref)
    if date:
        kw['date'] = date
    return StockMove.objects.create(**kw)


def transfer(tenant, product, source, target, quantity, *, date=None, note='', user=None):
    ref = uuid.uuid4().hex
    add_move(tenant, product, source, -quantity, StockMove.TRANSFER_OUT, date=date, reason=f'To {target.name}', note=note, user=user, transfer_ref=ref)
    add_move(tenant, product, target, quantity, StockMove.TRANSFER_IN, date=date, reason=f'From {source.name}', note=note, user=user, transfer_ref=ref)
    return ref
