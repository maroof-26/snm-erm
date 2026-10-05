from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Max, Sum
from django.utils import timezone

CENT = Decimal('0.01')


class TenantModel(models.Model):
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE, related_name='%(class)ss')

    class Meta:
        abstract = True


class Party(TenantModel):
    CUSTOMER, VENDOR, BOTH = 'customer', 'vendor', 'both'
    KINDS = [(CUSTOMER, 'Customer'), (VENDOR, 'Vendor'), (BOTH, 'Customer & Vendor')]

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=KINDS, default=CUSTOMER)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    opening_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Positive = party owes you (receivable). Negative = you owe the party (payable).')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'parties'

    def __str__(self):
        return self.name


class Product(TenantModel):
    name = models.CharField(max_length=150)
    sku = models.CharField('SKU / code', max_length=40, blank=True)
    unit = models.ForeignKey('tenants.Unit', on_delete=models.PROTECT)
    price = models.DecimalField('default rate', max_digits=14, decimal_places=2, default=0)
    track_stock = models.BooleanField('track stock', default=True, help_text='Turn off for services or things you do not count.')
    reorder_level = models.DecimalField('low-stock level', max_digits=14, decimal_places=3, default=0,
                                        help_text='Alert when stock falls to this quantity or below.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Warehouse(TenantModel):
    name = models.CharField(max_length=80)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'name']
        constraints = [models.UniqueConstraint(fields=['tenant', 'name'], name='unique_warehouse_name')]

    def __str__(self):
        return self.name


class Invoice(TenantModel):
    SALE, PURCHASE = 'sale', 'purchase'
    KINDS = [(SALE, 'Sale'), (PURCHASE, 'Purchase')]

    kind = models.CharField(max_length=10, choices=KINDS)
    number = models.PositiveIntegerField(blank=True)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='invoices')
    date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='invoices',
                                  help_text='Where the stock comes from / goes to.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-number']
        constraints = [models.UniqueConstraint(fields=['tenant', 'kind', 'number'], name='unique_invoice_number')]

    def __str__(self):
        return self.code

    @property
    def code(self):
        prefix = self.tenant.sale_prefix if self.kind == self.SALE else self.tenant.purchase_prefix
        return f'{prefix}-{self.number:04d}'

    def save(self, *args, **kwargs):
        if self.number is None:
            last = Invoice.objects.filter(tenant=self.tenant, kind=self.kind).aggregate(m=Max('number'))['m']
            self.number = (last or 0) + 1
        super().save(*args, **kwargs)

    def recalculate(self):
        self.total = sum((i.amount for i in self.items.all()), Decimal('0'))
        self.save(update_fields=['total'])

    @property
    def paid(self):
        if hasattr(self, 'paid_total'):
            return self.paid_total
        return self.payments.aggregate(s=Sum('amount'))['s'] or Decimal('0')

    @property
    def balance(self):
        return self.total - self.paid

    @property
    def status(self):
        if self.total > 0 and self.paid >= self.total:
            return 'Paid'
        return 'Partial' if self.paid > 0 else 'Unpaid'


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.ForeignKey('tenants.Unit', on_delete=models.PROTECT)
    rate = models.DecimalField(max_digits=14, decimal_places=2)

    @property
    def amount(self):
        return (self.quantity * self.rate).quantize(CENT)


class Payment(TenantModel):
    IN, OUT = 'in', 'out'
    DIRECTIONS = [(IN, 'Received from customer'), (OUT, 'Paid to vendor')]
    METHODS = [('cash', 'Cash'), ('bank', 'Bank transfer'), ('cheque', 'Cheque'), ('other', 'Other')]

    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='payments')
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS, default='cash')
    reference = models.CharField(max_length=80, blank=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name='payments')
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-date', '-id']


class LedgerEntry(TenantModel):
    """Source of truth for balances. Debit = party owes us more; credit = we owe party more."""
    OPENING, INVOICE, PAYMENT, MANUAL = 'opening', 'invoice', 'payment', 'manual'

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name='ledger')
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=200)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    kind = models.CharField(max_length=10, default=MANUAL)
    invoice = models.OneToOneField(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name='ledger_entry')
    payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.CASCADE, related_name='ledger_entry')

    class Meta:
        ordering = ['date', 'id']
        verbose_name_plural = 'ledger entries'
        indexes = [models.Index(fields=['tenant', 'party', 'date'])]


class StockMove(TenantModel):
    """One line of the stock ledger. quantity is signed: + adds stock, - removes it."""
    OPENING, PURCHASE, SALE = 'opening', 'purchase', 'sale'
    ADJUST_IN, ADJUST_OUT = 'adjust_in', 'adjust_out'
    TRANSFER_IN, TRANSFER_OUT = 'transfer_in', 'transfer_out'
    KINDS = [(OPENING, 'Opening stock'), (PURCHASE, 'Purchase'), (SALE, 'Sale'), (ADJUST_IN, 'Stock added'),
             (ADJUST_OUT, 'Stock removed'), (TRANSFER_IN, 'Transfer in'), (TRANSFER_OUT, 'Transfer out')]
    MANUAL_KINDS = (OPENING, ADJUST_IN, ADJUST_OUT, TRANSFER_IN, TRANSFER_OUT)

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='moves')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='moves')
    date = models.DateField(default=timezone.localdate)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                    help_text='Cost per unit for stock coming in; used to value stock.')
    kind = models.CharField(max_length=12, choices=KINDS)
    reason = models.CharField(max_length=60, blank=True)
    note = models.CharField(max_length=200, blank=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name='stock_moves')
    transfer_ref = models.CharField(max_length=32, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['tenant', 'product', 'date'])]

    def __str__(self):
        return f'{self.product} {self.quantity:+}'

    @property
    def qty_in(self):
        return self.quantity if self.quantity > 0 else None

    @property
    def qty_out(self):
        return -self.quantity if self.quantity < 0 else None
