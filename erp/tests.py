from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tenants.models import Feature, Membership, Tenant, Unit

from .models import Invoice, LedgerEntry, Party, Payment, Product
from .queries import party_balances, receivable_payable


def make_tenant(name, features='all', units=()):
    t = Tenant.objects.create(name=name, slug=name.lower())
    t.features.set(Feature.objects.all() if features == 'all' else Feature.objects.filter(code__in=features))
    if units:
        t.units.set(Unit.objects.filter(symbol__in=units))
    u = get_user_model().objects.create_user(name.lower(), password='pw')
    Membership.objects.create(user=u, tenant=t, is_owner=True)
    return t, u


class ErpTests(TestCase):
    def setUp(self):
        self.t, self.user = make_tenant('Cement', units=['kg', 'bag'])
        self.client.login(username='cement', password='pw')
        self.kg = Unit.objects.get(symbol='kg')
        self.cust = Party.objects.create(tenant=self.t, name='Ali', kind='customer', opening_balance=100)
        self.vend = Party.objects.create(tenant=self.t, name='Mill', kind='vendor')
        self.prod = Product.objects.create(tenant=self.t, name='OPC', unit=self.kg, price=10)

    def post_invoice(self, kind, party, qty='5', rate='10', paid=''):
        data = {'party': party.pk, 'date': '2026-09-01', 'due_date': '', 'notes': '', 'paid_now': paid, 'method': 'cash',
                'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
                'items-0-product': self.prod.pk, 'items-0-quantity': qty, 'items-0-unit': self.kg.pk, 'items-0-rate': rate}
        return self.client.post(reverse(f'{kind}_new'), data)

    def test_seeded_units_and_features(self):
        self.assertTrue(Unit.objects.filter(symbol='L').exists())
        self.assertEqual(Feature.objects.count(), 9)

    def test_sale_updates_ledger_and_paid(self):
        r = self.post_invoice('sale', self.cust, qty='50', rate='10', paid='200')
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        inv = Invoice.objects.get()
        self.assertEqual((inv.number, inv.total, inv.paid, inv.balance, inv.status), (1, 500, 200, 300, 'Partial'))
        bal = {p.name: p.closing for p in party_balances(self.t)}
        self.assertEqual(bal['Ali'], Decimal('400'))  # 100 opening + 500 sale - 200 paid
        self.assertEqual(receivable_payable(party_balances(self.t)), (Decimal('400'), Decimal('0')))

    def test_purchase_creates_payable_and_numbering(self):
        self.post_invoice('purchase', self.vend, qty='10', rate='7')
        self.post_invoice('purchase', self.vend, qty='1', rate='7')
        self.assertEqual(list(Invoice.objects.order_by('number').values_list('number', flat=True)), [1, 2])
        self.assertEqual(receivable_payable(party_balances(self.t))[1], Decimal('77'))

    def test_edit_and_delete_keep_ledger_consistent(self):
        self.post_invoice('sale', self.cust)
        inv = Invoice.objects.get()
        self.assertEqual(LedgerEntry.objects.filter(invoice=inv).get().debit, 50)
        inv.delete()
        self.assertFalse(LedgerEntry.objects.filter(kind='invoice').exists())

    def test_payment_form_and_manual_entry(self):
        self.post_invoice('sale', self.cust)
        inv = Invoice.objects.get()
        r = self.client.post(reverse('payment_new'), {'direction': 'in', 'party': self.cust.pk, 'invoice': inv.pk,
                                                       'date': '2026-09-02', 'amount': '50', 'method': 'bank'})
        self.assertEqual(r.status_code, 302, r.context['form'].errors if r.status_code == 200 else '')
        self.assertEqual(Invoice.objects.get().status, 'Paid')
        r = self.client.post(reverse('ledger_entry_new'), {'party': self.cust.pk, 'date': '2026-09-03', 'description': 'Adj', 'direction': 'gave', 'amount': '5'})
        self.assertEqual(r.status_code, 302)

    def test_units_limited_to_tenant_choice(self):
        symbols = set(self.t.available_units().values_list('symbol', flat=True))
        self.assertEqual(symbols, {'kg', 'bag'})
        other, _ = make_tenant('Other')  # no units chosen -> all units
        self.assertGreater(other.available_units().count(), 2)

    def test_tenant_isolation(self):
        self.post_invoice('sale', self.cust)
        _, _ = make_tenant('Rival')
        self.client.login(username='rival', password='pw')
        inv = Invoice.objects.get()
        self.assertEqual(self.client.get(reverse('sale_detail', args=[inv.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('party_list')), 'Ali')

    def test_feature_toggle(self):
        t, _ = make_tenant('Lite', features=['dashboard', 'parties'])
        self.client.login(username='lite', password='pw')
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('sale_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_index')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), reverse('sale_list'))
        t.features.add(Feature.objects.get(code='sales'))  # pulls in products/parties
        self.client.get('/')  # fresh request -> fresh tenant
        self.assertEqual(self.client.get(reverse('sale_list')).status_code, 200)

    def test_all_pages_render(self):
        self.post_invoice('sale', self.cust, paid='10')
        self.post_invoice('purchase', self.vend)
        inv = Invoice.objects.filter(kind='sale').get()
        urls = ['dashboard', 'party_list', 'product_list', 'sale_list', 'purchase_list', 'payment_list', 'ledger', 'report_index',
                'party_new', 'product_new', 'payment_new', 'sale_new', 'purchase_new', 'ledger_entry_new']
        for name in urls:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for name in ('sale_detail', 'sale_edit', 'sale_delete'):
            self.assertEqual(self.client.get(reverse(name, args=[inv.pk])).status_code, 200, name)
        self.assertEqual(self.client.get(reverse('ledger_party', args=[self.cust.pk]) + '?range=all').status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard') + '?range=year').status_code, 200)
        self.assertEqual(self.client.get(reverse('sale_list') + '?from=2026-01-01&to=2026-12-31&status=unpaid&q=ali').status_code, 200)

    def test_pdfs_and_csv(self):
        self.post_invoice('sale', self.cust, paid='10')
        inv = Invoice.objects.get()
        r = self.client.get(reverse('sale_pdf', args=[inv.pk]))
        self.assertEqual((r.status_code, r['Content-Type'], r.content[:4]), (200, 'application/pdf', b'%PDF'))
        for slug in ('sales', 'purchases', 'sales-by-product', 'purchases-by-product', 'receivables', 'payables', 'payments'):
            html = self.client.get(reverse('report_view', args=[slug]) + '?range=all')
            self.assertEqual(html.status_code, 200, slug)
            pdf = self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=pdf')
            self.assertEqual(pdf.content[:4], b'%PDF', slug)
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=csv')['Content-Type'], 'text/csv')
        r = self.client.get(reverse('report_view', args=['party-ledger']) + f'?range=all&party={self.cust.pk}&format=pdf')
        self.assertEqual(r.content[:4], b'%PDF')
        r = self.client.get(reverse('ledger_party', args=[self.cust.pk]) + '?format=pdf')
        self.assertEqual(r.content[:4], b'%PDF')

    def test_admin_onboarding_creates_owner(self):
        admin = get_user_model().objects.create_superuser('root', password='pw')
        self.client.login(username='root', password='pw')
        r = self.client.post('/admin/tenants/tenant/add/', {
            'name': 'Newco', 'slug': 'newco', 'is_active': 'on', 'features': [f.pk for f in Feature.objects.all()],
            'currency_symbol': 'Rs', 'sale_prefix': 'INV', 'purchase_prefix': 'BILL',
            'owner_username': 'newowner', 'owner_password': 'secret123',
            'memberships-TOTAL_FORMS': 0, 'memberships-INITIAL_FORMS': 0, 'memberships-MIN_NUM_FORMS': 0, 'memberships-MAX_NUM_FORMS': 1000})
        self.assertEqual(r.status_code, 302, r.content[:2000] if r.status_code == 200 else '')
        self.assertEqual(Membership.objects.get(user__username='newowner').tenant.slug, 'newco')

    def test_login_required_and_no_tenant(self):
        self.client.logout()
        self.assertEqual(self.client.get('/').status_code, 302)
        get_user_model().objects.create_user('lonely', password='pw')
        self.client.login(username='lonely', password='pw')
        self.assertEqual(self.client.get('/').status_code, 403)


class SimplifiedFlowTests(ErpTests):
    """Reuses ErpTests fixtures (its own tests re-run too; cheap)."""

    def test_whatsapp_link_on_sale_and_ledger(self):
        self.cust.phone = '0300-1234567'
        self.cust.save()
        self.post_invoice('sale', self.cust, qty='2', rate='10')
        inv = Invoice.objects.get()
        html = self.client.get(reverse('sale_detail', args=[inv.pk])).content.decode()
        self.assertIn('https://wa.me/923001234567?text=', html)
        self.assertIn('https://wa.me/923001234567?text=', self.client.get(reverse('ledger_party', args=[self.cust.pk])).content.decode())

    def test_payment_direction_locked_and_party_filtered(self):
        html = self.client.get(reverse('payment_new') + '?direction=in').content.decode()
        self.assertIn('Ali', html)
        self.assertNotIn('>Mill<', html)
        self.assertIn('type="hidden" name="direction"', html)

    def test_party_create_redirects_to_next(self):
        r = self.client.post(reverse('party_new') + '?kind=customer&next=/sales/new/',
                             {'name': 'Fresh', 'kind': 'customer', 'opening_balance': '0', 'is_active': 'on'})
        self.assertRedirects(r, '/sales/new/', fetch_redirect_response=False)
        r = self.client.post(reverse('party_new') + '?next=https://evil.example/', {'name': 'X', 'kind': 'customer', 'opening_balance': '0'})
        self.assertEqual(r.url, reverse('party_list'))


class LedgerEntryFormTests(ErpTests):
    def post(self, **over):
        data = {'party': self.cust.pk, 'date': '2026-09-03', 'direction': 'gave', 'amount': '250', 'description': ''}
        data.update(over)
        return self.client.post(reverse('ledger_entry_new'), data)

    def test_gave_is_debit_and_took_is_credit_with_default_note(self):
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.post(direction='took', amount='40').status_code, 302)
        gave = LedgerEntry.objects.get(kind='manual', debit__gt=0)
        took = LedgerEntry.objects.get(kind='manual', credit__gt=0)
        self.assertEqual((gave.debit, gave.credit, gave.description), (250, 0, 'Amount given'))
        self.assertEqual((took.debit, took.credit, took.description), (0, 40, 'Amount taken'))

    def test_direction_required_and_description_optional(self):
        r = self.post(direction='')
        self.assertEqual(r.status_code, 200)
        self.assertIn('direction', r.context['form'].errors)
        self.assertNotIn('description', r.context['form'].errors)
        self.assertFalse(LedgerEntry.objects.filter(kind='manual').exists())

    def test_amount_must_be_positive(self):
        self.assertEqual(self.post(amount='0').status_code, 200)
        self.assertEqual(self.post(amount='').status_code, 200)

    def test_custom_note_kept(self):
        self.post(description='Cash advance')
        self.assertEqual(LedgerEntry.objects.get(kind='manual').description, 'Cash advance')


