"""App de la demo: Revisor inteligente de contratos.

Ejecutar desde la raíz del proyecto:  .venv/bin/streamlit run app/app.py
"""

import hashlib
import html
import io
import json
import pathlib
import sys

import streamlit as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import revisor  # noqa: E402

DIR_RESULTADOS = revisor.RAIZ / "resultados"
EJEMPLOS = {
    "Centro de contacto – DataPlus Global Services": "contrato_03_contact_center_dataplus.pdf",
    "Limpieza de agencias – Servicios Integrales Huallaga": "contrato_02_limpieza_servicios_huallaga.pdf",
    "Soporte TI – Soluciones Digitales Inca": "contrato_01_soporte_ti_soluciones_inca.pdf",
}

# Preguntas sugeridas en el chat: cada una tiene respuesta clara en el contrato o en la política.
SUGERENCIAS = {
    "contrato_03_contact_center_dataplus.pdf": [
        "¿Dónde se guardarán los datos de los clientes del banco?",
        "Si hay un problema con el proveedor, ¿en qué país y en qué idioma se resolvería?",
        "¿Cuánto tiempo debe guardar el proveedor la confidencialidad después del contrato?",
    ],
    "contrato_02_limpieza_servicios_huallaga.pdf": [
        "¿En cuántos días tiene que pagar el banco cada factura?",
        "¿Qué pasa si el banco quiere terminar el contrato antes de tiempo?",
        "¿Cada cuánto puede subir el precio y cuánto?",
    ],
    "contrato_01_soporte_ti_soluciones_inca.pdf": [
        "¿En cuánto tiempo deben arreglar una computadora de ventanilla?",
        "¿Qué penalidad se aplica si el proveedor demora en atender?",
        "¿Cómo protege este contrato los datos personales de los clientes?",
    ],
}
SUGERENCIAS_GENERALES = [
    "¿Cuál es el plazo de pago y cumple con la política del banco?",
    "¿Este contrato se renueva automáticamente?",
    "¿Qué pasa si el banco quiere terminar el contrato antes de tiempo?",
]

