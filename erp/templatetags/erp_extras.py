import re
from urllib.parse import quote

from django import template
from django.conf import settings

from .. import formatting

register = template.Library()
register.filter('money', formatting.money)
register.filter('qty', formatting.qty)
register.filter('money_short', formatting.money_short)


@register.filter
def abs_money(v):
    return formatting.money(abs(v or 0))


def wa_number(phone):
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = settings.DEFAULT_COUNTRY_CODE + digits[1:]
    return digits


@register.simple_tag
def wa_url(phone, text):
    """WhatsApp click-to-chat link; opens the chat with `text` prefilled. Empty if no phone."""
    number = wa_number(phone)
    return f'https://wa.me/{number}?text={quote(text)}' if number else ''
