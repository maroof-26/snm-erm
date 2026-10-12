import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from tenants.models import Feature, Unit

from . import stock as stock_logic
from .models import Invoice, LedgerEntry, Party, Payment, PosSession, Product, StockMove
from .tests import make_tenant


class PosBase(TestCase):
    def setUp(self):
        self.t, self.user = make_tenant('Shop', units=['pcs', 'kg'])
        self.client.login(username='shop', password='pw')
        self.pcs = Unit.objects.get(symbol='pcs')
        self.soap = Product.objects.create(tenant=self.t, name='Soap', unit=self.pcs, price=40, barcode='890100')
        self.tea = Product.objects.create(tenant=self.t, name='Tea 1kg', unit=Unit.objects.get(symbol='kg'), price=900, sku='TEA1')
        stock_logic.add_move(self.t, self.soap, stock_logic.default_warehouse(self.t), Decimal('100'), StockMove.OPENING)
        self.cust = Party.objects.create(tenant=self.t, name='Ali', kind='customer', phone='0300-1')

    def open_shift(self, cash='1,000'):
        return self.client.post(reverse('pos_open'), {'opening_cash': cash})

    def sell(self, lines=None, **extra):
        payload = {'customer': None, 'discount': 0, 'lines': lines if lines is not None else [{'id': self.soap.pk, 'qty': 2, 'rate': 40}],
                   'payments': [{'method': 'cash', 'amount': 80}], **extra}
        return self.client.post(reverse('pos_checkout'), json.dumps(payload), content_type='application/json')

    def level(self, product=None):
        return stock_logic.on_hand(product or self.soap)