st.set_page_config(page_title="Revisor de contratos", page_icon="⚖️", layout="wide")
st.markdown(
    """<style>
    .alerta {border-radius: 10px; padding: 14px 18px; margin-bottom: 12px; border-left: 8px solid;}
    .rojo {background: rgba(220,38,38,.10); border-color: #dc2626;}
    .amarillo {background: rgba(234,179,8,.13); border-color: #ca8a04;}
    .cita {font-family: Georgia, serif; font-style: italic; padding: 6px 10px; margin: 8px 0;
           background: rgba(127,127,127,.12); border-radius: 6px;}
    .st-key-inicio button p {font-size: 1.9rem; font-weight: 700;}
    .st-key-inicio button {padding: 0;}
    .pill {font-size: .8rem; padding: 2px 8px; border-radius: 999px; background: rgba(127,127,127,.18);}
    </style>""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- análisis con caché

def _ruta_guardada(clave: str) -> pathlib.Path:
    return DIR_RESULTADOS / f"{clave}.json"


def analizar(texto: str, clave: str, usar_guardado: bool) -> dict:
    """Analiza el contrato. Guarda cada resultado para poder mostrarlo sin internet (plan B)."""
    ruta = _ruta_guardada(clave)
    if usar_guardado and ruta.exists():
        return json.loads(ruta.read_text(encoding="utf-8"))
    r = revisor.analizar_contrato(texto)
    salida = {
        "datos": r.datos, "segundos": r.segundos, "tokens_entrada": r.tokens_entrada,
        "tokens_salida": r.tokens_salida, "modelo": r.modelo,
    }
    DIR_RESULTADOS.mkdir(exist_ok=True)
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    return salida


# ---------------------------------------------------------------- barra lateral

with st.sidebar:
    # El título funciona como botón "Inicio": borra el análisis y el chat para revisar otro contrato.
    if st.button("⚖️ Revisor de contratos", type="tertiary", key="inicio", help="Volver al inicio"):
        for k in ("resultado", "clave", "chat"):
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("Banco Andino del Perú · Asesoría Legal")
    origen = st.radio("Contrato a revisar", ["Elegir un ejemplo", "Subir un PDF"])
    escaneado, datos_pdf = False, b""
    if origen == "Elegir un ejemplo":
        nombre = st.selectbox("Ejemplo", list(EJEMPLOS))
        archivo = revisor.DIR_PDF / EJEMPLOS[nombre]
        texto = revisor.leer_pdf(archivo)
        st.download_button("Ver el PDF", archivo.read_bytes(), file_name=archivo.name, mime="application/pdf")
    else:
        subido = st.file_uploader("Contrato en PDF (digital o escaneado)", type="pdf")
        nombre = subido.name if subido else ""
        datos_pdf = subido.getvalue() if subido else b""
        texto = revisor.leer_pdf(io.BytesIO(datos_pdf)) if subido else ""
        escaneado = bool(subido) and revisor.es_escaneado(texto)
        if escaneado:
            # Sin texto seleccionable: la IA leerá las páginas como imágenes al presionar "Revisar".
            texto = revisor.transcripcion_guardada(datos_pdf) or ""
            st.caption("📷 PDF escaneado: la IA leerá las páginas como imágenes.")

    usar_guardado = st.toggle("Usar el último resultado guardado", value=False,
                              help="Plan B: muestra el último análisis sin llamar a la IA.")
    revisar = st.button("🔍 Revisar contrato", type="primary", use_container_width=True,
                        disabled=not (texto or escaneado))

base_clave = datos_pdf if escaneado else texto.encode()
clave = hashlib.sha256(base_clave).hexdigest()[:16] if base_clave else ""
if revisar:
    with st.spinner("Leyendo el contrato y comparándolo con las políticas del banco…"):
        try:
            if escaneado and not texto:
                texto, _ = revisor.leer_contrato(datos_pdf)
            st.session_state["resultado"] = analizar(texto, clave, usar_guardado)
            st.session_state["clave"] = clave
            st.session_state["chat"] = []
        except Exception as error:  # se muestra el error en vez de romper la demo
            st.error(f"No se pudo analizar el contrato: {error}")

res = st.session_state.get("resultado") if st.session_state.get("clave") == clave else None

# ---------------------------------------------------------------- contenido principal

if not res:
    st.header("Revisor inteligente de contratos")
    st.write(
        "Elige o sube un contrato y presiona **Revisar contrato**. La IA lo compara con la "
        "**política de contratación del banco** y con la **Ley de Protección de Datos Personales**, "
        "y te muestra un semáforo con lo que debes mirar."
    )
    st.info("🟢 Cumple  ·  🟡 Revisar  ·  🔴 Riesgo: no firmar sin corregir")
    st.stop()

datos = res["datos"]
alertas = datos.get("alertas", [])
rojas = [a for a in alertas if a.get("color") == "rojo"]
amarillas = [a for a in alertas if a.get("color") == "amarillo"]
verdes = datos.get("conformes", [])

st.header(nombre)
st.write(datos.get("resumen", ""))

c1, c2, c3, c4 = st.columns(4)
c1.metric("🔴 Riesgos", len(rojas))
c2.metric("🟡 A revisar", len(amarillas))
c3.metric("🟢 Conformes", len(verdes))
c4.metric("⏱️ Tiempo", f"{res['segundos']:.0f} s")

tab_semaforo, tab_chat, tab_detalle = st.tabs(["🚦 Semáforo", "💬 Preguntar", "ℹ️ Detalle"])

with tab_semaforo:
    if not alertas:
        st.success("🟢 No se encontraron incumplimientos de la política. El contrato puede seguir su curso.")
    for i, a in enumerate(alertas):
        color = "rojo" if a.get("color") == "rojo" else "amarillo"
        # El texto viene del modelo y del PDF: se escapa para que no pueda insertar HTML en la página.
        e = lambda campo, a=a: html.escape(str(a.get(campo, "")))  # noqa: E731
        icono = "🔴" if color == "rojo" else "🟡"
        verificada = "✅ cita encontrada en el contrato" if a.get("cita_verificada") else "⚠️ cita no encontrada literalmente"
        st.markdown(
            f"""<div class="alerta {color}">
            <b>{icono} {e('titulo')}</b> &nbsp; <span class="pill">Cláusula {e('clausula')}</span>
            <div class="cita">“{e('cita')}”</div>
            <div>{e('explicacion')}</div>
            <div style="margin-top:6px;font-size:.85rem;opacity:.8">📘 {e('regla')} · {verificada}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        with st.expander("✍️ Redacción sugerida"):
            st.write(a.get("redaccion_sugerida", ""))
        decision = st.radio(
            "Decisión del abogado", ["Pendiente", "Aceptar alerta", "Descartar"],
            horizontal=True, key=f"decision_{clave}_{i}", label_visibility="collapsed",
        )
    if verdes:
        with st.expander(f"🟢 Lo que sí cumple ({len(verdes)})"):
            for v in verdes:
                st.write(f"- {v}")

with tab_chat:
    st.caption("Pregunta sobre este contrato, la política del banco o la ley de datos personales.")
    for turno in st.session_state.get("chat", []):
        with st.chat_message("user" if turno["rol"] == "user" else "assistant"):
            st.write(turno["texto"])
    # Sirve tanto para un ejemplo elegido (nombre visible) como para un PDF subido (nombre de archivo).
    sugerencias = SUGERENCIAS.get(EJEMPLOS.get(nombre, nombre), SUGERENCIAS_GENERALES)
    st.write("**Preguntas sugeridas:**")
    clic = None
    for j, sugerencia in enumerate(sugerencias):
        if st.button(sugerencia, key=f"sug_{clave}_{j}"):
            clic = sugerencia
    escrita = st.chat_input(f"Ej.: {sugerencias[0]}")
    pregunta = escrita or clic
    if pregunta:
        with st.chat_message("user"):
            st.write(pregunta)
        with st.chat_message("assistant"), st.spinner("Buscando en los documentos…"):
            try:
                respuesta = revisor.preguntar(texto, st.session_state.get("chat", []), pregunta)
            except Exception as error:
                respuesta = f"No pude responder: {error}"
            st.write(respuesta)
        st.session_state.setdefault("chat", []).extend(
            [{"rol": "user", "texto": pregunta}, {"rol": "assistant", "texto": respuesta}]
        )

with tab_detalle:
    st.json(datos.get("datos_contrato", {}))
    st.caption(
        f"Modelo: `{res['modelo']}` · Tokens de entrada: {res['tokens_entrada']:,} · "
        f"Tokens de salida: {res['tokens_salida']:,}"
    )
