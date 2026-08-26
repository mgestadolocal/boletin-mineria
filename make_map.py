#!/usr/bin/env python3
"""Genera mapas Leaflet auto-contenidos (HTML) a partir de una lista de
filas (dicts con geometry shapely + atributos): uno para la edición del día
(build_map_html) y otro para la capa histórica acumulada (build_historico_html)."""
import json
from shapely.geometry import mapping

COLORS = {
    'pedimento': '#2563eb',
    'manifestacion': '#16a34a',
    'mensura': '#dc2626',
    'sentencia_exploracion': '#9333ea',
    'sentencia_explotacion': '#ea580c',
}

LABELS = {
    'pedimento': 'Pedimentos',
    'manifestacion': 'Manifestaciones',
    'mensura': 'Mensuras',
    'sentencia_exploracion': 'Sentencias de Exploración',
    'sentencia_explotacion': 'Sentencias de Explotación',
}


def rows_to_geojson(rows):
    features = []
    for row in rows:
        geom = row['geometry']
        props = {k: v for k, v in row.items() if k != 'geometry'}
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
    return {"type": "FeatureCollection", "features": features}


_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
<style>
  html, body { margin:0; padding:0; height:100%; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
  #map { position:absolute; top:0; bottom:0; left:0; right:0; }
  .panel {
    position:absolute; top:8px; right:8px; z-index:1000; background:white;
    padding:9px 11px; border-radius:6px; box-shadow:0 2px 10px rgba(0,0,0,.25);
    max-width:158px; font-size:10.5px; line-height:1.35;
  }
  .panel h3 { margin:0 0 4px 0; font-size:11.5px; }
  .legend-item { display:flex; align-items:center; margin:2px 0; }
  .swatch { width:9px; height:9px; margin-right:5px; border-radius:2px; flex-shrink:0; }
  .stat { color:#555; }
  .popup-title { font-weight:600; margin-bottom:4px; }
  .popup-row { margin:2px 0; }
  a.pdf-link { color:#1a73e8; }
  .dl-row { margin-top:6px; }
  .dl-row a { display:inline-block; margin-right:6px; font-size:9.5px; color:#1a73e8; }
  .nav-row { margin-top:5px; font-size:9.5px; }
  .nav-row a { color:#1a73e8; }
  /* Cuando el mapa vive embebido en un iframe (la vista previa de la
     landing), el panel pierde los links de descarga/nav -- ahi solo
     importa mostrar el mapa y una leyenda legible; para los links esta el
     boton "abrir a pantalla completa" de la landing, que carga esta misma
     pagina sin iframe y con el panel completo. */
  .panel.panel--compact { max-width:146px; padding:8px 10px; }
  .panel.panel--compact .dl-row, .panel.panel--compact .nav-row { display:none; }
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h3>Boletin Oficial de Mineria</h3>
  <div class="stat">__SUBTITLE__</div>
  <div style="margin-top:10px;">
    __LEGEND__
  </div>
  <div class="dl-row">
    <a href="__GPKG_NAME__" download>Descargar GeoPackage</a>
    <a href="__GEOJSON_NAME__" download>Descargar GeoJSON</a>
  </div>
  <div class="nav-row">__NAV__</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const data = __GEOJSON__;

const map = L.map('map', { zoomControl: true });

// Embebido en un iframe (preview de la landing) vs. pagina abierta directo:
// el panel flotante se achica bastante mas cuando esta embebido, para que
// el mapa mismo se alcance a ver en un recuadro chico.
const embedded = window.self !== window.top;
if (embedded) document.querySelector('.panel').classList.add('panel--compact');

const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap', maxZoom: 19
});
const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  attribution: 'Tiles &copy; Esri', maxZoom: 19
});
osm.addTo(map);
L.control.layers({ "Calles": osm, "Satelite": sat }).addTo(map);

const colors = __COLORS__;

if (data.features.length) {
  const layer = L.geoJSON(data, {
    style: function(feature) {
      const c = colors[feature.properties.tipo_publicacion] || '#888';
      return { color: c, weight: 2, fillColor: c, fillOpacity: 0.25 };
    },
    onEachFeature: function(feature, lyr) {
      const p = feature.properties;
      const html = `
        <div class="popup-title">${p.nombre || '(sin nombre)'}</div>
        <div class="popup-row"><b>Tipo:</b> ${p.tipo_publicacion}</div>
        <div class="popup-row"><b>CVE:</b> ${p.cve}</div>
        <div class="popup-row"><b>Fecha publicacion:</b> ${p.fecha || '-'}</div>
        <div class="popup-row"><b>Solicitante:</b> ${p.solicitante || '-'}</div>
        <div class="popup-row"><b>Region:</b> ${p.region || '-'}</div>
        <div class="popup-row"><b>Provincia:</b> ${p.provincia || '-'}</div>
        <div class="popup-row"><b>Superficie:</b> ${p.superficie_ha ? p.superficie_ha + ' ha' : '-'}</div>
        <div class="popup-row"><b>Datum origen:</b> ${p.datum_original} / Huso ${p.huso}</div>
        <div class="popup-row"><a class="pdf-link" href="${p.fuente_pdf}" target="_blank">Ver PDF original</a></div>
      `;
      lyr.bindPopup(html);
    }
  }).addTo(map);
  // El encuadre inicial es un cajon fijo (calibrado a mano contra una
  // captura de referencia: norte de Chile con contexto del NO argentino
  // de fondo) en vez de ajustarse a los limites exactos de ESTE dia. Un
  // caso aislado en un extremo (ej. una mensura puntual en Magallanes,
  // mucho mas al sur que el resto) no debe forzar un zoom abierto que
  // empequeñezca todo lo demas -- esos puntos siguen en el mapa (clic,
  // popup), solo no entran en el encuadre inicial.
  const CHILE_MINERO = [[-40.5, -84.0], [-22.3, -55.0]];
  // Padding asimetrico: reserva espacio en la esquina superior derecha para
  // el panel/leyenda flotante, asi el zoom no deja el territorio ni los
  // datos tapados detras del panel.
  map.fitBounds(CHILE_MINERO, { paddingTopLeft: [24, 24], paddingBottomRight: [embedded ? 170 : 190, 24] });
} else {
  map.setView([-33.45, -70.65], 5);
}
</script>
</body>
</html>
"""


def _render(rows, title, subtitle, gpkg_name, geojson_name, nav_html, out_path):
    geojson = rows_to_geojson(rows)
    counts = {}
    for row in rows:
        counts[row['tipo_publicacion']] = counts.get(row['tipo_publicacion'], 0) + 1

    legend_items = "\n".join(
        f'<div class="legend-item"><span class="swatch" style="background:{COLORS.get(t, "#888")}"></span> '
        f'{LABELS.get(t, t)} ({n})</div>'
        for t, n in counts.items()
    )

    html = (_BASE_TEMPLATE
            .replace('__GEOJSON__', json.dumps(geojson, ensure_ascii=False))
            .replace('__COLORS__', json.dumps(COLORS))
            .replace('__LEGEND__', legend_items)
            .replace('__TITLE__', title)
            .replace('__SUBTITLE__', subtitle)
            .replace('__GPKG_NAME__', gpkg_name)
            .replace('__GEOJSON_NAME__', geojson_name)
            .replace('__NAV__', nav_html))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


def build_map_html(rows, fecha, edicion, out_path):
    """Mapa de la edición del día (solo lo publicado esa fecha)."""
    title = f"Boletin Oficial de Mineria - {fecha} - Edicion {edicion}"
    subtitle = f"Edicion {edicion} &middot; {fecha}"
    nav = '<a href="historico.html">Ver historico completo (todas las fechas) &rarr;</a>'
    _render(rows, title, subtitle, "data/boletin_mineria_latest.gpkg",
            "data/boletin_mineria_latest.geojson", nav, out_path)


def build_historico_html(rows, out_path):
    """Mapa acumulado con todas las publicaciones georreferenciadas hasta la
    fecha (todas las corridas diarias combinadas, sin duplicar por CVE)."""
    fechas = sorted({r.get('fecha') for r in rows if r.get('fecha')})
    rango = f"{fechas[0]} a {fechas[-1]}" if fechas else "sin datos"
    title = "Boletin Oficial de Mineria - Historico completo"
    subtitle = f"{len(rows)} publicaciones acumuladas &middot; {rango}"
    nav = '<a href="index.html">Ver solo la edicion de hoy &rarr;</a>'
    _render(rows, title, subtitle, "data/boletin_mineria_historico.gpkg",
            "data/boletin_mineria_historico.geojson", nav, out_path)
