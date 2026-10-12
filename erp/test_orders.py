from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from tenants.models import Feature, Unit

from . import orders as order_logic
from . import stock as stock_logic
from .models import Invoice, Order, OrderItem, Party, Product, StockMove
from .tests import make_tenant


class OrderBase(TestCase):
    def setUp(self):
        self.t, self.user = make_tenant('Cement', units=['kg', 'bag'])
        self.client.login(username='cement', password='pw')
        self.kg = Unit.objects.get(symbol='kg')
        self.cust = Party.objects.create(tenant=self.t, name='Ali', kind='customer', phone='0300-1234567')
        self.vend = Party.objects.create(tenant=self.t, name='Mill', kind='vendor')
        self.cement = Product.objects.create(tenant=self.t, name='OPC', unit=self.kg, price=10)
        self.steel = Product.objects.create(tenant=self.t, name='Steel', unit=self.kg, price=20)

    def post_order(self, kind, party, lines=((None, '100', '10'),), **extra):
        data = {'party': party.pk, 'date': '2026-09-01', 'due_date': '', 'notes': '', 'terms': 'Net 15', **extra}
        data.update({'items-TOTAL_FORMS': len(lines), 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000})
        for i, (prod, q, r) in enumerate(lines):
            data.update({f'items-{i}-product': (prod or self.cement).pk, f'items-{i}-quantity': q, f'items-{i}-unit': self.kg.pk, f'items-{i}-rate': r})
        return self.client.post(reverse(f'{kind}_new'), data)

    def make(self, kind, party=None, lines=((None, '100', '10'),)):
        r = self.post_order(kind, party or (self.vend if kind == 'purchase_order' else self.cust), lines)
        assert r.status_code == 302, getattr(r, 'context', None) and (r.context['form'].errors, r.context['formset'].errors)
        return Order.objects.filter(kind=kind).order_by('id').last()


class OrderBasics(OrderBase):
    def test_numbering_codes_totals_and_default_status(self):
        q1, q2 = self.make('quotation'), self.make('quotation', lines=((None, '5', '10'), (self.steel, '2', '20')))
        so, po = self.make('sales_order'), self.make('purchase_order')
        self.assertEqual((q1.code, q2.code, so.code, po.code), ('QUO-0001', 'QUO-0002', 'SO-0001', 'PO-0001'))
        self.assertEqual((q1.total, q2.total), (Decimal('1000'), Decimal('90')))
        self.assertEqual((q1.status, so.status, po.status), ('draft', 'open', 'open'))
        self.assertEqual(self.t.default_terms, '')
        self.assertEqual(q1.terms, 'Net 15')

    def test_orders_never_touch_ledger_or_stock(self):
        from .models import LedgerEntry
        self.make('sales_order')
        self.make('purchase_order')
        self.make('quotation')
        self.assertFalse(StockMove.objects.exists())
        self.assertFalse(LedgerEntry.objects.filter(kind='invoice').exists())

    def test_party_kind_is_enforced(self):
        r = self.post_order('purchase_order', self.cust)           # a customer cannot be a vendor on a PO
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)

    def test_edit_replaces_lines_and_recalculates(self):
        so = self.make('sales_order')
        item = so.items.get()
        data = {'party': self.cust.pk, 'date': '2026-09-02', 'due_date': '2026-09-20', 'notes': '', 'terms': '',
                'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
                'items-0-id': item.pk, 'items-0-order': so.pk, 'items-0-product': self.cement.pk, 'items-0-quantity': '50', 'items-0-unit': self.kg.pk, 'items-0-rate': '12'}
        self.assertEqual(self.client.post(reverse('sales_order_edit', args=[so.pk]), data).status_code, 302)
        so.refresh_from_db()
        self.assertEqual((so.total, so.due_date), (Decimal('600'), date(2026, 9, 20)))

    def test_quotation_status_flow_and_expiry(self):
        q = self.make('quotation')
        for status in ('sent', 'accepted', 'rejected', 'draft'):
            self.client.post(reverse('quotation_status', args=[q.pk]), {'status': status})
            q.refresh_from_db()
            self.assertEqual(q.status, status)
        self.client.post(reverse('quotation_status', args=[q.pk]), {'status': 'converted'})     # not allowed by hand
        q.refresh_from_db()
        self.assertEqual(q.status, 'draft')
        q.due_date = date.today() - timedelta(days=1)
        q.save()
        self.assertEqual(q.display_status, 'Expired')
        q.status = 'accepted'
        q.save()
        self.assertEqual(q.display_status, 'Accepted')

    def test_cancel_and_reopen_order(self):
        so = self.make('sales_order')
        self.client.post(reverse('sales_order_status', args=[so.pk]), {'status': 'cancelled'})
        so.refresh_from_db()
        self.assertEqual(so.display_status, 'Cancelled')
        self.client.post(reverse('sales_order_status', args=[so.pk]), {'status': 'open'})
        so.refresh_from_db()
        self.assertEqual(so.display_status, 'Open')

    def test_delete(self):
        so = self.make('sales_order')
        self.client.post(reverse('sales_order_delete', args=[so.pk]))
        self.assertFalse(Order.objects.exists())


