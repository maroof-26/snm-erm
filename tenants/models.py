from django.conf import settings
from django.db import models

from .features import REQUIRES


class Unit(models.Model):
    name = models.CharField(max_length=50, unique=True)
    symbol = models.CharField(max_length=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.symbol})'


class Feature(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    features = models.ManyToManyField(
        Feature, blank=True,
        help_text='Modules this client can use. Dependencies (e.g. Sales needs Customers & Products) are enabled automatically.')
    units = models.ManyToManyField(
        Unit, blank=True,
        help_text='Units of measure offered to this client (kg, pcs, liter...). Leave empty to offer all units.')
    default_unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    allow_negative_stock = models.BooleanField(
        default=True, help_text='If off, a sale that would take stock below zero is blocked. If on, it is allowed with a warning.')
    currency_symbol = models.CharField(max_length=8, default='Rs')
    sale_prefix = models.CharField(max_length=8, default='INV')
    purchase_prefix = models.CharField(max_length=8, default='BILL')
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def enabled_features(self):
        if not hasattr(self, '_features'):
            codes = set(self.features.values_list('code', flat=True))
            for code in list(codes):
                codes |= REQUIRES.get(code, set())
            self._features = codes
        return self._features

    def available_units(self):
        chosen = self.units.filter(is_active=True)
        return chosen if chosen.exists() else Unit.objects.filter(is_active=True)


class Membership(models.Model):
    """Links a login to exactly one client (tenant)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='membership')
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='memberships')
    is_owner = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user} @ {self.tenant}'
