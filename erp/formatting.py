from decimal import Decimal


def money(v):
    return f'{Decimal(v or 0):,.2f}'


def qty(v):
    return f'{Decimal(v or 0):,.3f}'.rstrip('0').rstrip('.')


def money_short(v):
    """Like money() but drops '.00' — used where width is tight."""
    text = money(v)
    return text[:-3] if text.endswith('.00') else text
