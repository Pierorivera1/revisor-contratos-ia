"""Convierte los documentos de documentos/fuente/*.md a PDF en documentos/pdf/.

Uso: .venv/bin/python scripts/md_a_pdf.py
Requiere: pip install markdown, y Chromium instalado (se usa en modo headless).
"""

import pathlib
import subprocess
import tempfile

import markdown

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "documentos" / "fuente"
SALIDA = RAIZ / "documentos" / "pdf"

CSS = """
@page { size: A4; margin: 2.2cm 2.3cm; }
body { font-family: 'Times New Roman', 'Liberation Serif', serif; font-size: 11.5pt;
       line-height: 1.45; color: #111; text-align: justify; }
h1 { font-size: 15pt; text-align: center; margin: 0 0 4pt; }
h2 { font-size: 12pt; margin: 16pt 0 6pt; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; }
blockquote { font-size: 9pt; color: #555; border: 1px solid #bbb; margin: 8pt 0;
             padding: 4pt 8pt; text-align: center; }
blockquote p { margin: 0; }
table { border-collapse: collapse; margin: 8pt auto; }
th, td { border: 1px solid #999; padding: 3pt 8pt; font-size: 10.5pt; text-align: left; }
hr { border: 0; border-top: 1px solid #999; margin: 12pt 0; }
p { margin: 5pt 0; }
"""


def convertir(md_path: pathlib.Path) -> pathlib.Path:
    html_body = markdown.markdown(md_path.read_text(encoding="utf-8"), extensions=["tables"])
    html = f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>{md_path.stem}</title><style>{CSS}</style></head><body>{html_body}</body></html>"
    pdf_path = SALIDA / (md_path.stem + ".pdf")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html)
    subprocess.run(
        ["chromium", "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", f"file://{tmp.name}"],
        check=True, capture_output=True,
    )
    pathlib.Path(tmp.name).unlink()
    return pdf_path


if __name__ == "__main__":
    SALIDA.mkdir(parents=True, exist_ok=True)
    for md in sorted(FUENTE.glob("*.md")):
        print("PDF:", convertir(md).relative_to(RAIZ))
