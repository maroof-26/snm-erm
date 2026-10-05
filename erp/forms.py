from decimal import Decimal

from django import forms
from django.utils import timezone
from django.forms import inlineformset_factory

from . import stock
from .models import Invoice, InvoiceItem, LedgerEntry, Party, Payment, Product, StockMove, Warehouse

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


class CommaNumberInput(forms.TextInput):
    """Numeric text box that shows thousands separators while typing (see base.html JS); commas are stripped on submit."""

    def __init__(self, attrs=None, decimals=2):
        super().__init__({'inputmode': 'decimal', 'autocomplete': 'off', 'data-comma': '1', 'data-decimals': str(decimals), **(attrs or {})})

    def value_from_datadict(self, data, files, name):
        value = data.get(name)
        return value.replace(',', '').replace(' ', '') if isinstance(value, str) else value


class BootstrapMixin:
    def style_fields(self):
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs['class'] = 'form-check-input'
            elif isinstance(w, forms.Select):
                w.attrs['class'] = 'form-select'
            else:
                w.attrs['class'] = 'form-control'
            if isinstance(f, forms.DecimalField):
                f.widget = w = CommaNumberInput(decimals=f.decimal_places or 2)
                w.attrs['class'] = 'form-control'


class TenantForm(BootstrapMixin, forms.ModelForm):
    """ModelForm bound to a tenant: sets it on the instance; subclasses restrict FK choices."""

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        if hasattr(self.instance, 'tenant_id'):
            self.instance.tenant = tenant
        self.style_fields()

    def scoped(self, field, model, **filters):
        self.fields[field].queryset = model.objects.filter(tenant=self.tenant, **filters)


class PartyForm(TenantForm):
    class Meta:
        model = Party
        fields = ['name', 'kind', 'phone', 'email', 'address', 'opening_balance', 'is_active']
        widgets = {'address': forms.Textarea(attrs={'rows': 2})}


