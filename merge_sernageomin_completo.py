#!/usr/bin/env python3
"""
Fusiona el catastro OFICIAL completo de SERNAGEOMIN (104.147 concesiones,
docs/data/sernageomin_concesiones_completo.gpkg) dentro de la capa historica
acumulada (docs/data/boletin_mineria_historico.*), reemplazando la muestra
filtrada (2025-2026, 3.950 registros) que se habia fusionado antes a mano en
el commit 3d6cc3c.

Uso: python merge_sernageomin_completo.py
(Sin argumentos -- opera siempre sobre las rutas fijas del repo. Pensado para
correrse de nuevo si algun dia llega un export mas reciente de SERNAGEOMIN:
basta con reemplazar sernageomin_concesiones_completo.gpkg y volver a correr
este script, que saca la version vieja (por prefijo de cve) antes de meter la
nueva.)

Deja el archivo descargable (historico.gpkg/.geojson) con el catastro
COMPLETO -- las 104.147 concesiones, sin filtrar. El mapa interactivo
(historico.html) sigue mostrando solo una ventana reciente de sentencias
(ver make_map.filter_for_map) para no reventar el navegador con 100k+
poligonos; el resto queda disponible completo solo para quien lo descargue.

Decisiones de calidad de datos (ver docs/data en el propio dato, campo
fuente_dato, para que quede trazable registro a registro):
  - ~21.500 registros (20%) no tienen ANO_INSCRIPCION en el catastro -> se
    deja fecha=None en vez de inventar una, y fuente_dato lo marca explicito
    como "fecha desconocida" (distinto del resto, que sí declara "solo se
    conoce el ano").
  - ~937 registros (0.9%) tienen geometria tecnicamente invalida
    (autointersecciones menores, comun en catastros reales) -> se dejan tal
    cual, no se "reparan" alterando la forma sin que se pida explicitamente.
"""
from datetime import datetime

import geopandas as gpd
import pandas as pd

import build_geometry as G
import make_map as M

COMPLETO_PATH = "docs/data/sernageomin_concesiones_completo.gpkg"
HIST_GEOJSON = "docs/data/boletin_mineria_historico.geojson"
HIST_GPKG = "docs/data/boletin_mineria_historico.gpkg"
HIST_HTML = "docs/historico.html"

TIPO_MAP = {"EXPLOTACION": "sentencia_explotacion", "EXPLORACION": "sentencia_exploracion"}

FUENTE_CON_ANO = (
    "Catastro SERNAGEOMIN (fecha aproximada -- solo se conoce el ano de "
    "inscripcion). Catastro completo (104.147 registros) disponible para descarga."
)
FUENTE_SIN_ANO = (
    "Catastro SERNAGEOMIN (sin ano de inscripcion registrado en el catastro "
    "-- fecha desconocida). Catastro completo (104.147 registros) disponible "
    "para descarga."
)


def build_rows_from_completo(gdf):
    """Traduce el esquema crudo del catastro SERNAGEOMIN al esquema comun de
    boletin_mineria_historico (mismos campos que usan pedimentos/
    manifestaciones/mensuras/sentencias reales del Boletin)."""
    ano = gdf["ANO_INSCRIPCION"]
    tiene_ano = ano.notna()
    fecha = ano.apply(lambda a: f"01-01-{int(a)}" if pd.notna(a) else None)
    fuente = tiene_ano.map({True: FUENTE_CON_ANO, False: FUENTE_SIN_ANO})
    tipo = gdf["TIPO_CONCESION"].map(TIPO_MAP)

    out = gpd.GeoDataFrame(
        {
            "cve": "SNGM-" + gdf["ID_CONCESION"].astype(str),
            "nombre": gdf["NOMBRE"],
            "solicitante": gdf["TITULAR_NOMBRE"],
            "region": None,
            "provincia": gdf["COMUNA"],
            "comuna": gdf["COMUNA"],
            "superficie_ha": gdf["HECTAREAS"],
            "datum_original": "WGS84",
            "huso": gdf["HUSO"],
            "epsg_origen": None,
            "tipo_publicacion": tipo,
            "fuente_pdf": None,
            "archivo": None,
            "fecha": fecha,
            "edicion": None,
            "fuente_dato": fuente,
            "geometry": gdf.geometry,
        },
        crs="EPSG:4326",
    )

    sin_tipo = out["tipo_publicacion"].isna().sum()
    if sin_tipo:
        print(f"AVISO: {sin_tipo} registros con TIPO_CONCESION desconocido, se descartan")
        out = out[out["tipo_publicacion"].notna()]

    return out.to_dict("records")


def main():
    print("Leyendo catastro completo...")
    completo = gpd.read_file(COMPLETO_PATH)
    print(f"  {len(completo)} registros")

    print("Leyendo historico actual...")
    hist = gpd.read_file(HIST_GEOJSON)
    hist_rows = hist.to_dict("records")
    print(f"  {len(hist_rows)} registros")

    antes = len(hist_rows)
    hist_rows = [r for r in hist_rows if not str(r.get("cve", "")).startswith("SNGM-")]
    print(f"Quitados {antes - len(hist_rows)} registros SNGM- (muestra filtrada anterior, 2025-2026)")

    nuevas = build_rows_from_completo(completo)
    print(f"Construidas {len(nuevas)} filas nuevas desde el catastro completo")

    todas = hist_rows + nuevas
    dedup = {}
    for r in todas:
        key = r.get("cve") or id(r)
        dedup[key] = r
    todas = list(dedup.values())
    print(f"Total historico (dedup por cve): {len(todas)}")

    total = G.export_gpkg_from_records(todas, HIST_GPKG)
    print(f"GPKG escrito: {total} registros -> {HIST_GPKG}")

    gdf_out = gpd.GeoDataFrame(todas, geometry="geometry", crs="EPSG:4326")
    gdf_out.to_file(HIST_GEOJSON, driver="GeoJSON")
    print(f"GeoJSON escrito -> {HIST_GEOJSON}")

    map_rows = M.filter_for_map(todas)
    print(f"Filas para el mapa interactivo (ventana reciente): {len(map_rows)}")
    M.build_historico_html(map_rows, HIST_HTML, stats_rows=todas)
    print(f"Mapa regenerado -> {HIST_HTML}")


if __name__ == "__main__":
    main()