class LedgerModalTests(ErpTests):
    def test_rows_carry_details_for_modal_and_totals_present(self):
        self.post_invoice('sale', self.cust, qty='2', rate='10', paid='5')
        html = self.client.get(reverse('ledger_party', args=[self.cust.pk])).content.decode()
        inv = Invoice.objects.get()
        self.assertIn('id="ledgerModal"', html)
        self.assertIn('class="ledger-totals"', html)
        self.assertIn('data-type="Sale invoice"', html)
        self.assertIn('data-type="Money received"', html)
        self.assertIn(f'data-ref="{inv.code}"', html)
        self.assertIn(f'data-url="{reverse("sale_detail", args=[inv.pk])}"', html)

    def test_manual_entry_row_has_delete_link_only_for_manual(self):
        self.client.post(reverse('ledger_entry_new'), {'party': self.cust.pk, 'date': '2026-09-03', 'direction': 'took', 'amount': '9'})
        e = LedgerEntry.objects.get(kind='manual')
        html = self.client.get(reverse('ledger_party', args=[self.cust.pk])).content.decode()
        self.assertIn(f'data-delete="{reverse("ledger_entry_delete", args=[e.pk])}"', html)
        self.assertIn('data-type="Manual entry"', html)
        self.assertEqual(html.count('data-delete="/'), 1)


