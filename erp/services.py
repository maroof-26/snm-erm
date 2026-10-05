"""Keeps LedgerEntry rows in sync with invoices, payments and opening balances."""
from .models import Invoice, LedgerEntry, Payment


def sync_invoice(inv):
    is_sale = inv.kind == Invoice.SALE
    LedgerEntry.objects.update_or_create(invoice=inv, defaults=dict(
        tenant=inv.tenant, party=inv.party, date=inv.date, kind=LedgerEntry.INVOICE,
        description=f"{'Sale' if is_sale else 'Purchase'} {inv.code}",
        debit=inv.total if is_sale else 0, credit=0 if is_sale else inv.total))


def sync_payment(pay):
    received = pay.direction == Payment.IN
    ref = f' ({pay.reference})' if pay.reference else ''
    inv = f' against {pay.invoice.code}' if pay.invoice_id else ''
    LedgerEntry.objects.update_or_create(payment=pay, defaults=dict(
        tenant=pay.tenant, party=pay.party, date=pay.date, kind=LedgerEntry.PAYMENT,
        description=f"{'Received' if received else 'Paid'} - {pay.get_method_display()}{ref}{inv}",
        debit=0 if received else pay.amount, credit=pay.amount if received else 0))


def sync_opening(party):
    qs = LedgerEntry.objects.filter(party=party, kind=LedgerEntry.OPENING)
    if not party.opening_balance:
        qs.delete()
        return
    bal = party.opening_balance
    LedgerEntry.objects.update_or_create(party=party, kind=LedgerEntry.OPENING, defaults=dict(
        tenant=party.tenant, date=party.created_at.date(), description='Opening balance',
        debit=bal if bal > 0 else 0, credit=-bal if bal < 0 else 0))
