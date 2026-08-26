#!/usr/bin/env python3
"""Revisa docs/data/reporte_latest.json despues de cada corrida del pipeline
y, si aparecio alguna Sentencia de Exploracion/Explotacion (georreferenciada
o pendiente de revision manual), abre o actualiza un issue de GitHub para que
alguien se entere sin tener que ir a revisar el reporte a mano.

Usa solo la libreria estandar (urllib) para no depender de que el contenedor
del job tenga instalado `gh` u otra herramienta extra. Nunca tumba el job por
un problema de notificacion: en el peor caso, el aviso simplemente no sale,
pero la corrida diaria del pipeline sigue su curso normal.
"""
import json
import os
import sys
import urllib.error
import urllib.request

REPORT_PATH = "docs/data/reporte_latest.json"
TIPOS = ("sentencia_exploracion", "sentencia_explotacion")
LABELS_TXT = {
    "sentencia_exploracion": "Sentencias de Exploracion",
    "sentencia_explotacion": "Sentencias de Explotacion",
}
ISSUE_LABEL = "sentencia-detectada"
ISSUE_TITLE = "Sentencia de Exploracion/Explotacion detectada - revisar"


def api(method, path, body=None):
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GH_REPO"]
    url = f"https://api.github.com/repos/{repo}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    try:
        report = json.load(open(REPORT_PATH, encoding="utf-8"))
    except FileNotFoundError:
        print("No hay reporte_latest.json todavia, nada que avisar.")
        return

    por_tipo = report.get("por_tipo", {})
    sin_geo = report.get("sin_georreferenciar", [])

    ok = {t: por_tipo.get(t, 0) for t in TIPOS}
    pendientes = {t: [s for s in sin_geo if s.get("tipo") == t] for t in TIPOS}

    if not any(ok.values()) and not any(len(v) for v in pendientes.values()):
        print("Sin Sentencias en esta edicion, nada que avisar.")
        return

    lineas = [f"Edicion {report.get('edicion')} - {report.get('fecha')}", ""]
    for t in TIPOS:
        if ok[t]:
            lineas.append(f"- {LABELS_TXT[t]}: {ok[t]} georreferenciada(s) automaticamente.")
        if pendientes[t]:
            lineas.append(f"- {LABELS_TXT[t]}: {len(pendientes[t])} pendiente(s) de revision manual:")
            for p in pendientes[t]:
                lineas.append(f"  - CVE {p.get('cve')} ({p.get('archivo')}): {p.get('motivo')}")
    lineas += [
        "",
        "Detalle completo en docs/data/reporte_latest.json.",
        "Sitio: https://mgestadolocal.github.io/boletin-mineria/",
    ]
    body = "\n".join(lineas)

    # Asegura que la label exista (ignora el error si ya existe).
    api("POST", "/labels", {
        "name": ISSUE_LABEL, "color": "fbca04",
        "description": "Sentencia de Exploracion/Explotacion detectada en el boletin",
    })

    status, issues = api("GET", f"/issues?labels={ISSUE_LABEL}&state=open")
    if status == 200 and issues:
        n = issues[0]["number"]
        api("POST", f"/issues/{n}/comments", {"body": body})
        print(f"Comentado en issue existente #{n}")
    else:
        status, created = api("POST", "/issues", {
            "title": ISSUE_TITLE, "body": body, "labels": [ISSUE_LABEL],
        })
        if status in (200, 201):
            print(f"Issue nuevo creado: #{created.get('number')}")
        else:
            print(f"No se pudo crear el issue (status {status}): {created}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Nunca tumbar el job por un problema de notificacion.
        print(f"AVISO: fallo el chequeo de notificacion de Sentencias: {e}")
        sys.exit(0)
