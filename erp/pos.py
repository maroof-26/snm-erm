"""Point-of-sale logic: shifts and the one-shot checkout that creates an invoice + payments."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from . import stock
from .models import Invoice, InvoiceItem, Party, Payment, PosSession, Product

WALK_IN = 'Walk-in Customer'
CENT, MILLI = Decimal('0.01'), Decimal('0.001')
METHODS = {code for code, _ in Payment.METHODS}


class CheckoutError(Exception):
    """A problem the cashier can fix (shown as a message on the POS screen)."""


def open_session(tenant, user):
    return PosSession.objects.filter(tenant=tenant, user=user, closed_at__isnull=True).first()


def walk_in(tenant):
    party, _ = Party.objects.get_or_create(tenant=tenant, name=WALK_IN, defaults={'kind': Party.CUSTOMER})
    return party


def _dec(value, places, label):
    try:
        d = Decimal(str(value).replace(',', '')) if value not in (None, '') else Decimal('0')
    except InvalidOperation:
        raise CheckoutError(f'{label} is not a number.')
    if not d.is_finite():
        raise CheckoutError(f'{label} is not a number.')
    return d.quantize(places, rounding=ROUND_HALF_UP)


def checkout(tenant, user, session, payload):
    """Create the sale invoice (stock + ledger follow automatically) and its payments. Returns (invoice, change)."""
    raw_lines = payload.get('lines') or []
    if not raw_lines or len(raw_lines) > 300:
        raise CheckoutError('Add at least one item.')
    products = {p.pk: p for p in Product.objects.filter(tenant=tenant, is_active=True, pk__in=[l.get('id') for l in raw_lines if isinstance(l.get('id'), int)])}
    lines = []
    for l in raw_lines:
        product = products.get(l.get('id'))
        if product is None:
            raise CheckoutError('An item in the cart is no longer available.')
        qty, rate = _dec(l.get('qty'), MILLI, 'Quantity'), _dec(l.get('rate'), CENT, 'Rate')
        if qty <= 0:
            raise CheckoutError(f'Enter a quantity for {product.name}.')
        if rate < 0:
            raise CheckoutError(f'Rate of {product.name} cannot be negative.')
        lines.append((product, qty, rate))
    subtotal = sum(((q * r).quantize(CENT) for _, q, r in lines), Decimal('0'))
    discount = _dec(payload.get('discount'), CENT, 'Discount')
    if discount < 0 or discount > subtotal:
        raise CheckoutError('Discount cannot be more than the bill.')
    total = subtotal - discount

    cust_id = payload.get('customer')
    if cust_id:
        party = Party.objects.filter(pk=cust_id, tenant=tenant, is_active=True, kind__in=[Party.CUSTOMER, Party.BOTH]).first()
        if party is None:
            raise CheckoutError('Customer not found.')
    else:
        party = walk_in(tenant)
    is_walk_in = party.name == WALK_IN and not cust_id

    payments = []
    for p in payload.get('payments') or []:
        amount = _dec(p.get('amount'), CENT, 'Payment')
        if amount < 0 or p.get('method') not in METHODS:
            raise CheckoutError('Invalid payment.')
        if amount:
            payments.append((p['method'], amount))
    paid = sum((a for _, a in payments), Decimal('0'))
    if is_walk_in and paid < total:
        raise CheckoutError(f'Walk-in customers must pay in full. Short by {total - paid:,.2f}.')

    if 'stock' in tenant.enabled_features() and not tenant.allow_negative_stock:
        needs = {}
        for product, qty, _ in lines:
            if product.track_stock:
                needs[product] = needs.get(product, Decimal('0')) + qty
        short = stock.shortages(tenant, stock.default_warehouse(tenant), needs)
        if short:
            raise CheckoutError('Not enough stock: ' + '; '.join(f'{p.name} (have {have.normalize():f}, need {need.normalize():f})' for p, have, need in short))

    with transaction.atomic():
        invoice = Invoice.objects.create(tenant=tenant, kind=Invoice.SALE, party=party, date=timezone.localdate(), created_by=user,
                                         discount=discount, pos_session=session)
        for product, qty, rate in lines:
            InvoiceItem.objects.create(invoice=invoice, product=product, quantity=qty, unit=product.unit, rate=rate)
        invoice.recalculate()
        left = invoice.total
        for method, amount in payments:
            applied = min(amount, left)
            if applied > 0:
                Payment.objects.create(tenant=tenant, party=party, invoice=invoice, direction=Payment.IN, amount=applied, method=method,
                                       date=invoice.date, notes='POS')
                left -= applied
    return invoice, max(paid - invoice.total, Decimal('0'))
