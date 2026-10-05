"""Feature catalogue. Add a row here and it appears in admin after `migrate`."""

FEATURES = [
    ('dashboard', 'Dashboard', 'Sales, purchases, receivable and payable overview.'),
    ('parties', 'Customers & Vendors', 'Manage customers and vendors.'),
    ('products', 'Products', 'Product catalogue with default unit and price.'),
    ('sales', 'Sales', 'Sale invoices and sale tracking.'),
    ('purchases', 'Purchases', 'Purchase bills from vendors.'),
    ('payments', 'Payments', 'Record money received and paid.'),
    ('stock', 'Stock', 'Stock levels, stock moves, adjustments, transfers and low-stock alerts.'),
    ('ledger', 'Ledger', 'Party ledgers, balances and manual entries.'),
    ('reports', 'Reports', 'PDF / CSV reports with date-range filters.'),
]

# Enabling a feature silently enables what it depends on.
REQUIRES = {
    'sales': {'parties', 'products'},
    'purchases': {'parties', 'products'},
    'stock': {'products'},
    'payments': {'parties'},
    'ledger': {'parties'},
    'reports': {'parties'},
}

DEFAULT_UNITS = [
    ('Kilogram', 'kg'), ('Gram', 'g'), ('Ton', 't'), ('Bag', 'bag'),
    ('Piece', 'pcs'), ('Dozen', 'doz'), ('Box', 'box'), ('Carton', 'ctn'),
    ('Packet', 'pkt'), ('Liter', 'L'), ('Milliliter', 'ml'), ('Pound', 'lb'),
    ('Meter', 'm'), ('Foot', 'ft'),
]
