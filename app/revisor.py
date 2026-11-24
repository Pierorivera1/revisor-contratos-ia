"""Lógica del revisor de contratos: leer PDF, analizar con Bedrock y verificar citas.

No depende de Streamlit, así que lo usan tanto la app (app.py) como la evaluación (evaluar.py).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from dataclasses import dataclass, field

import boto3
from botocore.config import Config
from pypdf import PdfReader

RAIZ = pathlib.Path(__file__).resolve().parent.parent
DIR_PDF = RAIZ / "documentos" / "pdf"
POLITICA_PDF = DIR_PDF / "politicas_contratacion_banco_andino.pdf"
NORMATIVA_PDF = DIR_PDF / "normativa_ley_29733_resumen.pdf"

# El modelo se puede cambiar sin tocar el código (Converse API trata igual a todos los modelos).
MODELO_ID = os.environ.get("MODELO_ID", "us.moonshotai.kimi-k3.1")
REGION = os.environ.get("AWS_REGION", "us-east-1")
# Las credenciales de `aws login` necesitan una región por defecto para renovarse.
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)

_cliente = None


def cliente_bedrock():
    global _cliente
    if _cliente is None:
        # El modelo razona antes de responder y puede tardar más de 60 s: se amplía la espera
        # y se desactivan los reintentos automáticos (cada reintento también se cobra).
        _cliente = boto3.client(
            "bedrock-runtime", region_name=REGION,
            config=Config(read_timeout=300, retries={"max_attempts": 1, "mode": "standard"}),
        )
    return _cliente


# ---------------------------------------------------------------- lectura de PDF

def leer_pdf(origen) -> str:
    """Extrae el texto de un PDF digital (ruta o archivo subido). Es local y gratis."""
    lector = PdfReader(origen)
    return "\n".join((pagina.extract_text() or "") for pagina in lector.pages)


PROMPT_TRANSCRIPCION = """Estas imágenes son las páginas escaneadas de un contrato, en orden.
Transcribe TODO el texto de forma literal, palabra por palabra, respetando títulos, numeración de
cláusulas y tablas (en formato simple). No resumas, no corrijas y no agregues comentarios.
Si una palabra no se lee, escribe [ilegible]. Responde solo con la transcripción."""


def leer_pdf_escaneado(datos_pdf: bytes, max_paginas: int = 12) -> str:
    """Lee un PDF escaneado (páginas que son imágenes, sin texto) con el mismo modelo de IA.

    Cada página se convierte en imagen en el computador y se envía al modelo, que la transcribe.
    En producción esto lo haría Amazon Textract; aquí se usa el modelo multimodal para no agregar
    otro servicio a la demo.
    """
    import io

    import pypdfium2

    documento = pypdfium2.PdfDocument(datos_pdf)
    contenido = []
    for i in range(min(len(documento), max_paginas)):
        imagen = documento[i].render(scale=1.6).to_pil().convert("L")  # ~115 dpi, escala de grises
        bufer = io.BytesIO()
        imagen.save(bufer, format="JPEG", quality=80)
        contenido.append({"image": {"format": "jpeg", "source": {"bytes": bufer.getvalue()}}})
    contenido.append({"text": "Transcribe el contrato completo."})
    texto, _, _, _ = _llamar_modelo(PROMPT_TRANSCRIPCION, [{"role": "user", "content": contenido}])
    return texto.strip()


def leer_contrato(datos_pdf: bytes) -> tuple[str, bool]:
    """Devuelve (texto, es_escaneado). Usa el texto del PDF si lo tiene; si no, lo transcribe la IA.

    Las transcripciones se guardan en resultados/ para no pagar dos veces por el mismo archivo.
    """
    import io

    texto = leer_pdf(io.BytesIO(datos_pdf))
    if not es_escaneado(texto):
        return texto, False
    guardado = transcripcion_guardada(datos_pdf)
    if guardado:
        return guardado, True
    texto = leer_pdf_escaneado(datos_pdf)
    ruta = _ruta_transcripcion(datos_pdf)
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")
    return texto, True


def es_escaneado(texto_extraido: str) -> bool:
    """Un PDF escaneado casi no tiene texto seleccionable."""
    return len(texto_extraido.strip()) < 200


def _ruta_transcripcion(datos_pdf: bytes) -> pathlib.Path:
    import hashlib

    return RAIZ / "resultados" / f"ocr_{hashlib.sha256(datos_pdf).hexdigest()[:16]}.txt"


def transcripcion_guardada(datos_pdf: bytes) -> str | None:
    ruta = _ruta_transcripcion(datos_pdf)
    return ruta.read_text(encoding="utf-8") if ruta.exists() else None


def normalizar(texto: str) -> str:
    """Quita saltos de línea y espacios repetidos, y unifica comillas, para comparar citas."""
    texto = texto.replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", texto).strip().lower()


# ---------------------------------------------------------------- prompts

PROMPT_SISTEMA = """Eres un asistente de la Gerencia de Asesoría Legal del Banco Andino del Perú S.A.
Tu trabajo es PRERREVISAR contratos con proveedores: comparas cada cláusula del contrato con la
Política Corporativa de Contratación con Proveedores del Banco y con la normativa peruana de
protección de datos personales (Ley N.° 29733). Un abogado revisará tu resultado y tomará la decisión final.

