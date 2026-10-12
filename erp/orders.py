"""Quotation / sales order / purchase order logic: progress tracking and conversions."""
from decimal import Decimal

from django.db.models import Sum

from .models import Invoice, Order, OrderItem


def invoice_kind_for(order):
    return Invoice.PURCHASE if order.kind == Order.PURCHASE_ORDER else Invoice.SALE


def sync_progress(order):
    """Recompute how much of each line has been invoiced, from the invoices / bills made from it."""
    want = invoice_kind_for(order)
    items = list(order.items.all())
    for it in items:
        done = it.invoice_items.filter(invoice__kind=want).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
        if done != it.invoiced_qty:
            it.invoiced_qty = done
            it.save(update_fields=['invoiced_qty'])
    if order.is_quote and order.status != Order.CONVERTED and any(i.invoiced_qty > 0 for i in items):
        Order.objects.filter(pk=order.pk).update(status=Order.CONVERTED)


def prefill_rows(order):
    """Invoice-form rows for what is still left to invoice on an order."""
    return [{'product': it.product_id, 'quantity': format(it.remaining.normalize(), 'f'), 'unit': it.unit_id, 'rate': it.rate, 'order_item': it.pk}
            for it in order.items.select_related('product') if it.remaining > 0]


def convert_quote_to_sales_order(quote, user=None):
    """Copy an accepted quotation into a new sales order and mark the quotation converted."""
    so = Order.objects.create(tenant=quote.tenant, kind=Order.SALES_ORDER, party=quote.party, notes=quote.notes, terms=quote.terms,
                              due_date=None, source=quote, created_by=user)
    OrderItem.objects.bulk_create([OrderItem(order=so, product=i.product, quantity=i.quantity, unit=i.unit, rate=i.rate) for i in quote.items.all()])
    so.recalculate()
    Order.objects.filter(pk=quote.pk).update(status=Order.CONVERTED)
    return so
