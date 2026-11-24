"""Evalúa el revisor contra la hoja de respuestas (documentos/respuestas_esperadas.json).

Mide, por contrato:
  - detección: cuántos de los riesgos plantados encontró;
  - color correcto: si el riesgo encontrado salió con el color esperado (rojo / amarillo);
  - falsas alarmas: alertas que no corresponden a ningún riesgo plantado;
  - citas verificadas: cada cita debe existir tal cual en el texto del contrato.

Uso:
  python scripts/evaluar.py                 # usa los análisis guardados en resultados/ (gratis)
  python scripts/evaluar.py --analizar      # analiza con Bedrock los que no tengan resultado (cuesta centavos)
  python scripts/evaluar.py --repeticiones 3  # consistencia: 3 análisis nuevos por contrato (cuesta centavos x 3)

Una alerta corresponde a un riesgo si cita su "texto_clave", o si apunta a la misma cláusula
(p. ej. "QUINTA") y a la misma regla de la política (p. ej. "4.1").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "app"))

import revisor  # noqa: E402

HOJA = RAIZ / "documentos" / "respuestas_esperadas.json"
DIR_RES = RAIZ / "resultados"
DIR_REP = DIR_RES / "evaluacion"


# ---------------------------------------------------------------- correspondencia alerta ↔ riesgo

def ordinal(clausula: str) -> str:
    """'QUINTA (5.2)' -> 'QUINTA'. Quita tildes para comparar 'SÉTIMA' con 'SETIMA'."""
    palabra = (clausula or "").strip().split(" ")[0].split("(")[0].upper()
    return palabra.translate(str.maketrans("ÁÉÍÓÚ", "AEIOU"))


def numero_regla(regla: str) -> str:
    m = re.search(r"\d+\.\d+", regla or "")
    return m.group(0) if m else ""


def corresponde(alerta: dict, riesgo: dict) -> bool:
    if revisor.normalizar(riesgo["texto_clave"]) in revisor.normalizar(alerta.get("cita", "")):
        return True
    misma_clausula = ordinal(alerta.get("clausula", "")) == ordinal(riesgo["clausula"])
    misma_regla = numero_regla(riesgo["regla"]) in re.findall(r"\d+\.\d+", alerta.get("regla", ""))
    return misma_clausula and misma_regla


def evaluar_contrato(alertas: list[dict], riesgos: list[dict], texto: str) -> dict:
    texto_norm = revisor.normalizar(texto)
    detalle, usadas = [], set()
    for riesgo in riesgos:
        indices = [i for i, a in enumerate(alertas) if corresponde(a, riesgo)]
        usadas.update(indices)
        colores = {alertas[i].get("color") for i in indices}
        detalle.append({
            "id": riesgo["id"], "clausula": riesgo["clausula"], "esperado": riesgo["color"],
            "detectado": bool(indices), "color_ok": riesgo["color"] in colores,
            "colores": sorted(colores),
        })
    falsas = [a for i, a in enumerate(alertas) if i not in usadas]
    citas_ok = sum(bool(a.get("cita")) and revisor.normalizar(a["cita"]) in texto_norm for a in alertas)
    return {
        "riesgos": len(riesgos),
        "detectados": sum(d["detectado"] for d in detalle),
        "color_ok": sum(d["color_ok"] for d in detalle),
        "alertas": len(alertas),
        "falsas_alarmas": len(falsas),
        "citas_ok": citas_ok,
        "detalle": detalle,
        "falsas": [{"color": a.get("color"), "clausula": a.get("clausula"), "titulo": a.get("titulo")} for a in falsas],
    }


# ---------------------------------------------------------------- obtener análisis

def contratos_a_evaluar(hoja: dict) -> list[dict]:
    """Los PDFs digitales de la hoja, más su versión escaneada si existe (misma hoja de respuestas)."""
    lista = []
    for nombre, info in hoja["contratos"].items():
        pdf = revisor.DIR_PDF / nombre
        texto = revisor.leer_pdf(pdf)
        lista.append({"nombre": nombre, "riesgos": info["riesgos"], "texto": texto,
                      "clave": hashlib.sha256(texto.encode()).hexdigest()[:16], "escaneado": False})
        escaneado = revisor.DIR_PDF / "escaneados" / nombre.replace(".pdf", "_escaneado.pdf")
        if escaneado.exists():
            datos = escaneado.read_bytes()
            lista.append({"nombre": escaneado.name, "riesgos": info["riesgos"],
                          "texto": revisor.transcripcion_guardada(datos), "datos_pdf": datos,
                          "clave": hashlib.sha256(datos).hexdigest()[:16], "escaneado": True})
    return lista


def guardar(ruta: pathlib.Path, r: revisor.Resultado) -> dict:
    datos = {"datos": r.datos, "segundos": r.segundos, "tokens_entrada": r.tokens_entrada,
             "tokens_salida": r.tokens_salida, "modelo": r.modelo}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    return datos


def analizar(c: dict) -> revisor.Resultado:
    if c["escaneado"] and not c["texto"]:
        c["texto"], _ = revisor.leer_contrato(c["datos_pdf"])
    return revisor.analizar_contrato(c["texto"])


def obtener_guardado(c: dict, permitir_analizar: bool) -> dict | None:
    """El mismo archivo que guarda la app (resultados/<clave>.json), así se comparten los análisis."""
    ruta = DIR_RES / f"{c['clave']}.json"
    if ruta.exists():
        return json.loads(ruta.read_text(encoding="utf-8"))
    if not permitir_analizar:
        return None
    print(f"  Analizando {c['nombre']} con Bedrock…", flush=True)
    return guardar(ruta, analizar(c))


# ---------------------------------------------------------------- reporte

def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f} %" if b else "—"


def imprimir(nombre: str, ev: dict, guardado: dict) -> None:
    print(f"\n■ {nombre}")
    if ev["riesgos"]:
        print(f"  Detectó {ev['detectados']} de {ev['riesgos']} riesgos · color correcto en {ev['color_ok']}")
    else:
        print("  Contrato limpio (no tiene riesgos plantados)")
    print(f"  Alertas: {ev['alertas']} · falsas alarmas: {ev['falsas_alarmas']} · "
          f"citas verificadas: {ev['citas_ok']}/{ev['alertas']}")
    print(f"  Tiempo: {guardado.get('segundos', 0):.0f} s · tokens: "
          f"{guardado.get('tokens_entrada', 0):,} entrada / {guardado.get('tokens_salida', 0):,} salida")
    for d in ev["detalle"]:
        marca = "✅" if d["detectado"] and d["color_ok"] else ("🟠" if d["detectado"] else "❌")
        extra = "" if d["color_ok"] or not d["detectado"] else f" (salió {', '.join(d['colores'])})"
        print(f"    {marca} {d['id']} {d['clausula']} → esperado {d['esperado']}{extra}")
    for f in ev["falsas"]:
        print(f"    ⚠️  extra: {f['color']} · {f['clausula']} · {f['titulo']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--analizar", action="store_true", help="analiza con Bedrock los contratos sin resultado guardado (cuesta)")
    p.add_argument("--repeticiones", type=int, default=0, help="N análisis nuevos por contrato para medir consistencia (cuesta)")
    p.add_argument("--solo", help="evaluar solo los contratos cuyo nombre contenga este texto")
    args = p.parse_args()

    hoja = json.loads(HOJA.read_text(encoding="utf-8"))
    contratos = [c for c in contratos_a_evaluar(hoja) if not args.solo or args.solo in c["nombre"]]

    total = {"riesgos": 0, "detectados": 0, "color_ok": 0, "alertas": 0, "falsas_alarmas": 0, "citas_ok": 0}
    resumen = {}
    for c in contratos:
        guardado = obtener_guardado(c, args.analizar)
        if guardado is None:
            print(f"\n■ {c['nombre']}\n  Sin análisis guardado (ábrelo en la app o usa --analizar).")
            continue
        if not c["texto"]:
            print(f"\n■ {c['nombre']}\n  Falta la transcripción del escaneado; no se pueden verificar las citas.")
            continue
        ev = evaluar_contrato(guardado["datos"].get("alertas", []), c["riesgos"], c["texto"])
        imprimir(c["nombre"], ev, guardado)
        resumen[c["nombre"]] = ev
        if not c["escaneado"]:  # el total solo cuenta los PDFs digitales, para no duplicar riesgos
            for k in total:
                total[k] += ev[k]

    print("\n" + "=" * 70)
    print(f"TOTAL (PDF digitales): detectó {total['detectados']} de {total['riesgos']} riesgos "
          f"({pct(total['detectados'], total['riesgos'])}) · color correcto: {total['color_ok']}/{total['riesgos']}")
    print(f"Citas verificadas: {total['citas_ok']}/{total['alertas']} ({pct(total['citas_ok'], total['alertas'])}) · "
          f"falsas alarmas: {total['falsas_alarmas']} de {total['alertas']} alertas")

    consistencia = {}
    if args.repeticiones:
        print("\n" + "=" * 70 + f"\nCONSISTENCIA: {args.repeticiones} análisis nuevos por contrato")
        for c in contratos:
            if c["escaneado"]:
                continue
            corridas = []
            for i in range(1, args.repeticiones + 1):
                ruta = DIR_REP / f"{c['nombre'].removesuffix('.pdf')}_{i}.json"
                if ruta.exists():
                    guardado = json.loads(ruta.read_text(encoding="utf-8"))
                else:
                    print(f"  {c['nombre']}: corrida {i}…", flush=True)
                    guardado = guardar(ruta, analizar(c))
                ev = evaluar_contrato(guardado["datos"].get("alertas", []), c["riesgos"], c["texto"])
                rojos = sorted(d["id"] for d in ev["detalle"] if "rojo" in d["colores"])
                corridas.append({"detectados": ev["detectados"], "rojos": rojos, "falsas": ev["falsas_alarmas"]})
            iguales = len({tuple(x["rojos"]) for x in corridas}) == 1
            consistencia[c["nombre"]] = {"corridas": corridas, "rojos_iguales": iguales}
            print(f"\n■ {c['nombre']}: rojos {'IGUALES' if iguales else 'DISTINTOS'} en las {len(corridas)} corridas")
            for i, x in enumerate(corridas, 1):
                print(f"    corrida {i}: detectó {x['detectados']}/{len(c['riesgos'])} · rojos {x['rojos']} · extras {x['falsas']}")

    salida = DIR_RES / "evaluacion.json"
    salida.write_text(json.dumps({"total": total, "contratos": resumen, "consistencia": consistencia},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nDetalle guardado en {salida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