class ProductForm(TenantForm):
    opening_qty = forms.DecimalField(required=False, min_value=Decimal('0'), max_digits=14, decimal_places=3, label='Stock in hand now',
                                     help_text='Optional. How much you have today. Saved as your opening stock.')
    opening_cost = forms.DecimalField(required=False, min_value=Decimal('0'), max_digits=14, decimal_places=2, label='Cost per unit',
                                      help_text='Optional. What one unit cost you; used to value your stock.')

    class Meta:
        model = Product
        fields = ['name', 'sku', 'unit', 'price', 'track_stock', 'reorder_level', 'is_active']
        labels = {'track_stock': 'Count stock of this item', 'reorder_level': 'Low-stock alert at'}

    field_order = ['name', 'sku', 'unit', 'price', 'track_stock', 'opening_qty', 'opening_cost', 'reorder_level', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._creating = not self.instance.pk
        self.fields['unit'].queryset = self.tenant.available_units()
        if self._creating and self.tenant.default_unit_id:
            self.fields['unit'].initial = self.tenant.default_unit_id
        if 'stock' not in self.tenant.enabled_features():
            for f in ('track_stock', 'reorder_level', 'opening_qty', 'opening_cost'):
                del self.fields[f]
        else:
            self.fields['reorder_level'].required = False
            if not self._creating:
                del self.fields['opening_qty'], self.fields['opening_cost']

    def clean_reorder_level(self):
        return self.cleaned_data.get('reorder_level') or Decimal('0')

    def save(self, commit=True):
        product = super().save(commit=commit)
        qty = self.cleaned_data.get('opening_qty')
        if commit and self._creating and qty and product.track_stock:
            stock.add_move(self.tenant, product, stock.default_warehouse(self.tenant), qty, StockMove.OPENING,
                           unit_cost=self.cleaned_data.get('opening_cost') or None, reason='Opening stock')
        return product


class InvoiceForm(TenantForm):
    paid_now = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=2, label='Amount paid now',
                                  help_text='Optional. Records a payment against this invoice.')
    method = forms.ChoiceField(choices=Payment.METHODS, required=False, label='Payment method')

    class Meta:
        model = Invoice
        fields = ['party', 'date', 'warehouse', 'due_date', 'notes']
        widgets = {'date': DATE, 'due_date': DATE, 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'stock' in self.tenant.enabled_features() and stock.has_many_warehouses(self.tenant):
            wf = self.fields['warehouse']
            wf.queryset = stock.active_warehouses(self.tenant)
            wf.empty_label = None
            wf.required = True
            wf.label = 'Warehouse'
            if not self.instance.pk:
                wf.initial = stock.default_warehouse(self.tenant).pk
        else:
            del self.fields['warehouse']
        kinds = [Party.CUSTOMER if self.instance.kind == Invoice.SALE else Party.VENDOR, Party.BOTH]
        self.scoped('party', Party, is_active=True, kind__in=kinds)
        self.fields['party'].label = 'Customer' if self.instance.kind == Invoice.SALE else 'Vendor'
        self.fields['party'].empty_label = 'Select customer' if self.instance.kind == Invoice.SALE else 'Select vendor'
        if self.instance.pk:
            del self.fields['paid_now'], self.fields['method']


class InvoiceItemForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['product', 'quantity', 'unit', 'rate']

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields['product'].queryset = Product.objects.filter(tenant=tenant, is_active=True)
        self.fields['unit'].queryset = tenant.available_units()
        self.fields['product'].empty_label = 'Select item'
        self.fields['unit'].empty_label = 'Unit'
        self.fields['unit'].label_from_instance = lambda u: u.symbol
        if tenant.default_unit_id and not self.instance.pk:
            self.fields['unit'].initial = tenant.default_unit_id
        self.style_fields()

    def clean(self):
        data = super().clean()
        product, unit = data.get('product'), data.get('unit')
        if product and unit and product.track_stock and unit != product.unit and 'stock' in self.tenant.enabled_features():
            self.add_error('unit', f'Stock of this item is counted in {product.unit.symbol}.')
        return data


ItemFormSet = inlineformset_factory(Invoice, InvoiceItem, form=InvoiceItemForm, extra=0, min_num=1, validate_min=True, can_delete=True)


class PaymentForm(TenantForm):
    class Meta:
        model = Payment
        fields = ['direction', 'party', 'invoice', 'amount', 'method', 'date', 'reference', 'notes']
        widgets = {'date': DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        direction = self.initial.get('direction')
        if direction in (Payment.IN, Payment.OUT) and not self.instance.pk:
            self.fields['direction'].widget = forms.HiddenInput()
            kinds = [Party.CUSTOMER if direction == Payment.IN else Party.VENDOR, Party.BOTH]
            self.scoped('party', Party, is_active=True, kind__in=kinds)
        else:
            self.scoped('party', Party, is_active=True)
        self.fields['party'].label = 'Customer' if direction == Payment.IN else 'Vendor' if direction == Payment.OUT else 'Party'
        self.fields['amount'].label = 'Amount'
        self.fields['invoice'].label = 'For which invoice? (optional)'
        self.scoped('invoice', Invoice)
        self.fields['invoice'].queryset = self.fields['invoice'].queryset.select_related('tenant')
        self.fields['invoice'].required = False

    def clean(self):
        data = super().clean()
        inv = data.get('invoice')
        if inv:
            if data.get('party') and data['party'] != inv.party:
                self.add_error('invoice', 'This invoice belongs to a different party.')
            expected = Payment.IN if inv.kind == Invoice.SALE else Payment.OUT
            if data.get('direction') and data['direction'] != expected:
                self.add_error('direction', 'Direction does not match the invoice type.')
        return data


class LedgerEntryForm(TenantForm):
    """Manual ledger entry: one amount + "I gave / I took" decides debit vs credit."""
    GAVE, TOOK = 'gave', 'took'
    direction = forms.ChoiceField(
        choices=[(GAVE, 'I gave'), (TOOK, 'I took')], widget=forms.RadioSelect, label='What happened?',
        help_text='I gave = they now owe you more (Lene hain). I took = you now owe them more (Dene hain).')
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=14, decimal_places=2)

    class Meta:
        model = LedgerEntry
        fields = ['party', 'date', 'description']
        widgets = {'date': DATE}

    field_order = ['party', 'date', 'direction', 'amount', 'description']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scoped('party', Party)
        self.fields['description'].required = False
        self.fields['description'].label = 'Note (optional)'
        self.style_fields()

    def clean(self):
        data = super().clean()
        if not data.get('description') and data.get('direction'):
            data['description'] = 'Amount given' if data['direction'] == self.GAVE else 'Amount taken'
        return data

    def save(self, commit=True):
        entry = super().save(commit=False)
        amount = self.cleaned_data['amount']
        gave = self.cleaned_data['direction'] == self.GAVE
        entry.debit, entry.credit = (amount, 0) if gave else (0, amount)
        if commit:
            entry.save()
        return entry


class StockAdjustForm(BootstrapMixin, forms.Form):
    """Add, remove or recount stock by hand (opening stock, damage, found items, stock-take)."""
    ADD, REMOVE, SET = 'add', 'remove', 'set'
    REASONS = [('Opening stock', 'Opening stock'), ('Found / correction', 'Found / correction'), ('Damaged / lost', 'Damaged / lost'),
               ('Expired', 'Expired'), ('Used internally', 'Used internally'), ('Stock count', 'Stock count'), ('Other', 'Other')]

    product = forms.ModelChoiceField(queryset=Product.objects.none(), empty_label='Select item')
    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.none(), required=False, empty_label=None)
    mode = forms.ChoiceField(choices=[(ADD, 'Add'), (REMOVE, 'Remove'), (SET, 'Set count')], widget=forms.RadioSelect, label='What do you want to do?',
                             help_text='Set count = you counted the shelf; we fix the difference for you.')
    quantity = forms.DecimalField(min_value=Decimal('0'), max_digits=14, decimal_places=3, label='Quantity')
    unit_cost = forms.DecimalField(required=False, min_value=Decimal('0'), max_digits=14, decimal_places=2, label='Cost per unit (optional)',
                                   help_text='Only for added stock. Used to value your stock.')
    reason = forms.ChoiceField(choices=REASONS, initial='Found / correction')
    date = forms.DateField(widget=DATE, initial=timezone.localdate)
    note = forms.CharField(required=False, max_length=200, label='Note (optional)')

    def __init__(self, *args, tenant, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant, self.user = tenant, user
        self.fields['product'].queryset = Product.objects.filter(tenant=tenant, track_stock=True, is_active=True)
        whs = stock.active_warehouses(tenant)
        self.fields['warehouse'].queryset = whs
        default = stock.default_warehouse(tenant)
        self.fields['warehouse'].initial = default.pk
        if whs.count() < 2:
            self.fields['warehouse'].widget = forms.HiddenInput()
        else:
            self.fields['warehouse'].label = 'Warehouse'
        self.style_fields()

    def clean(self):
        data = super().clean()
        product, mode, qty = data.get('product'), data.get('mode'), data.get('quantity')
        wh = data.get('warehouse') or stock.default_warehouse(self.tenant)
        data['warehouse'] = wh
        if not (product and mode and qty is not None):
            return data
        if mode in (self.ADD, self.REMOVE) and qty <= 0:
            self.add_error('quantity', 'Enter a quantity above zero.')
        current = stock.on_hand(product, wh)
        if mode == self.SET:
            data['delta'] = qty - current
            if data['delta'] == 0:
                self.add_error('quantity', f'Stock is already {qty.normalize():f}. Nothing to change.')
        else:
            data['delta'] = qty if mode == self.ADD else -qty
        if data.get('delta', 0) < 0 and not self.tenant.allow_negative_stock and current + data['delta'] < 0:
            self.add_error('quantity', f'Only {current.normalize():f} {product.unit.symbol} in stock.')
        return data

    def save(self):
        d = self.cleaned_data
        delta = d['delta']
        kind = StockMove.OPENING if (delta > 0 and d['reason'] == 'Opening stock') else (StockMove.ADJUST_IN if delta > 0 else StockMove.ADJUST_OUT)
        reason = 'Stock count' if d['mode'] == self.SET else d['reason']
        return stock.add_move(self.tenant, d['product'], d['warehouse'], delta, kind, date=d['date'], reason=reason, note=d.get('note', ''),
                              unit_cost=(d.get('unit_cost') or None) if delta > 0 else None, user=self.user)


class StockTransferForm(BootstrapMixin, forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), empty_label='Select item')
    source = forms.ModelChoiceField(queryset=Warehouse.objects.none(), label='From', empty_label=None)
    target = forms.ModelChoiceField(queryset=Warehouse.objects.none(), label='To', empty_label=None)
    quantity = forms.DecimalField(min_value=Decimal('0.001'), max_digits=14, decimal_places=3)
    date = forms.DateField(widget=DATE, initial=timezone.localdate)
    note = forms.CharField(required=False, max_length=200, label='Note (optional)')

    def __init__(self, *args, tenant, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant, self.user = tenant, user
        self.fields['product'].queryset = Product.objects.filter(tenant=tenant, track_stock=True, is_active=True)
        whs = stock.active_warehouses(tenant)
        self.fields['source'].queryset = self.fields['target'].queryset = whs
        self.fields['source'].initial = stock.default_warehouse(tenant).pk
        self.style_fields()

    def clean(self):
        data = super().clean()
        src, dst, product, qty = data.get('source'), data.get('target'), data.get('product'), data.get('quantity')
        if src and dst and src == dst:
            self.add_error('target', 'Choose a different warehouse.')
        elif product and src and qty and not self.tenant.allow_negative_stock and stock.on_hand(product, src) < qty:
            self.add_error('quantity', f'Only {stock.on_hand(product, src).normalize():f} {product.unit.symbol} in {src.name}.')
        return data

    def save(self):
        d = self.cleaned_data
        return stock.transfer(self.tenant, d['product'], d['source'], d['target'], d['quantity'], date=d['date'], note=d.get('note', ''), user=self.user)


class WarehouseForm(TenantForm):
    class Meta:
        model = Warehouse
        fields = ['name', 'is_default', 'is_active']
        labels = {'is_default': 'Main warehouse (used by default)'}

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if Warehouse.objects.filter(tenant=self.tenant, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('You already have a warehouse with this name.')
        return name

    def save(self, commit=True):
        wh = super().save(commit=commit)
        if commit and wh.is_default:
            Warehouse.objects.filter(tenant=self.tenant).exclude(pk=wh.pk).update(is_default=False)
        return wh
