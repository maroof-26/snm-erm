from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .formatting import money, qty

_base = getSampleStyleSheet()['Normal']
CELL = ParagraphStyle('cell', parent=_base, fontSize=9.5, leading=12.5)
CELL_R = ParagraphStyle('cell_r', parent=CELL, alignment=2)
CELL_B = ParagraphStyle('cell_b', parent=CELL, fontName='Helvetica-Bold')
CELL_BR = ParagraphStyle('cell_br', parent=CELL_B, alignment=2)
H1 = ParagraphStyle('h1', parent=_base, fontName='Helvetica-Bold', fontSize=15, leading=18)
H2 = ParagraphStyle('h2', parent=_base, fontName='Helvetica-Bold', fontSize=12, leading=15, spaceBefore=4)
MUTED = ParagraphStyle('muted', parent=_base, fontSize=8.5, textColor=colors.HexColor('#555555'))
GREY = colors.HexColor('#e9ecef')
LINE = colors.HexColor('#c8ccd0')


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#777777'))
    w = doc.pagesize[0]
    canvas.drawString(doc.leftMargin, 10 * mm, f'Generated {timezone.localtime():%Y-%m-%d %H:%M}')
    canvas.drawRightString(w - doc.rightMargin, 10 * mm, f'Page {doc.page}')
    canvas.restoreState()


def _build(story, pagesize):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _p(text, style):
    return Paragraph(escape(str(text)), style)


def _table(header, rows, aligns, width, totals=None, weights=None):
    """Grid table; 'r' columns are right-aligned. Text columns share width by weight."""
    n = len(header)
    right_w = 32 * mm
    n_right = aligns.count('r')
    text_idx = [i for i, a in enumerate(aligns) if a != 'r']
    weights = weights or [1] * n
    left = width - right_w * n_right
    total_w = sum(weights[i] for i in text_idx) or 1
    col_w = [right_w if a == 'r' else left * weights[i] / total_w for i, a in enumerate(aligns)]
    data = [[_p(h, CELL_BR if a == 'r' else CELL_B) for h, a in zip(header, aligns)]]
    data += [[_p(c, CELL_R if a == 'r' else CELL) for c, a in zip(r, aligns)] for r in rows]
    if totals:
        data.append([_p(c, CELL_BR if a == 'r' else CELL_B) for c, a in zip(totals, aligns)])
    style = [('BACKGROUND', (0, 0), (-1, 0), GREY), ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
             ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5)]
    if totals:
        style += [('BACKGROUND', (0, -1), (-1, -1), GREY)]
    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle(style))
    return t


def _header(tenant, title, subtitle):
    parts = [_p(tenant.name, H1)]
    contact = ' | '.join(x for x in (tenant.phone, tenant.address.replace('\n', ', ')) if x)
    if contact:
        parts.append(_p(contact, MUTED))
    parts += [Spacer(1, 4 * mm), _p(title, H2)]
    if subtitle:
        parts.append(_p(subtitle, MUTED))
    parts.append(Spacer(1, 4 * mm))
    return parts


def report_pdf(tenant, report):
    size = landscape(A4) if report.landscape else A4
    width = size[0] - 28 * mm
    weights = report.weights or [1] * len(report.columns)
    story = _header(tenant, report.title, report.subtitle)
    story.append(_table(report.columns, report.rows, report.aligns, width, report.totals, weights))
    return _build(story, size)


def invoice_pdf(invoice):
    tenant = invoice.tenant
    is_sale = invoice.kind == invoice.SALE
    title = f"{'Sale invoice' if is_sale else 'Purchase bill'} {invoice.code}"
    story = _header(tenant, title, None)
    meta = [f'{"Customer" if is_sale else "Vendor"}: {invoice.party.name}', f'Date: {invoice.date:%d %b %Y}']
    if invoice.due_date:
        meta.append(f'Due: {invoice.due_date:%d %b %Y}')
    if invoice.party.phone:
        meta.insert(1, f'Phone: {invoice.party.phone}')
    story += [_p(' | '.join(meta), CELL), Spacer(1, 4 * mm)]
    rows = [[i + 1, it.product.name, qty(it.quantity), it.unit.symbol, money(it.rate), money(it.amount)]
            for i, it in enumerate(invoice.items.select_related('product', 'unit'))]
    story.append(_table(['#', 'Item', 'Qty', 'Unit', 'Rate', 'Amount'], rows, 'llrlrr', A4[0] - 28 * mm,
                        ['', 'Total', '', '', '', f'{tenant.currency_symbol} {money(invoice.total)}'], [0.4, 4, 1, 1, 1, 1]))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(f'Paid: {tenant.currency_symbol} {money(invoice.paid)}    Balance: {tenant.currency_symbol} {money(invoice.balance)}', CELL_B))
    if invoice.notes:
        story += [Spacer(1, 3 * mm), _p(f'Notes: {invoice.notes}', CELL)]
    return _build(story, A4)
