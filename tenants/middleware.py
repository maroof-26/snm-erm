from django.core.exceptions import ObjectDoesNotExist


class TenantMiddleware:
    """Sets request.tenant from the logged-in user's membership (None if absent/inactive)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        user = request.user
        if user.is_authenticated:
            try:
                tenant = user.membership.tenant
            except ObjectDoesNotExist:
                tenant = None
            if tenant and tenant.is_active:
                request.tenant = tenant
        return self.get_response(request)
