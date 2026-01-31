import io

from PIL import Image

MAX_SIZE = 1200
JPEG_QUALITY = 80
MAX_INPUT_SIZE = 10 * 1024 * 1024


def process_image(image_bytes):
    if len(image_bytes) > MAX_INPUT_SIZE:
        print(f"❌ Image too large: {len(image_bytes)} bytes")
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        print(f"❌ Invalid image: {e}")
        return None

    if img.mode != "RGB":
        img = img.convert("RGB")

    if img.width > MAX_SIZE or img.height > MAX_SIZE:
        img.thumbnail((MAX_SIZE, MAX_SIZE), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    output.seek(0)
    return output.read()
