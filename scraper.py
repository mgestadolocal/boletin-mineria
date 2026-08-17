#!/usr/bin/env python3
"""
Scraper puro-Python (sin navegador) para el Boletin Oficial de Mineria de Chile
(https://www.boletinoficialdemineria.cl).

Confirmado por inspeccion en vivo (agosto 2026):
- La pagina de listado es HTML renderizado en servidor (sin llamadas XHR/API
  separadas) -> un GET normal con `requests` trae todo el contenido.
- El PDF de cada publicacion se sirve directo desde
  diariooficial.interior.gob.cl con un GET simple (redirige http->https),
  sin login ni challenge JS.
- El menu de categorias en la pagina de listado solo trae un <a href=...> con
  numero de "subseccion" para las categorias que SI tienen contenido ese dia;
  las vacias aparecen como texto plano sin href. Por eso no hardcodeamos los
  numeros de subseccion de Sentencias de Exploracion/Explotacion (rara vez
  activas) ni de Oposiciones de Mensura: se descubren dinamicamente cada dia.
"""
import os
import re
import time
import requests
from bs4 import BeautifulSoup

BASE = "https://www.boletinoficialdemineria.cl/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Nombre visible en el menu -> tipo interno usado en el resto del pipeline.
CATEGORIES = {
    "PedimentosMineros": "pedimento",
    "Manifestaciones Mineras": "manifestacion",
    "Solicitudes de Mensura": "mensura",
    "Oposiciones de Mensura": "mensura",
    "Sentencias de Exploración": "sentencia_exploracion",
    "Sentencias de Explotación": "sentencia_explotacion",
}


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_active_sections(session, date_str, edition=None):
    """Visita la portada del dia y devuelve {subseccion_id: tipo_interno} solo
    para las categorias que tienen contenido ese dia (con href en el menu)."""
    url = f"{BASE}?date={date_str}"
    if edition:
        url += f"&edition={edition}"
    r = session.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # La edicion real queda fijada en la URL final tras el redirect del sitio.
    m_ed = re.search(r"edition=(\d+)", r.url)
    resolved_edition = m_ed.group(1) if m_ed else edition

    active = {}
    all_labels = []
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True)
        all_labels.append(label)
        tipo = CATEGORIES.get(label)
        if not tipo:
            continue
        m = re.search(r"subseccion=(\d+)", a["href"])
        if m:
            active.setdefault(m.group(1), []).append(tipo)

    if os.environ.get("DEBUG_SCRAPER"):
        print(f"DEBUG status={r.status_code} final_url={r.url} history={[h.url for h in r.history]}")
        print(f"DEBUG page length={len(r.text)}")
        print(f"DEBUG all <a> labels found ({len(all_labels)}): {all_labels[:40]}")
        print(f"DEBUG snippet: {r.text[:500]!r}")

    return resolved_edition, active


def fetch_index(session, date_str, edition, subseccion):
    """Descarga la tabla de publicaciones de una subseccion y devuelve una
    lista de dicts: region, provincia, nombre_solicitante, cve, href."""
    url = f"{BASE}?date={date_str}&edition={edition}&subseccion={subseccion}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    region = provincia = None
    for tr in soup.select("table tr"):
        text = tr.get_text(strip=True)
        if not text:
            continue
        if text.upper().startswith("REGIÓN") or text.upper().startswith("REGION"):
            region = text
            continue
        if text.lower().startswith("provincia"):
            provincia = text
            continue
        a = tr.find("a", href=re.compile(r"/publicaciones/.+\.pdf"))
        if not a:
            continue
        href = a["href"]
        m_cve = re.search(r"CVE-(\d+)", a.get_text())
        if not m_cve:
            m_cve = re.search(r"/(\d+)\.pdf", href)
        cve = m_cve.group(1) if m_cve else None
        # Nombre/solicitante: todo el texto de la fila menos el texto del link.
        nombre_solicitante = text.replace(a.get_text(strip=True), "").strip()
        if not nombre_solicitante:
            # A veces el nombre esta DENTRO de otro nodo previo al link.
            nombre_solicitante = tr.get_text(" ", strip=True)
        items.append(dict(
            region=region, provincia=provincia,
            nombre_solicitante=nombre_solicitante,
            cve=cve, href=href,
        ))
    return items


def download_pdf(session, href, dest_path, retries=3, sleep_between=1.0):
    last_exc = None
    for attempt in range(retries):
        try:
            r = session.get(href, timeout=60)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise ValueError(f"Respuesta no es un PDF valido ({href})")
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            last_exc = e
            time.sleep(sleep_between)
    raise RuntimeError(f"No se pudo descargar {href}: {last_exc}")


def scrape_day(date_str, edition=None, out_dir="pdfs", sleep_between_pdfs=0.3):
    """Punto de entrada principal. date_str formato DD-MM-AAAA.
    Devuelve (edition_resuelta, manifest) donde manifest es una lista de
    dicts con toda la metadata de indice + tipo + ruta local del PDF."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    session = _session()

    resolved_edition, active = discover_active_sections(session, date_str, edition)
    print(f"Edicion resuelta: {resolved_edition}")
    print(f"Secciones activas hoy: {active}")

    manifest = []
    for subseccion, tipos in active.items():
        tipo = tipos[0]  # Solicitudes/Oposiciones de Mensura ambas -> 'mensura'
        items = fetch_index(session, date_str, resolved_edition, subseccion)
        print(f"  subseccion {subseccion} ({tipo}): {len(items)} publicaciones")
        for it in items:
            if not it["cve"] or not it["href"]:
                continue
            fname = f"gm_{tipo}_{it['cve']}.pdf"
            dest = os.path.join(out_dir, fname)
            try:
                download_pdf(session, it["href"], dest)
            except Exception as e:
                print(f"    ERROR descargando CVE {it['cve']}: {e}")
                continue
            manifest.append(dict(
                tipo=tipo, cve=it["cve"], region=it["region"],
                provincia=it["provincia"],
                nombre_solicitante=it["nombre_solicitante"],
                href=it["href"], archivo=fname, local_path=dest,
            ))
            time.sleep(sleep_between_pdfs)
    return resolved_edition, manifest


if __name__ == "__main__":
    import sys
    import json
    date_arg = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%d-%m-%Y")
    edition, manifest = scrape_day(date_arg)
    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(dict(date=date_arg, edition=edition, items=manifest), f,
                   ensure_ascii=False, indent=1)
    print(f"Total descargado: {len(manifest)}")
