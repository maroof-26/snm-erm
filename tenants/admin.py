from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import Feature, Membership, Tenant, Unit


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ['user']


class TenantAdminForm(forms.ModelForm):
    owner_username = forms.CharField(required=False, help_text='Creates a login for the client and links it to this tenant.')
    owner_email = forms.EmailField(required=False)
    owner_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = Tenant
        fields = '__all__'

    def clean(self):
        data = super().clean()
        username = data.get('owner_username')
        if username:
            if get_user_model().objects.filter(username=username).exists():
                self.add_error('owner_username', 'This username already exists.')
            if not data.get('owner_password'):
                self.add_error('owner_password', 'Required when creating an owner login.')
        return data


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    list_display = ['name', 'slug', 'is_active', 'feature_list', 'user_count', 'created_at']
    list_filter = ['is_active', 'features']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ['features', 'units']
    inlines = [MembershipInline]

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (None, {'fields': ['name', 'slug', 'is_active']}),
            ('Modules', {'fields': ['features']}),
            ('Units of measure', {'fields': ['units', 'default_unit']}),
            ('Stock', {'fields': ['allow_negative_stock']}),
            ('Business details', {'fields': ['currency_symbol', 'sale_prefix', 'purchase_prefix', 'phone', 'address']}),
        ]
        if obj is None:
            fieldsets.append(('Owner login (optional)', {'fields': ['owner_username', 'owner_email', 'owner_password']}))
        return fieldsets

    def get_changeform_initial_data(self, request):
        return {'features': list(Feature.objects.values_list('pk', flat=True))}

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        username = form.cleaned_data.get('owner_username')
        if not change and username:
            user = get_user_model().objects.create_user(
                username, form.cleaned_data.get('owner_email', ''), form.cleaned_data['owner_password'])
            Membership.objects.create(user=user, tenant=form.instance, is_owner=True)

    @admin.display(description='Modules')
    def feature_list(self, obj):
        return ', '.join(obj.features.values_list('code', flat=True))

    @admin.display(description='Users')
    def user_count(self, obj):
        return obj.memberships.count()


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ['name', 'symbol', 'is_active']
    list_editable = ['is_active']
    search_fields = ['name', 'symbol']


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'description']
    readonly_fields = ['code']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
