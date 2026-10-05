from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import services, stock
from .models import Invoice, InvoiceItem, Party, Payment


@receiver(post_save, sender=Invoice)
def invoice_saved(sender, instance, **kwargs):
    services.sync_invoice(instance)
    stock.sync_invoice_stock(instance)


@receiver(post_save, sender=InvoiceItem)
def item_saved(sender, instance, **kwargs):
    stock.sync_invoice_stock(instance.invoice)


@receiver(post_delete, sender=InvoiceItem)
def item_deleted(sender, instance, **kwargs):
    if isinstance(kwargs.get('origin'), Invoice):   # the whole invoice is being deleted; its moves go with it
        return
    if Invoice.objects.filter(pk=instance.invoice_id).exists():
        stock.sync_invoice_stock(instance.invoice)


@receiver(post_save, sender=Payment)
def payment_saved(sender, instance, **kwargs):
    services.sync_payment(instance)


@receiver(post_save, sender=Party)
def party_saved(sender, instance, **kwargs):
    services.sync_opening(instance)