Reglas:
- Marca "rojo" cuando una cláusula incumple una REGLA OBLIGATORIA (sección 4 de la política) o la ley.
- Marca "amarillo" cuando incumple una CONDICIÓN RECOMENDADA (sección 5 de la política) o es ambigua.
- No inventes problemas: si una cláusula cumple la política, no la incluyas como alerta.
- Si el contrato omite algo que la política exige (por ejemplo, una cláusula de datos personales), es una alerta.
- "cita" debe ser una copia LITERAL y exacta de una frase del contrato (entre 5 y 40 palabras), sin cambiar
  ni una palabra, para que el abogado pueda encontrarla. Si la alerta es por una omisión, cita la frase
  más cercana relacionada.
- Escribe "explicacion" en lenguaje simple, para alguien que no es abogado (1–2 oraciones).
- "redaccion_sugerida" es el texto alternativo de la cláusula que cumpliría la política (máximo 60 palabras).
- Sé breve: "titulo" de máximo 8 palabras y "conformes" con máximo 6 frases cortas.
- Responde SOLO con un objeto JSON válido, sin texto antes ni después y sin bloques de código."""

FORMATO_JSON = """{
  "resumen": "2–3 oraciones en lenguaje simple: de qué trata el contrato y si se puede firmar",
  "datos_contrato": {"proveedor": "", "objeto": "", "monto_total": "", "plazo": ""},
  "alertas": [
    {
      "color": "rojo | amarillo",
      "titulo": "título corto del problema",
      "clausula": "por ejemplo: QUINTA (5.2)",
      "cita": "frase literal del contrato",
      "regla": "número y nombre de la regla de la política o ley incumplida, p. ej. 4.1 Plazo de pago",
      "explicacion": "por qué es un riesgo, en simple",
      "redaccion_sugerida": "texto alternativo"
    }
  ],
  "conformes": ["aspectos importantes que SÍ cumplen la política, en frases cortas"]
}"""


@dataclass
class Resultado:
    datos: dict
    segundos: float
    tokens_entrada: int = 0
    tokens_salida: int = 0
    modelo: str = MODELO_ID
    texto_crudo: str = ""
    alertas: list = field(default_factory=list)


# ---------------------------------------------------------------- llamada al modelo

_ALERTA_SCHEMA = {
    "type": "object",
    "properties": {
        "color": {"type": "string", "enum": ["rojo", "amarillo"]},
        "titulo": {"type": "string"},
        "clausula": {"type": "string"},
        "cita": {"type": "string"},
        "regla": {"type": "string"},
        "explicacion": {"type": "string"},
        "redaccion_sugerida": {"type": "string"},
    },
    "required": ["color", "titulo", "clausula", "cita", "regla", "explicacion", "redaccion_sugerida"],
}
HERRAMIENTA_REVISION = {
    "toolSpec": {
        "name": "entregar_revision",
        "description": "Entrega el resultado de la revisión del contrato.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "resumen": {"type": "string"},
                "datos_contrato": {"type": "object", "properties": {
                    "proveedor": {"type": "string"}, "objeto": {"type": "string"},
                    "monto_total": {"type": "string"}, "plazo": {"type": "string"}}},
                "alertas": {"type": "array", "items": _ALERTA_SCHEMA},
                "conformes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["resumen", "datos_contrato", "alertas", "conformes"],
        }},
    }
}


def _llamar_modelo(sistema: str, mensajes: list[dict], max_tokens: int = 16000,
                   herramienta: dict | None = None) -> tuple[str, dict | None, dict, float]:
    """Llama al modelo con la Converse API. Devuelve (texto, json_de_herramienta, uso, segundos)."""
    parametros = dict(
        modelId=MODELO_ID,
        system=[{"text": sistema}],
        messages=mensajes,
        inferenceConfig={"maxTokens": max_tokens},  # Kimi K3.1 no acepta "temperature"
    )
    if herramienta:
        parametros["toolConfig"] = {"tools": [herramienta], "toolChoice": {"any": {}}}
    inicio = time.time()
    respuesta = cliente_bedrock().converse(**parametros)
    segundos = time.time() - inicio
    bloques = respuesta["output"]["message"]["content"]
    texto = "".join(b.get("text", "") for b in bloques)  # se ignoran los bloques de razonamiento
    datos = next((b["toolUse"]["input"] for b in bloques if "toolUse" in b), None)
    return texto, datos, respuesta.get("usage", {}), segundos


def _extraer_json(texto: str) -> dict:
    texto = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError("El modelo no devolvió JSON")
    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError as error:
        raise ValueError(str(error)) from error


def textos_referencia() -> tuple[str, str]:
    return leer_pdf(POLITICA_PDF), leer_pdf(NORMATIVA_PDF)


def analizar_contrato(texto_contrato: str) -> Resultado:
    politica, normativa = textos_referencia()
    mensaje = (
        f"<politica>\n{politica}\n</politica>\n\n"
        f"<normativa>\n{normativa}\n</normativa>\n\n"
        f"<contrato>\n{texto_contrato}\n</contrato>\n\n"
        f"Revisa el contrato y responde SOLO con este formato JSON:\n{FORMATO_JSON}"
    )
    # Nota: con Kimi K3.1 el modo "herramienta" devolvía las listas como texto cortado, así que se
    # pide el JSON directamente en la respuesta (HERRAMIENTA_REVISION queda para otros modelos).
    texto, datos, uso, segundos = _llamar_modelo(PROMPT_SISTEMA, [{"role": "user", "content": [{"text": mensaje}]}])
    if datos is None:  # el modelo respondió en texto: se intenta leer el JSON igual
        try:
            datos = _extraer_json(texto)
        except ValueError as error:
            (RAIZ / "resultados").mkdir(exist_ok=True)
            (RAIZ / "resultados" / "ultimo_error.txt").write_text(texto, encoding="utf-8")
            raise ValueError("La respuesta del modelo no se pudo leer (guardada en resultados/ultimo_error.txt)") from error

    # Algunos modelos devuelven las listas como texto JSON dentro del JSON: se convierten.
    (RAIZ / "resultados").mkdir(exist_ok=True)
    (RAIZ / "resultados" / "ultimo_crudo.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    for campo in ("alertas", "conformes", "datos_contrato"):
        if isinstance(datos.get(campo), str):
            try:
                datos[campo] = json.loads(datos[campo])
            except json.JSONDecodeError:
                pass

    # Verificación automática: ¿la cita existe tal cual en el contrato? (prueba de que no inventa)
    contrato_norm = normalizar(texto_contrato)
    for alerta in datos.get("alertas", []):
        alerta["cita_verificada"] = bool(alerta.get("cita")) and normalizar(alerta["cita"]) in contrato_norm
    orden = {"rojo": 0, "amarillo": 1}
    datos["alertas"] = sorted(datos.get("alertas", []), key=lambda a: orden.get(a.get("color", ""), 2))

    return Resultado(
        datos=datos,
        segundos=segundos,
        tokens_entrada=uso.get("inputTokens", 0),
        tokens_salida=uso.get("outputTokens", 0),
        texto_crudo=texto,
        alertas=datos["alertas"],
    )


# ---------------------------------------------------------------- chat (nivel 1: sin RAG)

PROMPT_CHAT = """Eres el asistente legal del Banco Andino del Perú S.A. Respondes preguntas sobre el contrato
que se está revisando, la Política de Contratación con Proveedores del Banco y la Ley N.° 29733.
- Responde en español, en lenguaje simple y en máximo 4 oraciones.
- Basa tu respuesta SOLO en los documentos entregados y di siempre de dónde sale
  (por ejemplo: "Contrato, cláusula QUINTA" o "Política, regla 4.1").
- Si la respuesta no está en los documentos, di exactamente: "No encuentro esa información en los documentos."
"""


def preguntar(texto_contrato: str, historial: list[dict], pregunta: str) -> str:
    politica, normativa = textos_referencia()
    contexto = (
        f"<contrato>\n{texto_contrato}\n</contrato>\n<politica>\n{politica}\n</politica>\n"
        f"<normativa>\n{normativa}\n</normativa>"
    )
    mensajes = [
        {"role": "user", "content": [{"text": contexto + "\n\nEstos son los documentos. Espera mis preguntas."}]},
        {"role": "assistant", "content": [{"text": "Entendido. ¿Qué quieres saber?"}]},
    ]
    for turno in historial:
        mensajes.append({"role": turno["rol"], "content": [{"text": turno["texto"]}]})
    mensajes.append({"role": "user", "content": [{"text": pregunta}]})
    texto, _, _, _ = _llamar_modelo(PROMPT_CHAT, mensajes, max_tokens=1500)
    return texto.strip()
