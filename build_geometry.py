#!/usr/bin/env python3
"""
Construye geometrias (poligonos) a partir de records ya parseados por
parser.py, y exporta un GeoPackage con una capa por tipo de publicacion.
"""
from shapely.geometry import Polygon
from pyproj import Transformer
from parser import epsg_for

# Limite ESTE aproximado de Chile continental (frontera con Argentina) por
# banda de latitud (grado entero). Uso: cuando un documento no declara el
# Huso/Zona UTM, o lo declara mal (ver build_feature mas abajo), hay que
# elegir entre las dos zonas UTM que cruzan Chile continental (18 o 19);
# esta tabla sirve de heuristica de desempate ("cual de las dos cae del
# lado chileno de la cordillera") -- no pretende ser una frontera exacta.
#
# Calibrada EMPIRICAMENTE el 27-08-2026 contra las ~3500 publicaciones ya
# georreferenciadas y confirmadas correctas hasta esa fecha (maxima
# longitud real observada por banda de latitud + margen de seguridad de
# 1.5 grados) -- una tabla a mano (primera version de este fix) resultaba
# demasiado angosta y rechazaba ~45 puntos reales del norte de Chile
# (Antofagasta, donde el territorio se acerca bastante a la frontera). Si
# el histórico crece mucho mas hacia el oriente en el futuro (nuevas
# publicaciones reales cerca del limite), reconsiderar recalibrar.
_CHILE_LAT_LON_ESTE = [
    (-18, -67.79), (-19, -67.52), (-20, -67.35), (-21, -66.75),
    (-22, -66.62), (-23, -65.58), (-24, -65.83), (-25, -67.11),
    (-26, -67.15), (-27, -66.97), (-28, -67.56), (-29, -68.17),
    (-30, -68.44), (-31, -68.80), (-32, -68.76), (-33, -68.51),
    (-34, -68.40), (-35, -68.92), (-36, -70.27), (-37, -70.53),
    (-38, -70.71), (-39, -70.94), (-40, -71.07), (-41, -71.64),
    (-42, -71.27), (-43, -70.91), (-44, -70.54), (-45, -70.18),
    (-46, -69.71), (-47, -69.25), (-48, -68.78), (-49, -68.32),
    (-50, -67.85), (-51, -67.39), (-52, -66.93), (-53, -68.55),
    (-54, -70.52),
]


def _limite_este_chile(lat):
    pts = _CHILE_LAT_LON_ESTE
    if lat >= pts[0][0]:
        return pts[0][1]
    if lat <= pts[-1][0]:
        return pts[-1][1]
    for (lat0, lon0), (lat1, lon1) in zip(pts, pts[1:]):
        if lat1 <= lat <= lat0:
            frac = (lat0 - lat) / (lat0 - lat1)
            return lon0 + frac * (lon1 - lon0)
    return pts[-1][1]


def _parece_chile(lon, lat):
    """True si (lon,lat) cae del lado chileno del limite aproximado (con un
    margen de tolerancia de 0.3 grados)."""
    return lon <= _limite_este_chile(lat) + 0.3


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


def _build_geom(tipo, rec, tf):
    if tipo == 'pedimento':
        return build_rect_pedimento(tf, rec['punto_medio_norte'], rec['punto_medio_este'],
                                     rec['lado_ns'], rec['lado_eo'])
    elif tipo in ('manifestacion', 'sentencia_exploracion', 'sentencia_explotacion'):
        # Sentencia se resuelve igual que manifestacion: mismo Punto de
        # Interes + lados Norte-Sur/Este-Oeste del expediente.
        return build_rect_manifestacion(tf, rec['punto_interes_norte'], rec['punto_interes_este'],
                                         rec['lado_ns'], rec['lado_eo'])
    elif tipo == 'mensura':
        if len(rec.get('vertices', [])) < 3:
            raise ValueError('menos de 3 vertices')
        return build_mensura(tf, rec['vertices'])
    else:
        raise ValueError(f'tipo no soportado: {tipo}')


def build_feature(tipo, rec, meta):
    """rec: salida de parser.parse_pedimento/parse_manifestacion/parse_mensura.
    meta: dict con cve/region/provincia/nombre_solicitante/href del indice.
    Devuelve (geom, atributos) o levanta excepcion si no se puede construir."""
    datum = rec.get('datum', 'WGS84')
    huso_declarado = rec.get('huso')
    hemis = rec.get('hemisferio', 'S')

    # Chile continental cruza las zonas UTM 18 y 19. Se prueba primero el
    # huso declarado por el documento (o 19 si no declaraba ninguno -- ver
    # parser.find_datum), y solo si el resultado NO parece caer en Chile se
    # prueba la otra zona como respaldo. Dos motivos distintos detectados el
    # 27-08-2026 para necesitar esto:
    # (a) el documento no declara Huso/Zona en absoluto (huso_declarado is
    #     None) -- no hay nada de que fiarse, hay que decidir por geografia;
    # (b) el documento SI declara un huso, pero es inconsistente con sus
    #     propias coordenadas (CVEs 2859100/2859101: dicen "Huso 19" pero la
    #     tabla de vertices esta en zona 18 -- error del documento original,
    #     no de nuestro parseo). Confiar ciegamente en la etiqueta declarada
    #     seguia poniendo estos casos en Argentina.
    orden = [huso_declarado if huso_declarado is not None else 19, 18]
    if orden[0] == orden[1]:
        orden = orden[:1]

    candidatos = []
    for h in orden:
        try:
            epsg_h = epsg_for(datum, h, hemis)
            tf_h = Transformer.from_crs(f"EPSG:{epsg_h}", "EPSG:4326", always_xy=True)
            geom_h = _build_geom(tipo, rec, tf_h)
            candidatos.append((h, geom_h))
        except Exception:
            continue
    if not candidatos:
        raise ValueError('no se pudo construir geometria con ninguna zona candidata')
    huso, geom = next(
        (par for par in candidatos if _parece_chile(par[1].centroid.x, par[1].centroid.y)),
        candidatos[0],  # ninguna parece Chile -- nos quedamos con la primera igual
    )

    epsg = epsg_for(datum, huso, hemis)

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
