"""Rebuild the editable logo and theme-specific GIFs: python -B artwork/build_assets.py."""
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from artwork.face import logo_svg, render as render_face
from artwork.scanner import render as render_scanner, COUNT
from artwork.scan_scene import scene_svg, WIDTH, HEIGHT
from theme import PALETTES


def recolor(svg, name):
    p = PALETTES[name]
    mapping = {'#191A1B': p['bg'], '#FFB835': p['accent'], '#F5F3EF': '#f5f3ef'}
    if name == 'light':
        mapping.update({'#191A1B': p['text'], '#F5F3EF': '#e3e8eb',
            '#232628': '#e6eaed', '#1C1F20': '#f1f4f5', '#272B2D': '#e6eaed',
            '#343638': '#e2e6e9', '#64666A': '#b5bec5', '#3A3D3F': '#d7dfe3',
            '#795C2C': '#bad0c3', '#D8CBB2': '#c7d2ca', '#FFE2A5': '#e0eee5',
            '#76541C': '#d3e6db', '#6B5734': '#acc4b5', '#303436': '#dce2e5',
            '#0C0E0F': '#8c979d', '#767B7E': '#88959c', '#111314': '#929da3'})
    else:
        mapping.update({'#795C2C': '#715538', '#FFE2A5': '#f7d6ae', '#76541C': '#77522c'})
    # Replace original color tokens together; substitutions must not cascade.
    import re
    svg = re.sub(r'#[0-9A-Fa-f]{6}', lambda match: mapping.get(match[0], match[0]), svg)
    # The scene background / logo tile blend with the surrounding application.
    svg = svg.replace(f'<rect width="512" height="512" rx="106" fill="{p["text"]}"/>',
                      f'<rect width="512" height="512" rx="106" fill="{p["bg"]}"/>')
    svg = svg.replace(f'<rect width="960" height="480" rx="30" fill="{p["text"]}"/>',
                      f'<rect width="960" height="480" rx="30" fill="{p["bg"]}"/>')
    svg = svg.replace(f'<rect width="960" height="220" fill="{p["text"]}"/>',
                      f'<rect width="960" height="220" fill="{p["bg"]}"/>')
    return svg


def save_gif(frames, path, colors):
    # Shared palette prevents frame-to-frame color flicker. Full source frames,
    # incremental GIF rectangles, no cached bitmap animation required at runtime.
    width, height = frames[0].size
    samples = Image.new('RGB', (width, height*4))
    for j, index in enumerate((0, len(frames)//4, len(frames)//2, len(frames)*3//4)):
        samples.paste(frames[index], (0, height*j))
    palette = samples.quantize(colors=160, method=Image.Quantize.MEDIANCUT)
    entries = palette.getpalette()
    entries.extend([0] * (768-len(entries)))
    fixed = [ImageColor.getrgb(colors[key]) for key in ('bg', 'accent')]
    for index, color in enumerate(fixed, 254):
        entries[index*3:index*3+3] = color
    palette.putpalette(entries)
    indexed = []
    for frame in frames:
        quantized = frame.quantize(palette=palette, dither=Image.Dither.NONE)
        for index, color in enumerate(fixed, 254):
            difference = ImageChops.difference(frame, Image.new('RGB', frame.size, color))
            r, g, b = difference.split()
            difference = ImageChops.lighter(r, ImageChops.lighter(g, b))
            exact = difference.point(lambda value: 255 if value == 0 else 0)
            quantized.paste(index, mask=exact)
        indexed.append(quantized)
    indexed[0].save(path, save_all=True, append_images=indexed[1:], duration=50,
                    loop=0, disposal=1, optimize=True)


def flatten(frame, color, size=None):
    if size:
        frame = frame.resize(size, Image.Resampling.LANCZOS)
    background = Image.new('RGB', frame.size, color)
    background.paste(frame, mask=frame.getchannel('A'))
    return background


def main():
    target = ROOT/'assets'
    # The application mark keeps its amber identity on both Windows themes.
    svg = recolor(logo_svg(0), 'dark')
    (target/'application.svg').write_text(svg, encoding='utf-8')
    icon = render_face(svg)
    icon.save(target/'application.ico', sizes=[(n, n) for n in (16,24,32,48,64,128,256)])
    for name, p in PALETTES.items():
        frames = [flatten(render_scanner(recolor(scene_svg(i*.05), name), WIDTH, HEIGHT), p['bg'])
                  for i in range(COUNT)]
        path = target/f'scan-scene-{name}.gif'
        save_gif(frames, path, p)
        with Image.open(path) as saved:
            if saved.convert('RGB').getpixel((0, 0)) != ImageColor.getrgb(p['bg']):
                raise RuntimeError('Animation background differs from the theme: '+path.name)
        print(f'{path.name}: {path.stat().st_size:,} bytes', flush=True)


if __name__ == '__main__':
    main()
