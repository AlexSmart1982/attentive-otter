"""One timeline for the file, scanner, destinations, and watching logo."""
from math import hypot, pi, sin
import xml.etree.ElementTree as ET

from artwork.face import logo_svg
from artwork.scanner import photo, feeding_photo, ease, mix, curve, clamp, INK, PAPER, AMBER

WIDTH, HEIGHT = 960, 220


def file_state(t):
    p, keep = t % 4.5, (t % 9) < 4.5
    appear = ease(p/.45)
    x, y, scale, alpha = 65.0, mix(147, 155, appear), mix(.65, 1, appear), appear
    if .45 <= p < 1.4:
        x, y = mix(65, 460, ease((p-.45)/.95)), 155
    elif 1.4 <= p < 2.25:
        x, y = 460, 155
    elif 2.25 <= p < 3.3:
        u = ease((p-2.25)/1.05)
        end_y = 81 if keep else 147
        x, y = curve((460,155), (640,155), (785,end_y), (880,end_y), u)
    elif p >= 3.3:
        x = 880
        y = mix(81, 129, ease((p-3.3)/.8)) if keep else mix(147, 210, ease((p-3.3)/.8))
        alpha = 1-ease((p-4)/.35)
    return x, y, scale, alpha, p, keep


def scene_svg(t):
    x, y, scale, alpha, p, keep = file_state(t)
    dx, dy = x-460, y-44
    distance = max(1, hypot(dx,dy))
    gaze_x, gaze_y = 10*dx/distance, 10*dy/distance
    # Return to the next appearing file during the quiet part of the cycle.
    reset = ease((p-4)/.5)
    next_distance = hypot(65-460,147-44)
    gaze_x = mix(gaze_x,10*(65-460)/next_distance,reset)
    gaze_y = mix(gaze_y,10*(147-44)/next_distance,reset)
    face = ET.fromstring(logo_svg(gaze_x/7.5, gaze_y=gaze_y))
    # Embed just the vector shapes, never a second independent movie or clock.
    content = ''.join(ET.tostring(child, encoding='unicode') for child in face
                      if not child.tag.endswith('title') and child.get('width') != '512')
    logo = f'<g transform="translate(404 -9) scale(.21875)">{content}</g>'
    current = photo(x,y,scale=.62*scale,opacity=alpha,color=PAPER if keep else AMBER)
    if not keep and p > 3.3:
        current = feeding_photo(x,y,alpha,slot_y=173,scale=.62)
    scan = clamp((p-1.4)/.85)
    scanning = 1.4 <= p < 2.25
    glow = sin(scan*pi) if scanning else 0
    beam = ''
    if scanning:
        bx = mix(437,483,scan)
        beam = f'<rect x="{bx-5:.2f}" y="119" width="10" height="72" rx="3" fill="{AMBER}" opacity=".12"/><rect x="{bx-1:.2f}" y="121" width="2" height="68" rx="1" fill="{AMBER}"/>'
    pieces = ''
    if not keep and p > 3.4:
        for i in range(7):
            u = clamp((p-3.4-i*.025)/.7)
            sx, sy = 861+i*6+sin(i*2.2)*u*6, 199+u*12
            pieces += f'<rect x="{sx:.2f}" y="{sy:.2f}" width="3" height="6" rx="1" fill="{AMBER}" opacity="{sin(u*pi)*.9:.3f}" transform="rotate({(i-3)*u*8:.2f} {sx:.2f} {sy:.2f})"/>'
    sparkles = ''
    if p < .6:
        u = clamp(p/.6)
        for sx,sy in ((36,139),(91,132),(37,174)):
            sparkles += f'<circle cx="{sx}" cy="{sy}" r="1.5" fill="{AMBER}" opacity="{sin(u*pi)*.6:.3f}"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="220" viewBox="0 0 960 220">
      <rect width="960" height="220" fill="{INK}"/>
      {logo}
      <g fill="none" stroke-linecap="round">
        <path d="M24 182 H480" stroke="#343638" stroke-width="10"/>
        <path d="M24 182 H480" stroke="#64666A" stroke-width="1.5" stroke-dasharray="2 17" stroke-dashoffset="{-t*19*12/9:.2f}"/>
        <path d="M493 155 C640 155 691 106 832 106" stroke="#3A3D3F" stroke-width="2"/>
        <path d="M493 155 C640 155 713 177 832 177" stroke="#3A3D3F" stroke-width="2"/>
      </g>
      <path d="M835 78 Q835 70 843 70 H858 L867 78 H921 Q930 78 930 88 V118 H835Z" fill="#795C2C"/>
      {photo(873,91,scale=.42,angle=-7,color='#D8CBB2')}
      {photo(889,91,scale=.42,angle=6)}
      <rect x="422" y="107" width="76" height="89" rx="13" fill="#232628"/>
      <rect x="433" y="117" width="54" height="72" rx="7" fill="#1C1F20"/>
      <rect x="432" y="97" width="56" height="8" rx="4" fill="#272B2D"/>
      <circle cx="448" cy="101" r="1.5" fill="{AMBER}"/>
      <circle cx="460" cy="101" r="1.5" fill="{AMBER}" opacity=".55"/>
      <circle cx="472" cy="101" r="1.5" fill="{AMBER}" opacity=".25"/>
      {sparkles}{current}{beam}
      <path d="M428 192 V121 Q428 112 437 112 H483 Q492 112 492 121 V192" fill="none" stroke="{AMBER}" stroke-opacity="{.48+glow*.5:.3f}" stroke-width="3" stroke-linecap="round"/>
      <rect x="418" y="192" width="20" height="6" rx="3" fill="#6B5734"/>
      <rect x="483" y="192" width="20" height="6" rx="3" fill="#6B5734"/>
      <path d="M830 94 Q830 87 838 87 H860 L867 94 H931 Q939 94 937 102 L933 120 Q932 128 924 128 H844 Q835 128 834 120Z" fill="{AMBER}"/>
      <path d="M843 103 H926" stroke="#FFE2A5" stroke-width="1.5" opacity=".42" stroke-linecap="round"/>
      <rect x="862" y="111" width="37" height="3" rx="1.5" fill="#76541C" opacity=".6"/>
      <rect x="831" y="170" width="107" height="31" rx="9" fill="#303436"/>
      <rect x="839" y="171" width="91" height="5" rx="2.5" fill="#0C0E0F"/>
      <path d="M849 182 854 187 859 182 864 187 869 182 874 187 879 182 884 187 889 182 894 187 899 182 904 187 909 182 914 187" fill="none" stroke="#767B7E" stroke-width="1.5" stroke-linejoin="round"/>
      <rect x="851" y="195" width="66" height="4" rx="2" fill="#111314"/>
      {pieces}
    </svg>'''