class OrderConversions(OrderBase):
    def invoice_from(self, kind, order, qty_by_item):
        """Post the pre-filled invoice form of an order with chosen quantities."""
        url = reverse(f'{kind}_new') + f'?from_order={order.pk}'
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        formset = page.context['formset']
        data = {'party': order.party.pk, 'date': '2026-09-05', 'due_date': '', 'notes': '', 'paid_now': '', 'method': 'cash',
                'items-TOTAL_FORMS': len(qty_by_item), 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 0, 'items-MAX_NUM_FORMS': 1000}
        for i, (item, qty) in enumerate(qty_by_item):
            data.update({f'items-{i}-product': item.product_id, f'items-{i}-quantity': qty, f'items-{i}-unit': item.unit_id,
                         f'items-{i}-rate': item.rate, f'items-{i}-order_item': item.pk})
        return self.client.post(url, data)

    def test_quotation_to_sales_order_copies_lines(self):
        q = self.make('quotation', lines=((None, '100', '10'), (self.steel, '5', '20')))
        r = self.client.post(reverse('quotation_convert', args=[q.pk]))
        so = Order.objects.get(kind='sales_order')
        self.assertRedirects(r, reverse('sales_order_detail', args=[so.pk]))
        q.refresh_from_db()
        self.assertEqual((q.status, so.source_id, so.total, so.items.count()), ('converted', q.pk, Decimal('1100'), 2))
        self.assertEqual(self.client.post(reverse('quotation_convert', args=[q.pk])).status_code, 404)   # only once

    def test_rejected_quote_cannot_convert(self):
        q = self.make('quotation')
        self.client.post(reverse('quotation_status', args=[q.pk]), {'status': 'rejected'})
        self.assertEqual(self.client.post(reverse('quotation_convert', args=[q.pk])).status_code, 404)

    def test_sales_order_partial_then_full_invoicing(self):
        so = self.make('sales_order', lines=((None, '100', '10'), (self.steel, '10', '20')))
        a, b = list(so.items.order_by('id'))
        page = self.client.get(reverse('sale_new') + f'?from_order={so.pk}')
        rows = [f.initial for f in page.context['formset'].forms]
        self.assertEqual([(r['product'], r['quantity']) for r in rows], [(self.cement.pk, '100'), (self.steel.pk, '10')])
        self.assertEqual(page.context['form'].initial['party'], self.cust.pk)

        self.assertEqual(self.invoice_from('sale', so, [(a, '40')]).status_code, 302)
        so = Order.objects.get(pk=so.pk)
        a.refresh_from_db()
        self.assertEqual((a.invoiced_qty, a.remaining, so.fulfilment), (Decimal('40'), Decimal('60'), 'Partial'))
        inv = Invoice.objects.get()
        self.assertEqual(inv.order_id, so.pk)

        self.assertEqual(self.invoice_from('sale', so, [(a, '60'), (b, '10')]).status_code, 302)
        so = Order.objects.get(pk=so.pk)
        self.assertEqual(so.fulfilment, 'Completed')
        self.assertEqual(so.invoices.count(), 2)
        # nothing left -> no "create invoice" button and an empty prefill
        self.assertNotContains(self.client.get(reverse('sales_order_detail', args=[so.pk])), 'Create invoice')
        self.assertEqual(order_logic.prefill_rows(so), [])

    def test_deleting_the_invoice_reopens_the_order(self):
        so = self.make('sales_order')
        item = so.items.get()
        self.invoice_from('sale', so, [(item, '100')])
        self.assertEqual(Order.objects.get(pk=so.pk).fulfilment, 'Completed')
        Invoice.objects.get().delete()
        self.assertEqual(Order.objects.get(pk=so.pk).fulfilment, 'Open')
        item.refresh_from_db()
        self.assertEqual(item.invoiced_qty, 0)

    def test_quotation_can_be_invoiced_directly_and_becomes_converted(self):
        q = self.make('quotation')
        self.invoice_from('sale', q, [(q.items.get(), '100')])
        q.refresh_from_db()
        self.assertEqual(q.status, 'converted')

    def test_purchase_order_receives_stock_through_the_bill(self):
        po = self.make('purchase_order')
        item = po.items.get()
        self.assertEqual(stock_logic.on_hand(self.cement), 0)
        self.invoice_from('purchase', po, [(item, '60')])
        self.assertEqual(stock_logic.on_hand(self.cement), 60)                      # stock arrives with the bill
        po = Order.objects.get(pk=po.pk)
        self.assertEqual((po.fulfilment, po.invoices.get().kind), ('Partial', 'purchase'))
        self.invoice_from('purchase', po, [(item, '40')])
        self.assertEqual((Order.objects.get(pk=po.pk).fulfilment, stock_logic.on_hand(self.cement)), ('Completed', 100))

    def test_wrong_kind_source_is_ignored(self):
        po = self.make('purchase_order')
        page = self.client.get(reverse('sale_new') + f'?from_order={po.pk}')            # a PO cannot seed a sale
        self.assertEqual(len([f for f in page.context['formset'].forms if f.initial.get('product')]), 0)

    def test_cancelled_order_offers_no_invoice(self):
        so = self.make('sales_order')
        self.client.post(reverse('sales_order_status', args=[so.pk]), {'status': 'cancelled'})
        self.assertNotContains(self.client.get(reverse('sales_order_detail', args=[so.pk])), 'Create invoice')