class PosShiftAndCheckout(PosBase):
    def test_must_open_a_shift_first(self):
        self.assertRedirects(self.client.get(reverse('pos_home')), reverse('pos_open'))
        r = self.sell()
        self.assertEqual((r.status_code, r.json()['ok']), (400, False))
        self.assertIn('shift', r.json()['error'].lower())
        self.assertRedirects(self.open_shift(), reverse('pos_home'))
        self.assertEqual(PosSession.objects.get().opening_cash, Decimal('1000'))
        self.assertRedirects(self.client.get(reverse('pos_open')), reverse('pos_home'))     # already open
        self.assertEqual(self.client.get(reverse('pos_home')).status_code, 200)

    def test_walk_in_cash_sale_creates_invoice_payment_stock_and_no_debt(self):
        self.open_shift()
        r = self.sell(lines=[{'id': self.soap.pk, 'qty': 3, 'rate': 40}, {'id': self.tea.pk, 'qty': '0.5', 'rate': 900}],
                      payments=[{'method': 'cash', 'amount': 1000}])
        d = r.json()
        self.assertTrue(d['ok'], d)
        self.assertEqual((d['total'], d['change']), ('570.00', '430.00'))
        inv = Invoice.objects.get()
        self.assertEqual((inv.party.name, inv.kind, inv.total, inv.paid, inv.balance), ('Walk-in Customer', 'sale', Decimal('570'), Decimal('570'), Decimal('0')))
        self.assertEqual(inv.pos_session, PosSession.objects.get())
        pay = Payment.objects.get()
        self.assertEqual((pay.amount, pay.method, pay.direction), (Decimal('570'), 'cash', 'in'))      # change is not income
        self.assertEqual(self.level(), 97)                                                              # stock fell
        self.assertEqual(stock_logic.on_hand(self.tea), Decimal('-0.5'))
        bal = sum(e.debit - e.credit for e in LedgerEntry.objects.filter(party=inv.party))
        self.assertEqual(bal, 0)

    def test_discount_split_payments_and_credit_sale(self):
        self.open_shift()
        r = self.sell(lines=[{'id': self.tea.pk, 'qty': 1, 'rate': 900}], discount='100',
                      payments=[{'method': 'cash', 'amount': 500}, {'method': 'card', 'amount': 300}])
        self.assertTrue(r.json()['ok'], r.json())
        inv = Invoice.objects.get()
        self.assertEqual((inv.discount, inv.total), (Decimal('100'), Decimal('800')))
        self.assertEqual(sorted(Payment.objects.values_list('method', 'amount')), [('card', Decimal('300.00')), ('cash', Decimal('500.00'))])
        # named customer may leave a balance (credit)
        r = self.sell(lines=[{'id': self.tea.pk, 'qty': 1, 'rate': 900}], customer=self.cust.pk, payments=[{'method': 'cash', 'amount': 200}])
        self.assertTrue(r.json()['ok'], r.json())
        credit = Invoice.objects.order_by('id').last()
        self.assertEqual((credit.party, credit.balance), (self.cust, Decimal('700')))
        self.assertEqual(sum(e.debit - e.credit for e in LedgerEntry.objects.filter(party=self.cust)), Decimal('700'))

    def test_validation_errors_create_nothing(self):
        self.open_shift()
        bad = [
            dict(payments=[{'method': 'cash', 'amount': 10}]),                                         # walk-in must pay in full
            dict(lines=[]),                                                                            # empty cart
            dict(lines=[{'id': 99999, 'qty': 1, 'rate': 1}]),                                          # unknown item
            dict(lines=[{'id': self.soap.pk, 'qty': 0, 'rate': 40}]),                                  # zero qty
            dict(lines=[{'id': self.soap.pk, 'qty': 1, 'rate': -5}]),                                  # negative rate
            dict(discount=999),                                                                        # discount > bill
            dict(payments=[{'method': 'crypto', 'amount': 80}]),                                       # bad method
            dict(customer=99999),                                                                      # unknown customer
            dict(lines=[{'id': self.soap.pk, 'qty': 'abc', 'rate': 40}]),                              # junk number
        ]
        for extra in bad:
            r = self.sell(**extra)
            self.assertEqual((r.status_code, r.json()['ok']), (400, False), extra)
        self.assertEqual(self.client.post(reverse('pos_checkout'), 'not json', content_type='application/json').status_code, 400)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.level(), 100)

    def test_other_tenants_items_and_inactive_items_are_refused(self):
        other, _ = make_tenant('Rival')
        foreign = Product.objects.create(tenant=other, name='X', unit=self.pcs, price=1)
        self.open_shift()
        self.assertEqual(self.sell(lines=[{'id': foreign.pk, 'qty': 1, 'rate': 1}], payments=[{'method': 'cash', 'amount': 1}]).status_code, 400)
        self.soap.is_active = False
        self.soap.save()
        self.assertEqual(self.sell().status_code, 400)

    def test_negative_stock_rules(self):
        self.open_shift()
        big = [{'id': self.soap.pk, 'qty': 150, 'rate': 40}]
        pay = [{'method': 'cash', 'amount': 6000}]
        self.assertTrue(self.sell(lines=big, payments=pay).json()['ok'])                 # allowed by default
        self.assertEqual(self.level(), -50)
        self.t.allow_negative_stock = False
        self.t.save()
        r = self.sell(lines=[{'id': self.soap.pk, 'qty': 1, 'rate': 40}], payments=[{'method': 'cash', 'amount': 40}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('Not enough stock', r.json()['error'])
        self.assertEqual(Invoice.objects.count(), 1)

    def test_walk_in_party_is_created_once(self):
        self.open_shift()
        for _ in range(3):
            self.assertTrue(self.sell().json()['ok'])
        self.assertEqual(Party.objects.filter(tenant=self.t, name='Walk-in Customer').count(), 1)


class PosShiftReports(PosBase):
    def test_shift_summary_close_and_difference(self):
        self.open_shift('1,000')
        self.sell(payments=[{'method': 'cash', 'amount': 100}])                                       # 80 cash (20 change)
        self.sell(lines=[{'id': self.tea.pk, 'qty': 1, 'rate': 900}], discount='100', payments=[{'method': 'card', 'amount': 800}])
        s = PosSession.objects.get()
        sm = s.summary()
        self.assertEqual((sm['count'], sm['total'], sm['discount'], sm['received']), (2, Decimal('880'), Decimal('100'), Decimal('880')))
        self.assertEqual(dict(sm['by_method']), {'Cash': Decimal('80'), 'Card': Decimal('800')})
        self.assertEqual(sm['expected_cash'], Decimal('1080'))
        page = self.client.get(reverse('pos_shift'))
        self.assertContains(page, 'Cash should be')
        r = self.client.post(reverse('pos_shift'), {'closing_cash': '1,070', 'note': 'ok'})
        self.assertRedirects(r, reverse('pos_shift_detail', args=[s.pk]))
        s.refresh_from_db()
        self.assertFalse(s.is_open)
        self.assertEqual(s.summary()['difference'], Decimal('-10'))
        self.assertContains(self.client.get(reverse('pos_shift_detail', args=[s.pk])), 'Difference')
        self.assertEqual(self.sell().status_code, 400)                                                # closed -> cannot sell
        self.assertRedirects(self.client.get(reverse('pos_home')), reverse('pos_open'))
        self.assertEqual(self.client.post(reverse('pos_shift'), {'closing_cash': '1'}).status_code, 200)   # nothing open to close

    def test_close_requires_a_number(self):
        self.open_shift()
        r = self.client.post(reverse('pos_shift'), {'closing_cash': 'lots'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(PosSession.objects.get().is_open)

    def test_receipt_page(self):
        self.open_shift()
        d = self.sell(lines=[{'id': self.tea.pk, 'qty': 1, 'rate': 900}], discount='100', payments=[{'method': 'cash', 'amount': 1000}]).json()
        html = self.client.get(d['receipt'] + '?print=1&change=200').content.decode()
        for needle in ('Tea 1kg', 'TOTAL', '800.00', 'Discount', '-100.00', 'Cash', 'Change', '200', 'Thank you!', 'window.print'):
            self.assertIn(needle, html)
        self.assertNotIn('window.print();', self.client.get(d['receipt']).content.decode().split('<script>')[-1] if '<script>' in self.client.get(d['receipt']).content.decode() else '')
        other, _ = make_tenant('Rival')
        self.client.login(username='rival', password='pw')
        self.assertEqual(self.client.get(d['receipt']).status_code, 404)

    def test_till_page_embeds_items_safely_and_is_feature_gated(self):
        self.soap.name = 'Soap </script><b>x'
        self.soap.save()
        self.open_shift()
        html = self.client.get(reverse('pos_home')).content.decode()
        self.assertIn('pos-products', html)
        self.assertNotIn('</script><b>x', html)                      # escaped inside json_script
        self.assertIn('890100', html)                                # barcode available for scanning
        lite, _ = make_tenant('Lite', features=['dashboard', 'sales'])
        self.client.login(username='lite', password='pw')
        self.assertEqual(self.client.get(reverse('pos_home')).status_code, 403)
        lite.features.add(Feature.objects.get(code='pos'))            # pulls in sales, payments, parties, products
        self.client.get('/')
        self.assertEqual(self.client.get(reverse('pos_open')).status_code, 200)

    def test_shifts_are_per_tenant(self):
        self.open_shift()
        other, _ = make_tenant('Rival')
        self.client.login(username='rival', password='pw')
        s = PosSession.objects.get(tenant=self.t)
        self.assertEqual(self.client.get(reverse('pos_shift_detail', args=[s.pk])).status_code, 404)
        self.assertRedirects(self.client.get(reverse('pos_home')), reverse('pos_open'))


class ProductBarcodeAndDiscount(PosBase):
    def test_barcode_unique_per_tenant_and_only_shown_with_pos(self):
        r = self.client.post(reverse('product_new'), {'name': 'Dup', 'unit': self.pcs.pk, 'price': '5', 'barcode': '890100', 'is_active': 'on'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('barcode', r.context['form'].errors)
        self.assertEqual(self.client.post(reverse('product_new'), {'name': 'Fine', 'unit': self.pcs.pk, 'price': '5', 'barcode': '111', 'is_active': 'on'}).status_code, 302)
        lite, _ = make_tenant('Lite', features=['dashboard', 'products'])
        self.client.login(username='lite', password='pw')
        self.assertNotIn('barcode', self.client.get(reverse('product_new')).context['form'].fields)

    def test_normal_invoice_discount_flows_into_total_and_ledger(self):
        data = {'party': self.cust.pk, 'date': '2026-09-01', 'discount': '50', 'due_date': '', 'notes': '', 'paid_now': '', 'method': 'cash',
                'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
                'items-0-product': self.soap.pk, 'items-0-quantity': '10', 'items-0-unit': self.pcs.pk, 'items-0-rate': '40'}
        self.assertEqual(self.client.post(reverse('sale_new'), data).status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual((inv.discount, inv.total), (Decimal('50'), Decimal('350')))
        self.assertEqual(LedgerEntry.objects.get(invoice=inv).debit, Decimal('350'))
        self.assertContains(self.client.get(reverse('sale_detail', args=[inv.pk])), 'Discount')
        self.assertEqual(self.client.get(reverse('sale_pdf', args=[inv.pk])).content[:4], b'%PDF')
        data['paid_now'] = '400'                                                                      # more than the discounted total
        self.assertEqual(self.client.post(reverse('sale_new'), data).status_code, 200)
