#!/usr/bin/env python3
"""
Scraper para el Boletin Oficial de Mineria de Chile
(https://www.boletinoficialdemineria.cl).

IMPORTANTE (confirmado en vivo, agosto 2026): el sitio esta protegido por un
challenge anti-bot tipo F5 BIG-IP/TrafficShield (se detecta por la variable
`window["bobcmn"]` y la cadena `/TSPD/` en el HTML crudo). Un cliente HTTP
sin motor JS (p.ej. `requests`) recibe solo la pagina del challenge (~6-7 KB,
sin contenido real) en vez de la pagina con las publicaciones del dia. Por
eso este scraper usa Playwright (Chromium real, headless) para navegar: el
navegador ejecuta el JS del challenge igual que lo haria un usuario real, y
entonces si trae el HTML final con las publicaciones.

- La pagina de listado es HTML renderizado en servidor una vez pasado el
  challenge (sin llamadas XHR/API separadas) -> tras `page.goto`, el DOM ya
  tiene todo el contenido.
- El PDF de cada publicacion se sirve desde diariooficial.interior.gob.cl.
  Se descarga con `context.request.get(...)`, que comparte las cookies de la
  sesion del navegador (por si ese host tambien exige alguna cookie fijada
  durante la navegacion anterior).
- El menu de categorias en la pagina de listado solo trae un <a href=...> con
  numero de "subseccion" para las categorias que SI tienen contenido ese dia;
  las vacias aparecen como texto plano sin href. Por eso no hardcodeamos los
  numeros de subseccion de Sentencias de Exploracion/Explotacion (rara vez
  activas) ni de Oposiciones de Mensura: se descubren dinamicamente cada dia.
"""
import os
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://www.boletinoficialdemineria.cl/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Nombre visible en el menu -> tipo interno usado en el resto del pipeline.
CATEGORIES = {
    "PedimentosMineros": "pedimento",
    "Manifestaciones Mineras": "manifestacion",
    "Solicitudes de Mensura": "mensura",
    "Oposiciones de Mensura": "mensura",
    "Sentencias de Exploración": "sentencia_exploracion",
    "Sentencias de Explotación": "sentencia_explotacion",
}


def _normalize_label(s):
    """Colapsa TODO espacio en blanco (incluidos saltos de linea entre nodos
    de texto separados en el DOM, ej. "Pedimentos" y "Mineros" renderizados
    en dos lineas). Bug detectado el 18-08-2026: la edicion 44527 tenia 2
    Pedimentos reales que el scraper no encontro porque a.get_text(strip=True)
    no calzaba exacto contra la clave "PedimentosMineros" del dict — no se
    habia notado antes porque el 17-08 esa categoria realmente tenia 0
    publicaciones. Normalizar ambos lados de la comparacion la hace robusta
    sin importar como el sitio particione el texto del link ese dia."""
    return re.sub(r"\s+", "", s)


CATEGORIES_NORM = {_normalize_label(k): v for k, v in CATEGORIES.items()}

# ID de subseccion fijo y conocido para Pedimentos Mineros (confirmado
# manualmente por Miguel el 18-08-2026 en la URL del sitio: .../?date=...&
# edition=...&subseccion=7099).
PEDIMENTO_SUBSECCION_ID = "7099"

DEBUG = bool(os.environ.get("DEBUG_SCRAPER"))


def _stable_content(page, timeout=45000, retries=6):
    """page.content() con reintentos: el challenge anti-bot a veces dispara
    una navegacion/recarga del lado del cliente justo despues de que
    Playwright considera la pagina "cargada", y llamar a content() en ese
    instante exacto lanza un error transitorio de Playwright ("Unable to
    retrieve content because the page is navigating"). Reintentamos con una
    pausa breve en vez de dejar que tumbe todo el pipeline."""
    last_exc = None
    for attempt in range(retries):
        try:
            return page.content()
        except Exception as e:
            last_exc = e
            page.wait_for_timeout(1500)
            try:
                page.wait_for_load_state("load", timeout=timeout)
            except Exception:
                pass
    raise last_exc


def _goto_and_get_html(page, url, timeout=45000):
    """Navega con un navegador real (para pasar el challenge JS anti-bot) y
    devuelve el HTML final, esperando a que la pagina se estabilice."""
    page.goto(url, wait_until="load", timeout=timeout)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    html = _stable_content(page, timeout)

    if "TSPD" in html or "bobcmn" in html:
        # El challenge anti-bot no alcanzo a resolverse a tiempo; damos un
        # margen extra y reintentamos antes de rendirnos.
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        html = _stable_content(page, timeout)

    if DEBUG:
        print(f"DEBUG url={url} final_url={page.url} html_len={len(html)}")
        if "TSPD" in html or "bobcmn" in html:
            print("DEBUG *** el challenge anti-bot sigue presente en el HTML final ***")
        print(f"DEBUG snippet: {html[:300]!r}")

    return html