class OrderPages(OrderBase):
    def test_pages_pdfs_and_reports_render(self):
        q = self.make('quotation')
        so = self.make('sales_order')
        po = self.make('purchase_order')
        for kind, obj in (('quotation', q), ('sales_order', so), ('purchase_order', po)):
            for name in ('list', 'new'):
                self.assertEqual(self.client.get(reverse(f'{kind}_{name}')).status_code, 200, f'{kind}_{name}')
            for name in ('detail', 'edit', 'delete'):
                self.assertEqual(self.client.get(reverse(f'{kind}_{name}', args=[obj.pk])).status_code, 200, f'{kind}_{name}')
            pdf = self.client.get(reverse(f'{kind}_pdf', args=[obj.pk]))
            self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b'%PDF'))
        self.assertEqual(self.client.get(reverse('quotation_list') + '?status=draft&q=ali&range=all').status_code, 200)
        self.assertEqual(self.client.get(reverse('sales_order_list') + '?status=open').status_code, 200)
        self.assertContains(self.client.get(reverse('sales_order_detail', args=[so.pk])), 'In stock')   # stock check column
        for slug in ('quotations', 'sales-orders', 'pending-sales-orders', 'purchase-orders', 'pending-purchase-orders'):
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all').status_code, 200, slug)
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=pdf').content[:4], b'%PDF', slug)
            self.assertEqual(self.client.get(reverse('report_view', args=[slug]) + '?range=all&format=csv')['Content-Type'], 'text/csv', slug)

    def test_sales_order_flags_short_stock(self):
        so = self.make('sales_order')
        html = self.client.get(reverse('sales_order_detail', args=[so.pk])).content.decode()
        self.assertIn('Not enough stock', html)
        stock_logic.add_move(self.t, self.cement, stock_logic.default_warehouse(self.t), Decimal('500'), StockMove.OPENING)
        self.assertNotIn('Not enough stock', self.client.get(reverse('sales_order_detail', args=[so.pk])).content.decode())

    def test_modules_are_switchable_and_depend_on_each_other(self):
        lite, _ = make_tenant('Lite', features=['dashboard', 'quotations'])
        self.client.login(username='lite', password='pw')
        self.assertEqual(self.client.get(reverse('quotation_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('sales_order_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('purchase_order_list')).status_code, 403)
        lite.features.add(Feature.objects.get(code='sales_orders'))      # pulls in sales + parties + products
        self.client.get('/')
        self.assertEqual(self.client.get(reverse('sales_order_list')).status_code, 200)
        self.assertIn('sales', Order.objects.none().model and __import__('tenants.models', fromlist=['Tenant']).Tenant.objects.get(name='Lite').enabled_features())

    def test_orders_are_tenant_isolated(self):
        so = self.make('sales_order')
        make_tenant('Rival')
        self.client.login(username='rival', password='pw')
        self.assertEqual(self.client.get(reverse('sales_order_detail', args=[so.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('sales_order_list')), reverse('sales_order_detail', args=[so.pk]))
