import io
import json
import sys

from PIL import Image, ImageDraw

from skin_type_classifier import classify_skin_type_from_image, validate_classification


def make_test_image(color, label):
    img = Image.new("RGB", (400, 300), color=color)
    draw = ImageDraw.Draw(img)
    for i in range(0, 400, 20):
        shade = tuple(max(0, c - 10) for c in color)
        draw.line([(i, 0), (i, 300)], fill=shade, width=2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    print(f"[{label}] generated test image, {buf.tell()} bytes")
    return buf.getvalue()


cases = [
    ("light skin tone (~Fitzpatrick I-II)", (245, 220, 200)),
    ("medium skin tone (~Fitzpatrick III-IV)", (190, 150, 120)),
    ("dark skin tone (~Fitzpatrick V-VI)", (90, 60, 45)),
    ("NOT skin -- solid blue (should be rejected)", (40, 80, 200)),
]

for label, color in cases:
    print("=" * 70)
    print(label)
    image_bytes = make_test_image(color, label)
    try:
        raw = classify_skin_type_from_image(image_bytes)
        print("raw model output:", json.dumps(raw, ensure_ascii=False))
        result = validate_classification(raw)
        print("validated result:", json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print("ERROR calling classifier:", repr(e))
