def tenant(request):
    t = getattr(request, 'tenant', None)
    feats = t.enabled_features() if t else set()
    low = 0
    if t and 'stock' in feats and request.user.is_authenticated:
        from erp import stock
        low = stock.low_stock_count(t)
    return {'tenant': t, 'features': feats, 'currency': t.currency_symbol if t else '', 'low_stock_count': low}
