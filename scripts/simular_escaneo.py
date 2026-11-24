"""Crea una versión "escaneada" de un contrato: cada página pasa a ser una imagen (sin texto
seleccionable), en escala de grises, levemente torcida y con ruido, como una fotocopia.

Uso: .venv/bin/python scripts/simular_escaneo.py documentos/pdf/contrato_03_contact_center_dataplus.pdf
Salida: documentos/pdf/escaneados/<nombre>_escaneado.pdf
"""

import pathlib
import random
import sys

import pypdfium2
from PIL import Image, ImageFilter

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def simular(origen: pathlib.Path) -> pathlib.Path:
    random.seed(7)
    paginas = []
    for pagina in pypdfium2.PdfDocument(str(origen)):
        img = pagina.render(scale=150 / 72).to_pil().convert("L")  # 150 dpi
        img = img.rotate(random.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=False, fillcolor=255)
        ruido = Image.effect_noise(img.size, 18).point(lambda v: 255 if v > 120 else 235)
        img = Image.composite(img, ruido, img.point(lambda v: 255 if v < 200 else 0))
        img = img.filter(ImageFilter.GaussianBlur(0.4))
        paginas.append(img)
    salida = RAIZ / "documentos" / "pdf" / "escaneados" / f"{origen.stem}_escaneado.pdf"
    salida.parent.mkdir(parents=True, exist_ok=True)
    paginas[0].save(salida, save_all=True, append_images=paginas[1:], resolution=150)
    return salida


if __name__ == "__main__":
    print(simular(pathlib.Path(sys.argv[1])).relative_to(RAIZ))
