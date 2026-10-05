from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render

from .features import FEATURES

FEATURE_NAMES = {code: name for code, name, _ in FEATURES}


def check_access(request, feature=None):
    """Return an error response if the user can't use `feature`, else None."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    tenant = request.tenant
    if tenant is None:
        if request.user.is_superuser:
            return redirect('admin:index')
        return render(request, 'tenants/blocked.html', {'reason': 'no_tenant'}, status=403)
    if feature and feature not in tenant.enabled_features():
        return render(request, 'tenants/blocked.html',
                      {'reason': 'feature', 'feature_name': FEATURE_NAMES.get(feature, feature)}, status=403)
    return None


def tenant_view(feature=None):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            denied = check_access(request, feature)
            return denied or view(request, *args, **kwargs)
        return wrapper
    return decorator
