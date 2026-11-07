"""Shared vector shapes, timing helpers and offline SVG rendering."""
from __future__ import annotations

from io import BytesIO

from PIL import Image
from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer

WIDTH, HEIGHT = 960, 480
FRAME_MS, COUNT = 50, 180
INK, PAPER, AMBER = '#191A1B', '#F5F3EF', '#FFB835'


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


def mix(a: float, b: float, x: float) -> float:
    return a + (b - a) * x


def curve(a, b, c, d, u):
    return tuple((1-u)**3*a[i] + 3*(1-u)**2*u*b[i] + 3*(1-u)*u*u*c[i] + u**3*d[i] for i in (0, 1))


def photo(x, y, *, scale=1.0, opacity=1.0, angle=0.0, color=PAPER):
    return f'''<g opacity="{opacity:.3f}" transform="translate({x:.3f} {y:.3f}) rotate({angle:.3f}) scale({scale:.3f})">
      <path d="M-21-40 H13 L31-22 V30 Q31 40 21 40 H-21 Q-31 40-31 30 V-30 Q-31-40-21-40Z" fill="{color}"/>
      <path d="M13-36 V-25 Q13-22 17-22 H27Z" fill="{INK}" opacity=".78"/>
      <circle cx="-12" cy="-9" r="5" fill="{INK}"/>
      <path d="M-23 25 -7 4 3 16 12 7 24 25Z" fill="{INK}" stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>
    </g>'''


def feeding_photo(x, y, opacity, *, slot_y=332, scale=1.0):
    # Qt's SVG Tiny renderer does not honor clipPath: cut geometry at the slot.
    cutoff = (slot_y-y)/scale

    def polygon(points, fill):
        clipped = []
        previous = points[-1]
        for current in points:
            if (previous[1] <= cutoff) != (current[1] <= cutoff):
                u = (cutoff-previous[1])/(current[1]-previous[1])
                clipped.append((mix(previous[0], current[0], u), cutoff))
            if current[1] <= cutoff:
                clipped.append(current)
            previous = current
        if len(clipped) < 3:
            return ''
        coords = ' '.join(f'{px:.2f},{py:.2f}' for px, py in clipped)
        return f'<polygon points="{coords}" fill="{fill}"/>'

    body = polygon([(-31,-40),(13,-40),(31,-22),(31,40),(-31,40)], AMBER)
    fold = polygon([(13,-36),(13,-22),(27,-22)], INK)
    mountain = polygon([(-23,25),(-7,4),(3,16),(12,7),(24,25)], INK)
    eye = '<circle cx="-12" cy="-9" r="5" fill="#191A1B"/>' if cutoff >= -4 else ''
    return f'<g transform="translate({x:.2f} {y:.2f}) scale({scale})" opacity="{opacity:.3f}">{body}{fold}{eye}{mountain}</g>'


def render(svg: str, width=WIDTH, height=HEIGHT) -> Image.Image:
    image = QImage(width*2, height*2, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg.encode('utf-8'))).render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return Image.open(BytesIO(bytes(buffer.data()))).convert('RGBA').resize(
        (width, height), Image.Resampling.LANCZOS)

