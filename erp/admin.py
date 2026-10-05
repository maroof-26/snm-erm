from django.contrib import admin

from .models import Invoice, InvoiceItem, LedgerEntry, Party, Payment, Product, StockMove, Warehouse


class TenantAdmin(admin.ModelAdmin):
    list_filter = ['tenant']


class ItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Party)
class PartyAdmin(TenantAdmin):
    list_display = ['name', 'tenant', 'kind', 'phone', 'opening_balance', 'is_active']
    list_filter = ['tenant', 'kind', 'is_active']
    search_fields = ['name', 'phone']


@admin.register(Product)
class ProductAdmin(TenantAdmin):
    list_display = ['name', 'tenant', 'sku', 'unit', 'price', 'track_stock', 'reorder_level', 'is_active']
    search_fields = ['name', 'sku']


@admin.register(Invoice)
class InvoiceAdmin(TenantAdmin):
    list_display = ['__str__', 'tenant', 'party', 'date', 'total']
    list_filter = ['tenant', 'kind']
    inlines = [ItemInline]
    readonly_fields = ['total']


@admin.register(Payment)
class PaymentAdmin(TenantAdmin):
    list_display = ['date', 'tenant', 'party', 'direction', 'amount', 'method']
    list_filter = ['tenant', 'direction', 'method']


@admin.register(LedgerEntry)
class LedgerEntryAdmin(TenantAdmin):
    list_display = ['date', 'tenant', 'party', 'description', 'debit', 'credit', 'kind']
    list_filter = ['tenant', 'kind']


@admin.register(Warehouse)
class WarehouseAdmin(TenantAdmin):
    list_display = ['name', 'tenant', 'is_default', 'is_active']


@admin.register(StockMove)
class StockMoveAdmin(TenantAdmin):
    list_display = ['date', 'tenant', 'product', 'warehouse', 'quantity', 'kind', 'invoice']
    list_filter = ['tenant', 'kind', 'warehouse']
    search_fields = ['product__name', 'reason', 'note']