class CommaNumberTests(ErpTests):
    def test_comma_separated_numbers_are_accepted_everywhere(self):
        # invoice: quantity, rate, paid_now
        r = self.post_invoice('sale', self.cust, qty='1,000', rate='1,250.50', paid='500,000')
        self.assertEqual(r.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual((inv.total, inv.paid), (Decimal('1250500.00'), Decimal('500000.00')))
        # payment amount
        r = self.client.post(reverse('payment_new'), {'direction': 'in', 'party': self.cust.pk, 'invoice': inv.pk,
                                                       'date': '2026-09-02', 'amount': '250,000.50', 'method': 'cash'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Payment.objects.filter(invoice=inv).order_by('-id').first().amount, Decimal('250000.50'))
        # party opening balance (negative) and product price
        r = self.client.post(reverse('party_new'), {'name': 'Neg', 'kind': 'vendor', 'opening_balance': '-12,500.75', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Party.objects.get(name='Neg').opening_balance, Decimal('-12500.75'))
        r = self.client.post(reverse('product_new'), {'name': 'Pricey', 'unit': self.kg.pk, 'price': '1,234,567.00', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        # ledger amount
        r = self.client.post(reverse('ledger_entry_new'), {'party': self.cust.pk, 'date': '2026-09-03', 'direction': 'gave', 'amount': '2,500'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(LedgerEntry.objects.get(kind='manual').debit, Decimal('2500'))

    def test_inputs_are_comma_enabled_text_boxes(self):
        html = self.client.get(reverse('sale_new')).content.decode()
        self.assertIn('data-comma="1"', html)
        self.assertNotIn('type="number"', html)
        self.assertIn('data-decimals="3"', html)   # quantity allows 3 decimals

    def test_garbage_is_still_rejected(self):
        r = self.client.post(reverse('payment_new'), {'direction': 'in', 'party': self.cust.pk, 'date': '2026-09-02', 'amount': 'abc', 'method': 'cash'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('amount', r.context['form'].errors)


class LoginPageTests(ErpTests):
    def test_login_page_renders_and_works(self):
        self.client.logout()
        html = self.client.get(reverse('login')).content.decode()
        for needle in ('Welcome back', 'name="username"', 'name="password"', 'Sign in'):
            self.assertIn(needle, html)
        bad = self.client.post(reverse('login'), {'username': 'cement', 'password': 'wrong'})
        self.assertContains(bad, 'Wrong username or password')
        ok = self.client.post(reverse('login'), {'username': 'cement', 'password': 'pw'})
        self.assertRedirects(ok, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)


# ============================================================ stock module
from datetime import date as _date

from . import stock as stock_logic
from .models import StockMove, Warehouse


class StockTests(ErpTests):
    """self.t has every feature (stock included); self.prod = 'OPC' tracked, unit kg."""

    def add_stock(self, qty, cost=None, **kw):
        return stock_logic.add_move(self.t, self.prod, stock_logic.default_warehouse(self.t), Decimal(str(qty)), StockMove.OPENING,
                                    unit_cost=Decimal(str(cost)) if cost else None, **kw)

    def level(self, product=None, wh=None):
        return stock_logic.on_hand(product or self.prod, wh)

    def test_purchase_adds_and_sale_removes_stock(self):
        self.post_invoice('purchase', self.vend, qty='100', rate='7')
        self.assertEqual(self.level(), 100)
        self.post_invoice('sale', self.cust, qty='30', rate='10')
        self.assertEqual(self.level(), 70)
        kinds = list(StockMove.objects.order_by('id').values_list('kind', 'quantity'))
        self.assertEqual(kinds, [('purchase', Decimal('100')), ('sale', Decimal('-30'))])

    def test_editing_and_deleting_invoice_keeps_stock_right(self):
        self.post_invoice('purchase', self.vend, qty='100', rate='7')
        self.post_invoice('sale', self.cust, qty='30')
        sale = Invoice.objects.get(kind='sale')
        r = self.client.post(reverse('sale_edit', args=[sale.pk]), {
            'party': self.cust.pk, 'date': '2026-09-01', 'due_date': '', 'notes': '',
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
            'items-0-id': sale.items.get().pk, 'items-0-invoice': sale.pk, 'items-0-product': self.prod.pk,
            'items-0-quantity': '45', 'items-0-unit': self.kg.pk, 'items-0-rate': '10'})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['formset'].errors)
        self.assertEqual(self.level(), 55)                       # 100 - 45
        sale.delete()
        self.assertEqual(self.level(), 100)
        self.assertFalse(StockMove.objects.filter(kind='sale').exists())

    def test_untracked_items_make_no_moves(self):
        self.prod.track_stock = False
        self.prod.save()
        self.post_invoice('sale', self.cust, qty='5')
        self.assertFalse(StockMove.objects.exists())
        self.assertEqual(stock_logic.stock_summary(self.t), [])

    def test_average_cost_value_and_status(self):
        self.add_stock(10, cost=100)
        stock_logic.add_move(self.t, self.prod, stock_logic.default_warehouse(self.t), Decimal('30'), StockMove.PURCHASE, unit_cost=Decimal('200'))
        self.prod.reorder_level = Decimal('50')
        self.prod.save()
        row = stock_logic.stock_summary(self.t)[0]
        self.assertEqual((row.on_hand, row.avg_cost, row.value), (Decimal('40'), Decimal('175'), Decimal('7000')))   # (10*100+30*200)/40
        self.assertEqual(row.status, 'low')
        self.assertEqual(stock_logic.low_stock_count(self.t), 1)
        stock_logic.add_move(self.t, self.prod, stock_logic.default_warehouse(self.t), Decimal('-40'), StockMove.ADJUST_OUT)
        self.assertEqual(stock_logic.stock_summary(self.t)[0].status, 'out')
        stock_logic.add_move(self.t, self.prod, stock_logic.default_warehouse(self.t), Decimal('100'), StockMove.ADJUST_IN)
        self.assertEqual(stock_logic.stock_summary(self.t)[0].status, 'ok')
        self.assertEqual(stock_logic.low_stock_count(self.t), 0)

    def test_negative_stock_warns_when_allowed_and_blocks_when_not(self):
        self.post_invoice('sale', self.cust, qty='5')          # no stock yet, negatives allowed by default
        self.assertEqual(self.level(), -5)
        r = self.client.get(reverse('sale_list'))
        self.assertContains(r, 'below zero')
        self.t.allow_negative_stock = False
        self.t.save()
        r = self.post_invoice('sale', self.cust, qty='1')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Not enough stock')
        self.assertEqual(Invoice.objects.filter(kind='sale').count(), 1)
        self.add_stock(50)
        self.assertEqual(self.post_invoice('sale', self.cust, qty='20').status_code, 302)
        self.assertEqual(self.level(), 25)

    def test_item_unit_must_match_stock_unit(self):
        bag = Unit.objects.get(symbol='bag')
        data = {'party': self.cust.pk, 'date': '2026-09-01', 'due_date': '', 'notes': '', 'paid_now': '', 'method': 'cash',
                'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
                'items-0-product': self.prod.pk, 'items-0-quantity': '5', 'items-0-unit': bag.pk, 'items-0-rate': '10'}
        r = self.client.post(reverse('sale_new'), data)
        self.assertEqual(r.status_code, 200)
        self.assertIn('counted in kg', str(r.context['formset'].errors))

    def test_adjust_add_remove_and_set_count(self):
        def adjust(**d):
            data = {'product': self.prod.pk, 'warehouse': stock_logic.default_warehouse(self.t).pk, 'date': '2026-09-02', 'reason': 'Found / correction', 'note': ''}
            data.update(d)
            return self.client.post(reverse('stock_adjust'), data)
        self.assertEqual(adjust(mode='add', quantity='100', unit_cost='50', reason='Opening stock').status_code, 302)
        self.assertEqual(StockMove.objects.get().kind, 'opening')
        self.assertEqual(adjust(mode='remove', quantity='15', reason='Damaged / lost').status_code, 302)
        self.assertEqual(self.level(), 85)
        self.assertEqual(adjust(mode='set', quantity='60').status_code, 302)              # counted 60 -> remove 25
        self.assertEqual(self.level(), 60)
        last = StockMove.objects.order_by('id').last()
        self.assertEqual((last.quantity, last.kind, last.reason), (Decimal('-25'), 'adjust_out', 'Stock count'))
        self.assertEqual(adjust(mode='set', quantity='60').status_code, 200)               # no change -> error
        self.assertEqual(adjust(mode='add', quantity='0').status_code, 200)
        self.assertEqual(adjust(quantity='5').status_code, 200)                            # mode required

    def test_adjust_cannot_go_negative_when_disallowed(self):
        self.t.allow_negative_stock = False
        self.t.save()
        self.add_stock(10)
        r = self.client.post(reverse('stock_adjust'), {'product': self.prod.pk, 'warehouse': stock_logic.default_warehouse(self.t).pk, 'date': '2026-09-02',
                                                       'mode': 'remove', 'quantity': '11', 'reason': 'Damaged / lost'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.level(), 10)

    def test_product_form_opening_stock(self):
        r = self.client.post(reverse('product_new'), {'name': 'Brick', 'unit': self.kg.pk, 'price': '12', 'track_stock': 'on', 'reorder_level': '100',
                                                       'opening_qty': '5,000', 'opening_cost': '9.5', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        brick = Product.objects.get(name='Brick')
        m = StockMove.objects.get(product=brick)
        self.assertEqual((m.kind, m.quantity, m.unit_cost), ('opening', Decimal('5000'), Decimal('9.50')))
        self.assertEqual(brick.reorder_level, 100)
        r = self.client.post(reverse('product_new'), {'name': 'Plain', 'unit': self.kg.pk, 'price': '1', 'reorder_level': '', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)                                                # alert level optional
        self.assertFalse(StockMove.objects.filter(product__name='Plain').exists())

    def test_warehouses_and_transfer(self):
        main = stock_logic.default_warehouse(self.t)
        self.assertFalse(stock_logic.has_many_warehouses(self.t))
        self.assertRedirects(self.client.get(reverse('stock_transfer')), reverse('warehouse_list'))
        r = self.client.post(reverse('warehouse_new'), {'name': 'Shop', 'is_active': 'on'})
        self.assertEqual(r.status_code, 302)
        shop = Warehouse.objects.get(name='Shop')
        self.assertTrue(stock_logic.has_many_warehouses(self.t))
        self.assertEqual(self.client.post(reverse('warehouse_new'), {'name': 'shop', 'is_active': 'on'}).status_code, 200)   # duplicate name
        self.add_stock(100)
        r = self.client.post(reverse('stock_transfer'), {'product': self.prod.pk, 'source': main.pk, 'target': shop.pk, 'quantity': '40', 'date': '2026-09-02'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual((self.level(wh=main), self.level(wh=shop), self.level()), (60, 40, 100))
        self.assertEqual(self.client.post(reverse('stock_transfer'), {'product': self.prod.pk, 'source': main.pk, 'target': main.pk, 'quantity': '1', 'date': '2026-09-02'}).status_code, 200)
        # deleting one half of a transfer removes both
        out = StockMove.objects.get(kind='transfer_out')
        self.client.post(reverse('stock_move_delete', args=[out.pk]))
        self.assertEqual(self.level(), 100)
        self.assertFalse(StockMove.objects.filter(kind__startswith='transfer').exists())

    def test_sale_uses_chosen_warehouse(self):
        main = stock_logic.default_warehouse(self.t)
        shop = Warehouse.objects.create(tenant=self.t, name='Shop')
        self.add_stock(100)
        data = {'party': self.cust.pk, 'date': '2026-09-01', 'warehouse': shop.pk, 'due_date': '', 'notes': '', 'paid_now': '', 'method': 'cash',
                'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
                'items-0-product': self.prod.pk, 'items-0-quantity': '10', 'items-0-unit': self.kg.pk, 'items-0-rate': '10'}
        self.assertEqual(self.client.post(reverse('sale_new'), data).status_code, 302)
        self.assertEqual((self.level(wh=main), self.level(wh=shop)), (100, -10))

    def test_invoice_moves_cannot_be_deleted_by_hand(self):
        self.post_invoice('sale', self.cust, qty='5')
        mv = StockMove.objects.get(kind='sale')
        self.assertEqual(self.client.post(reverse('stock_move_delete', args=[mv.pk])).status_code, 404)

    def test_stock_pages_and_reports_render(self):
        self.post_invoice('purchase', self.vend, qty='100', rate='7')
        self.post_invoice('sale', self.cust, qty='30')
        self.prod.reorder_level = Decimal('200')
        self.prod.save()
        for name, args in [('stock_overview', []), ('stock_moves', []), ('stock_adjust', []), ('warehouse_list', []), ('warehouse_new', []),
                           ('stock_product', [self.prod.pk]), ('product_list', []), ('dashboard', [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse('stock_overview') + '?status=low&q=OPC').status_code, 200)
        self.assertEqual(self.client.get(reverse('stock_moves') + '?range=all&kind=sale').status_code, 200)
        self.assertContains(self.client.get(reverse('dashboard')), 'low on stock')
        self.assertContains(self.client.get(reverse('stock_overview')), 'OPC')
        for slug in ('stock-summary', 'low-stock', 'stock-moves'):
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all').status_code, 200, slug)
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=pdf').content[:4], b'%PDF', slug)
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=csv')['Content-Type'], 'text/csv', slug)

    def test_stock_is_a_switchable_feature(self):
        lite, _ = make_tenant('Lite', features=['dashboard', 'sales', 'purchases'])
        self.client.login(username='lite', password='pw')
        self.assertEqual(self.client.get(reverse('stock_overview')).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_view', args=['stock-summary'])).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), reverse('stock_overview'))
        self.assertNotIn('stock-summary', [r.slug for r in self.client.get(reverse('report_index')).context.get('reports', [])] if self.client.get(reverse('report_index')).status_code == 200 else [])
        lite.features.add(Feature.objects.get(code='stock'))
        self.client.get('/')
        self.assertEqual(self.client.get(reverse('stock_overview')).status_code, 200)

    def test_stock_is_tenant_isolated(self):
        self.add_stock(10)
        other, _ = make_tenant('Rival')
        self.client.login(username='rival', password='pw')
        self.assertEqual(self.client.get(reverse('stock_product', args=[self.prod.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('stock_overview')), 'OPC')
