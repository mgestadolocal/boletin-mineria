#!/usr/bin/env python3
"""
Orquestador diario: descarga -> extrae texto -> parsea -> georreferencia ->
exporta GeoPackage/GeoJSON -> genera mapa -> escribe reporte.

Uso: python run_pipeline.py [DD-MM-AAAA]  (por defecto: hoy, hora del runner)
"""
import sys
import os
import json
import time
import shutil

import pdfplumber

import parser as P
import build_geometry as G
import make_map as M


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


PARSERS = {
    'pedimento': P.parse_pedimento,
    'manifestacion': P.parse_manifestacion,
    'mensura': P.parse_mensura,
    'sentencia_exploracion': P.parse_sentencia_exploracion,
    'sentencia_explotacion': P.parse_sentencia_explotacion,
}


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%d-%m-%Y")
    print(f"=== Boletin Oficial de Mineria — {date_str} ===")

    import scraper as S
    pdf_dir = "pdfs"
    edition, manifest = S.scrape_day(date_str, out_dir=pdf_dir)

    print(f"\n{len(manifest)} PDFs descargados. Extrayendo texto y parseando...")

    features_by_type = {}
    sin_georreferenciar = []
    parsed_count = 0

    for item in manifest:
        tipo = item['tipo']
        if tipo not in PARSERS:
            # Cualquier tipo nuevo que el scraper llegue a descubrir sin parser aun.
            sin_georreferenciar.append(dict(
                cve=item['cve'], tipo=tipo, archivo=item['archivo'],
                motivo=f"sin_parser_para_tipo_{tipo}_revisar_manualmente",
            ))
            continue
        try:
            text = extract_text(item['local_path'])
        except Exception as e:
            sin_georreferenciar.append(dict(cve=item['cve'], tipo=tipo,
                                             archivo=item['archivo'],
                                             motivo=f"error_extrayendo_pdf: {e}"))
            continue

        rec = PARSERS[tipo](item['archivo'], text)
        if not rec.get('ok'):
            sin_georreferenciar.append(dict(cve=item['cve'], tipo=tipo,
                                             archivo=item['archivo'],
                                             motivo=rec.get('motivo', 'parseo_incompleto')))
            continue

        try:
            geom, attrs = G.build_feature(tipo, rec, item)
        except Exception as e:
            sin_georreferenciar.append(dict(cve=item['cve'], tipo=tipo,
                                             archivo=item['archivo'], motivo=str(e)))
            continue

        attrs['fecha'] = date_str
        attrs['edicion'] = str(edition)
        features_by_type.setdefault(tipo, []).append((geom, attrs))
        parsed_count += 1

    print(f"Georreferenciados: {parsed_count} / {len(manifest)}")

    date_compact = date_str.replace("-", "")[-4:] + date_str.replace("-", "")[2:4] + date_str.replace("-", "")[0:2]
    # date_str es DD-MM-AAAA -> queremos AAAAMMDD
    d, mth, y = date_str.split("-")
    date_compact = f"{y}{mth}{d}"

    os.makedirs("docs/data", exist_ok=True)
    gpkg_dated = f"docs/data/boletin_mineria_{date_compact}.gpkg"
    geojson_dated = f"docs/data/boletin_mineria_{date_compact}.geojson"
    gpkg_latest = "docs/data/boletin_mineria_latest.gpkg"
    geojson_latest = "docs/data/boletin_mineria_latest.geojson"

    total, all_rows = G.export_gpkg(features_by_type, gpkg_dated)
    print(f"GeoPackage: {total} poligonos -> {gpkg_dated}")

    if all_rows:
        import geopandas as gpd
        gdf_all = gpd.GeoDataFrame(all_rows, geometry='geometry', crs='EPSG:4326')
        gdf_all.to_file(geojson_dated, driver='GeoJSON')

    shutil.copyfile(gpkg_dated, gpkg_latest) if os.path.exists(gpkg_dated) else None
    if os.path.exists(geojson_dated):
        shutil.copyfile(geojson_dated, geojson_latest)

    M.build_map_html(all_rows, date_str, edition, "docs/index.html")
    dated_map = f"docs/data/mapa_{date_compact}.html"
    shutil.copyfile("docs/index.html", dated_map)

    # --- Capa historica acumulada: suma lo de hoy a lo ya acumulado hasta
    # ahora, deduplicando por CVE (si un CVE reaparece, gana la version mas
    # reciente). Se guarda aparte de los archivos "latest" (que son solo del
    # dia) para no perder el historico dia a dia. ---
    hist_gpkg = "docs/data/boletin_mineria_historico.gpkg"
    hist_geojson = "docs/data/boletin_mineria_historico.geojson"
    historico_total = 0
    historico_rows = list(all_rows)
    if os.path.exists(hist_geojson):
        import geopandas as gpd
        try:
            gdf_prev = gpd.read_file(hist_geojson)
            historico_rows = gdf_prev.to_dict('records') + historico_rows
        except Exception as e:
            print(f"AVISO: no se pudo leer el historico previo ({e}); se reconstruye desde cero.")

    if historico_rows:
        import geopandas as gpd
        dedup = {}
        for r in historico_rows:
            key = r.get('cve') or id(r)
            dedup[key] = r  # las filas de hoy van al final -> prevalecen si se repite el CVE
        historico_rows = list(dedup.values())

        historico_total = G.export_gpkg_from_records(historico_rows, hist_gpkg)
        gdf_hist = gpd.GeoDataFrame(historico_rows, geometry='geometry', crs='EPSG:4326')
        gdf_hist.to_file(hist_geojson, driver='GeoJSON')
        M.build_historico_html(historico_rows, "docs/historico.html")
        print(f"Historico acumulado: {historico_total} publicaciones en total -> {hist_gpkg}")

    report = {
        "fecha": date_str,
        "edicion": edition,
        "total_publicaciones": len(manifest),
        "georreferenciadas": parsed_count,
        "por_tipo": {t: len(f) for t, f in features_by_type.items()},
        "sin_georreferenciar": sin_georreferenciar,
        "historico_total_acumulado": historico_total,
    }
    with open(f"docs/data/reporte_{date_compact}.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open("docs/data/reporte_latest.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("\n=== Resumen ===")
    print(json.dumps({k: v for k, v in report.items() if k != "sin_georreferenciar"},
                      ensure_ascii=False, indent=1))
    if sin_georreferenciar:
        print(f"\n{len(sin_georreferenciar)} sin georreferenciar:")
        for s in sin_georreferenciar:
            print(" -", s['archivo'], "->", s['motivo'])

    # Limpieza: no versionamos los PDFs originales en git (pesan y no aportan
    # al repo; el link al PDF oficial queda en cada feature).
    shutil.rmtree(pdf_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