def discover_active_sections(page, date_str, edition=None):
    """Visita la portada del dia y devuelve (edicion_resuelta, {subseccion_id: [tipo,...]})
    solo para las categorias que tienen contenido ese dia (con href en el menu)."""
    url = f"{BASE}?date={date_str}"
    if edition:
        url += f"&edition={edition}"
    html = _goto_and_get_html(page, url)
    soup = BeautifulSoup(html, "html.parser")

    # La edicion real queda fijada en la URL final tras el redirect del sitio.
    m_ed = re.search(r"edition=(\d+)", page.url)
    resolved_edition = m_ed.group(1) if m_ed else edition

    active = {}
    all_labels = []
    for a in soup.find_all("a", href=True):
        label = a.get_text(strip=True)
        all_labels.append(label)
        tipo = CATEGORIES_NORM.get(_normalize_label(label))
        if not tipo:
            continue
        m = re.search(r"subseccion=(\d+)", a["href"])
        if m:
            active.setdefault(m.group(1), []).append(tipo)

    # Pedimentos Mineros SIEMPRE se agrega directo por su ID de subseccion
    # fijo (7099), sin depender de que la portada lo muestre como tile/link
    # navegable. Confirmado con un log DEBUG_SCRAPER el 18-08-2026: la
    # portada de la edicion 44527 NO listaba ningun link a Pedimentos entre
    # las 19 etiquetas <a> encontradas (solo Manifestaciones, Solicitudes de
    # Mensura, Prorrogas, Nomina, Sumario...) pese a que esa edicion SI tenia
    # 2 pedimentos reales -- visibles directo en .../subseccion=7099 y
    # confirmados ademas por dos "Ver PDF (CVE-...)" sueltos en el Sumario de
    # la Edicion con los mismos CVE. O sea: el sitio nunca expone Pedimentos
    # como tile descubrible en la portada (a diferencia de Manifestaciones/
    # Mensuras, que si tienen tile+subseccion propios cada dia), asi que
    # cualquier intento de "descubrirlo" desde la portada (por texto o por
    # href) esta condenado a fallar. fetch_index() arma su URL de forma
    # independiente del descubrimiento por <a href> del listado de la
    # portada, asi que no hace falta encontrar ningun link ahi: si ese dia
    # no hay pedimentos, fetch_index() simplemente devuelve una lista vacia
    # y no se descarga nada, igual que para cualquier otra categoria vacia.
    active.setdefault(PEDIMENTO_SUBSECCION_ID, []).append("pedimento")

    if DEBUG:
        print(f"DEBUG all <a> labels found ({len(all_labels)}): {all_labels[:40]}")

    return resolved_edition, active


def fetch_index(page, date_str, edition, subseccion):
    """Descarga la tabla de publicaciones de una subseccion y devuelve una
    lista de dicts: region, provincia, nombre_solicitante, cve, href."""
    url = f"{BASE}?date={date_str}&edition={edition}&subseccion={subseccion}"
    html = _goto_and_get_html(page, url)
    soup = BeautifulSoup(html, "html.parser")

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


def download_pdf(context, href, dest_path, retries=3, sleep_between=1.0):
    """Descarga un PDF usando el contexto del navegador (comparte cookies de
    sesion con la navegacion anterior, por si el host de PDFs tambien las
    exige)."""
    last_exc = None
    for attempt in range(retries):
        try:
            resp = context.request.get(href, timeout=60000)
            if not resp.ok:
                raise ValueError(f"status HTTP {resp.status}")
            content = resp.body()
            if not content.startswith(b"%PDF"):
                raise ValueError(f"Respuesta no es un PDF valido ({href})")
            with open(dest_path, "wb") as f:
                f.write(content)
            return True
        except Exception as e:
            last_exc = e
            time.sleep(sleep_between)
    raise RuntimeError(f"No se pudo descargar {href}: {last_exc}")


def scrape_day(date_str, edition=None, out_dir="pdfs", sleep_between_pdfs=0.3):
    """Punto de entrada principal. date_str formato DD-MM-AAAA.
    Devuelve (edition_resuelta, manifest) donde manifest es una lista de
    dicts con toda la metadata de indice + tipo + ruta local del PDF."""
    os.makedirs(out_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        resolved_edition, active = discover_active_sections(page, date_str, edition)
        print(f"Edicion resuelta: {resolved_edition}")
        print(f"Secciones activas hoy: {active}")

        manifest = []
        for subseccion, tipos in active.items():
            tipo = tipos[0]  # Solicitudes/Oposiciones de Mensura ambas -> 'mensura'
            items = fetch_index(page, date_str, resolved_edition, subseccion)
            print(f"  subseccion {subseccion} ({tipo}): {len(items)} publicaciones")
            for it in items:
                if not it["cve"] or not it["href"]:
                    continue
                fname = f"gm_{tipo}_{it['cve']}.pdf"
                dest = os.path.join(out_dir, fname)
                try:
                    download_pdf(context, it["href"], dest)
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

        browser.close()

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
