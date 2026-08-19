#!/usr/bin/env python3
"""
Construye geometrias (poligonos) a partir de records ya parseados por
parser.py, y exporta un GeoPackage con una capa por tipo de publicacion.
"""
from shapely.geometry import Polygon
from pyproj import Transformer
from parser import epsg_for


def transform_pt(tf, norte, este):
    lon, lat = tf.transform(este, norte)
    return lon, lat


def build_rect_pedimento(tf, norte, este, lado_ns, lado_eo):
    hn, he = lado_ns / 2, lado_eo / 2
    corners_utm = [
        (norte + hn, este - he), (norte + hn, este + he),
        (norte - hn, este + he), (norte - hn, este - he),
    ]
    return Polygon([transform_pt(tf, n, e) for n, e in corners_utm])


def build_rect_manifestacion(tf, norte, este, lado_ns, lado_eo):
    # Punto de Interes = midpoint del lado NORTE (superior).
    he = lado_eo / 2
    corners_utm = [
        (norte, este - he), (norte, este + he),
        (norte - lado_ns, este + he), (norte - lado_ns, este - he),
    ]
    return Polygon([transform_pt(tf, n, e) for n, e in corners_utm])


def build_mensura(tf, vertices):
    return Polygon([transform_pt(tf, n, e) for n, e in vertices])


def build_feature(tipo, rec, meta):
    """rec: salida de parser.parse_pedimento/parse_manifestacion/parse_mensura.
    meta: dict con cve/region/provincia/nombre_solicitante/href del indice.
    Devuelve (geom, atributos) o levanta excepcion si no se puede construir."""
    datum = rec.get('datum', 'WGS84')
    huso = rec.get('huso', 19)
    hemis = rec.get('hemisferio', 'S')
    epsg = epsg_for(datum, huso, hemis)
    tf = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    if tipo == 'pedimento':
        geom = build_rect_pedimento(tf, rec['punto_medio_norte'], rec['punto_medio_este'],
                                     rec['lado_ns'], rec['lado_eo'])
    elif tipo == 'manifestacion':
        geom = build_rect_manifestacion(tf, rec['punto_interes_norte'], rec['punto_interes_este'],
                                         rec['lado_ns'], rec['lado_eo'])
    elif tipo in ('sentencia_exploracion', 'sentencia_explotacion'):
        # Misma construccion que manifestacion: la sentencia se resuelve sobre
        # el mismo Punto de Interes + lados Norte-Sur/Este-Oeste del expediente.
        geom = build_rect_manifestacion(tf, rec['punto_interes_norte'], rec['punto_interes_este'],
                                         rec['lado_ns'], rec['lado_eo'])
    elif tipo == 'mensura':
        if len(rec.get('vertices', [])) < 3:
            raise ValueError('menos de 3 vertices')
        geom = build_mensura(tf, rec['vertices'])
    else:
        raise ValueError(f'tipo no soportado: {tipo}')

    if not geom.is_valid or geom.area == 0:
        raise ValueError('geometria invalida o area cero')

    nombre, solicitante = None, None
    ns = meta.get('nombre_solicitante')
    if ns:
        if ' / ' in ns:
            nombre, solicitante = [x.strip() for x in ns.split(' / ', 1)]
        else:
            nombre = ns.strip()

    attrs = dict(
        cve=meta.get('cve'),
        nombre=nombre or meta.get('archivo'),
        solicitante=solicitante or rec.get('solicitante'),
        region=meta.get('region') or rec.get('region'),
        provincia=meta.get('provincia') or rec.get('provincia'),
        comuna=rec.get('comuna'),
        superficie_ha=rec.get('superficie_ha'),
        datum_original=datum,
        huso=huso,
        epsg_origen=epsg,
        tipo_publicacion=tipo,
        fuente_pdf=meta.get('href'),
        archivo=meta.get('archivo'),
    )
    return geom, attrs


LAYER_NAMES = {
    'pedimento': 'pedimentos',
    'manifestacion': 'manifestaciones',
    'mensura': 'mensuras',
    'sentencia_exploracion': 'sentencias_exploracion',
    'sentencia_explotacion': 'sentencias_explotacion',
}


def export_gpkg(features_by_type, out_path):
    """features_by_type: {tipo: [ (geom, attrs), ... ]}. Escribe un .gpkg con
    una capa por tipo que tenga al menos 1 feature. Devuelve el total exportado
    y la lista plana de filas (dicts con 'geometry' + atributos)."""
    import os
    import geopandas as gpd

    if os.path.exists(out_path):
        os.remove(out_path)

    total = 0
    all_rows = []
    for tipo, feats in features_by_type.items():
        if not feats:
            continue
        rows = [dict(**attrs, geometry=geom) for geom, attrs in feats]
        gdf = gpd.GeoDataFrame(rows, geometry='geometry', crs='EPSG:4326')
        gdf.to_file(out_path, layer=LAYER_NAMES.get(tipo, tipo), driver='GPKG')
        total += len(gdf)
        all_rows.extend(rows)

    return total, all_rows


def export_gpkg_from_records(rows, out_path):
    """rows: lista plana de dicts, cada uno con 'geometry' (shapely) + atributos
    (incluyendo 'tipo_publicacion'). Agrupa por tipo y escribe un .gpkg
    multi-capa, igual que export_gpkg pero partiendo de filas ya planas (usado
    para la capa histórica acumulada). Devuelve el total exportado."""
    import os
    import geopandas as gpd

    if os.path.exists(out_path):
        os.remove(out_path)

    by_type = {}
    for r in rows:
        by_type.setdefault(r.get('tipo_publicacion', 'sin_tipo'), []).append(r)

    total = 0
    for tipo, recs in by_type.items():
        if not recs:
            continue
        gdf = gpd.GeoDataFrame(recs, geometry='geometry', crs='EPSG:4326')
        gdf.to_file(out_path, layer=LAYER_NAMES.get(tipo, tipo), driver='GPKG')
        total += len(gdf)

    return total
