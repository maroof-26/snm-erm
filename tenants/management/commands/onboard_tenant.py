from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from tenants.models import Feature, Membership, Tenant, Unit


class Command(BaseCommand):
    help = 'Onboard a new client: tenant + owner login + modules + units.'

    def add_arguments(self, parser):
        parser.add_argument('name')
        parser.add_argument('--username', required=True)
        parser.add_argument('--password')
        parser.add_argument('--email', default='')
        parser.add_argument('--features', default='all', help='Comma-separated codes, or "all".')
        parser.add_argument('--units', default='', help='Comma-separated unit symbols; empty = all units.')
        parser.add_argument('--currency', default='Rs')

    def handle(self, *args, **o):
        User = get_user_model()
        if User.objects.filter(username=o['username']).exists():
            raise CommandError('Username already exists.')
        slug = slugify(o['name'])
        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError(f'Tenant "{slug}" already exists.')
        password = o['password'] or getpass('Owner password: ')
        tenant = Tenant.objects.create(name=o['name'], slug=slug, currency_symbol=o['currency'])
        features = Feature.objects.all() if o['features'] == 'all' else Feature.objects.filter(code__in=o['features'].split(','))
        tenant.features.set(features)
        if o['units']:
            tenant.units.set(Unit.objects.filter(symbol__in=o['units'].split(',')))
        user = User.objects.create_user(o['username'], o['email'], password)
        Membership.objects.create(user=user, tenant=tenant, is_owner=True)
        self.stdout.write(self.style.SUCCESS(f'Onboarded {tenant} ({", ".join(sorted(tenant.enabled_features()))})'))
