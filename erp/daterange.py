from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from django.utils import timezone
from django.utils.dateparse import parse_date

PRESETS = [('today', 'Today'), ('week', 'This week'), ('month', 'This month'), ('last_month', 'Last month'),
           ('year', 'This year'), ('all', 'All time')]


@dataclass
class DateRange:
    start: date | None
    end: date | None
    key: str
    label: str

    def apply(self, qs, field='date'):
        if self.start:
            qs = qs.filter(**{f'{field}__gte': self.start})
        if self.end:
            qs = qs.filter(**{f'{field}__lte': self.end})
        return qs

    @property
    def note(self):
        """Short human text for under the page title."""
        def d(x):
            return f'{x.day} {x:%b}'
        if self.key == 'all':
            return 'All time'
        if self.key == 'today':
            return 'Today'
        span = f'{d(self.start)} – {d(self.end)}' if self.start and self.end else self.label
        return span if self.key == 'custom' else f'{dict(PRESETS)[self.key]} · {span}'

    @property
    def is_long(self):
        return not self.start or not self.end or (self.end - self.start).days > 62


def get_range(request, default='month'):
    today = timezone.localdate()
    start = parse_date(request.GET.get('from') or '')
    end = parse_date(request.GET.get('to') or '')
    if start or end:
        label = f'{start or "beginning"} to {end or "today"}'
        return DateRange(start, end, 'custom', label)
    key = request.GET.get('range')
    if key not in dict(PRESETS):
        key = default
    first = today.replace(day=1)
    if key == 'today':
        start, end = today, today
    elif key == 'week':
        start, end = today - timedelta(days=today.weekday()), today
    elif key == 'month':
        start, end = first, today
    elif key == 'last_month':
        end = first - timedelta(days=1)
        start = end.replace(day=1)
    elif key == 'year':
        start, end = today.replace(month=1, day=1), today
    label = dict(PRESETS)[key] if key == 'all' else f'{dict(PRESETS)[key]} ({start} to {end})'
    return DateRange(start, end, key, label)


def range_context(request, rng):
    """Template context for _range_filter.html; preserves the other GET params."""
    keep = [(k, v) for k, v in request.GET.items() if k not in ('range', 'from', 'to', 'page') and v]
    return {'rng': rng, 'presets': PRESETS, 'keep': keep, 'keep_qs': urlencode(keep)}
