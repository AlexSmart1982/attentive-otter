"""Editable comparison mark and its pupil positions."""
from __future__ import annotations

from io import BytesIO

from PIL import Image
from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer


SIZE = 480


def logo_svg(gaze: float = 1.0, *, video: bool = False, gaze_y=None) -> str:
    pupil_dx, pupil_dy = (gaze * 5.5, 7.0) if video else (gaze * 7.5, 8.0)
    if gaze_y is not None:
        pupil_dy = gaze_y
    pupil_radius = 6.0 if video else 7.5
    if video:
        # Leave enough room inside both right-pointing eyes for the full gaze arc.
        eye_shapes = '''<path d="M148 216 Q148 211 153 214 L190 236 Q199 242 190 248 L153 270 Q148 273 148 267 Z"/>
    <path transform="translate(180 0)" d="M148 216 Q148 211 153 214 L190 236 Q199 242 190 248 L153 270 Q148 273 148 267 Z"/>'''
    else:
        eye_shapes = '<circle cx="166" cy="242" r="21"/><circle cx="346" cy="242" r="21"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <title>Стык — два файла, взгляд вниз</title>
  <rect width="512" height="512" rx="106" fill="#191A1B"/>
  <path fill="#F5F3EF" d="M84 100 H204 Q252 100 252 148 V386 Q252 434 204 434 H90 Q42 434 42 386 V179 Q42 168 50 160 L100 110 Q110 100 124 100 Z"/>
  <path fill="#FFB835" d="M308 100 H388 Q402 100 412 110 L462 160 Q470 168 470 179 V386 Q470 434 422 434 H308 Q260 434 260 386 V148 Q260 100 308 100 Z"/>
  <path fill="#191A1B" d="M68 174 L119 123 Q126 116 126 127 V166 Q126 180 112 180 H73 Q63 180 68 174 Z"/>
  <path fill="#191A1B" d="M444 174 L393 123 Q386 116 386 127 V166 Q386 180 400 180 H439 Q449 180 444 174 Z"/>
  <g fill="#191A1B">
    {eye_shapes}
    <path d="M76 353 L128 300 Q134 294 140 300 L177 337 Q182 343 188 337 L200 325 Q206 319 212 325 L242 355 Q249 367 236 367 H82 Q69 367 76 353 Z"/>
    <path transform="translate(512 0) scale(-1 1)" d="M76 353 L128 300 Q134 294 140 300 L177 337 Q182 343 188 337 L200 325 Q206 319 212 325 L242 355 Q249 367 236 367 H82 Q69 367 76 353 Z"/>
    <path d="M249 237 Q256 230 263 237 L301 275 Q308 282 301 289 L263 327 Q256 334 249 327 L211 289 Q204 282 211 275 Z"/>
  </g>
  <g fill="#FDFCF9">
    <circle cx="{166 + pupil_dx:.3f}" cy="{242 + pupil_dy:.3f}" r="{pupil_radius}"/>
    <circle cx="{346 + pupil_dx:.3f}" cy="{242 + pupil_dy:.3f}" r="{pupil_radius}"/>
    <rect x="235" y="266" width="42" height="10" rx="3.5"/>
    <rect x="235" y="289" width="42" height="10" rx="3.5"/>
  </g>
</svg>'''


def render(svg: str) -> Image.Image:
    canvas = QImage(SIZE * 2, SIZE * 2, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    QSvgRenderer(QByteArray(svg.encode('utf-8'))).render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    canvas.save(buffer, 'PNG')
    return Image.open(BytesIO(bytes(buffer.data()))).convert('RGBA').resize(
        (SIZE, SIZE), Image.Resampling.LANCZOS
    )

