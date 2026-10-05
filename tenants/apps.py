from django.apps import AppConfig
from django.db.models.signals import post_migrate


def seed(sender, **kwargs):
    from .features import DEFAULT_UNITS, FEATURES
    from .models import Feature, Unit
    for code, name, description in FEATURES:
        Feature.objects.update_or_create(code=code, defaults={'name': name, 'description': description})
    for name, symbol in DEFAULT_UNITS:
        Unit.objects.get_or_create(name=name, defaults={'symbol': symbol})


class TenantsConfig(AppConfig):
    name = 'tenants'

    def ready(self):
        post_migrate.connect(seed, sender=self)
