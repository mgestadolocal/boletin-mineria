# Boletín Oficial de Minería — georreferenciación diaria

Descarga automáticamente cada día los Pedimentos Mineros, Manifestaciones Mineras y Solicitudes/Oposición de Mensura publicados en el [Boletín Oficial de Minería de Chile](https://www.boletinoficialdemineria.cl), los georreferencia según las reglas del Código de Minería chileno, y publica los resultados como GeoPackage/GeoJSON descargables más un mapa interactivo — todo esto corre en GitHub Actions, **sin depender de que ningún computador esté encendido**.

**Mapa y descargas:** `https://mgestadolocal.github.io/boletin-mineria/` (se activa tras el primer deploy de GitHub Pages — ver abajo).

## Cómo funciona

1. `scraper.py` — visita la portada del día en boletinoficialdemineria.cl con un navegador Chromium real (headless, vía Playwright), descubre qué categorías tienen contenido ese día (Pedimentos, Manifestaciones, Solicitudes/Oposición de Mensura, Sentencias de Exploración, Sentencias de Explotación), y descarga cada PDF (desde diariooficial.interior.gob.cl) reutilizando la sesión/cookies del navegador. **Por qué un navegador y no un simple `requests.get`**: el sitio boletinoficialdemineria.cl está protegido por un challenge anti-bot tipo F5 BIG-IP/TrafficShield — un cliente HTTP sin motor JS recibe solo la página del challenge (vacía), nunca el contenido real. Un navegador real lo resuelve automáticamente, igual que en Chrome normal. El navegador corre dentro del runner de GitHub Actions, así que esto sigue sin depender de tu computador.
2. `parser.py` — extrae las coordenadas UTM y dimensiones de cada documento con expresiones regulares tolerantes a la variación de redacción entre estudios jurídicos/geomensores.
3. `build_geometry.py` — construye el polígono de cada publicación (rectángulo centrado en el Punto Medio para pedimentos, rectángulo con Punto de Interés en el borde norte para manifestaciones, polígono directo desde la tabla de vértices para mensuras) y reproyecta a WGS84 detectando el datum de origen (WGS84 o PSAD56 "Datum La Canoa 1956") por documento.
4. `make_map.py` — genera un mapa Leaflet autocontenido.
5. `run_pipeline.py` — orquesta todo lo anterior y deja los resultados en `docs/` (servido por GitHub Pages).
6. `.github/workflows/daily.yml` — corre `run_pipeline.py` todos los días a las 13:00 hora de Chile y commitea los resultados automáticamente.

## Estructura de salida (`docs/`)

- `index.html` — mapa del día más reciente (esto es lo que sirve GitHub Pages en la raíz).
- `data/boletin_mineria_latest.gpkg` / `.geojson` — última corrida (para descargar y abrir en QGIS).
- `data/boletin_mineria_AAAAMMDD.gpkg` / `.geojson` — histórico por fecha.
- `data/reporte_latest.json` / `reporte_AAAAMMDD.json` — resumen de la corrida: cuántas publicaciones de cada tipo, y cuáles quedaron sin georreferenciar (con motivo).

## Limitaciones conocidas

- **El primer intento del scraper usaba `requests` puro y falló en producción**: boletinoficialdemineria.cl tiene un challenge anti-bot JS (F5/TrafficShield) que un cliente HTTP sin navegador no puede resolver, así que solo recibía una página de challenge vacía (0 secciones detectadas). Se corrigió cambiando a Playwright (Chromium headless real) para toda la navegación — ver arriba. Si en el futuro el sitio cambia su protección anti-bot y el scraper vuelve a devolver 0 secciones, revisar el HTML crudo con `DEBUG_SCRAPER=true` (input del workflow) antes de asumir que simplemente no hay publicaciones ese día.
- **Sentencias de Exploración / Explotación**: el sitio rara vez publica contenido en estas categorías (no se encontró ningún ejemplo real durante el desarrollo). El pipeline las detecta si aparecen, descarga el PDF, pero **no las georreferencia automáticamente todavía** — quedan listadas en `sin_georreferenciar` del reporte del día para revisión manual, porque no existe aún una regla de geometría verificada para ese tipo de documento (a diferencia de pedimentos/manifestaciones/mensuras, que sí siguen un formato geométrico estandarizado por el Código de Minería). Cuando aparezca un caso real, hay que revisar el PDF, derivar la regla y agregar la función correspondiente a `parser.py` + `build_geometry.py`.
- Un documento puntual puede fallar el parseo si usa una redacción muy distinta a las ~15 plantillas ya cubiertas — esos casos también quedan en `sin_georreferenciar` con el motivo, en vez de forzar una geometría incorrecta.
- El horario del cron está codificado en UTC (17:00 UTC = 13:00 en Chile continental en horario de invierno). Chile cambia de huso horario por horario de verano; si en algún momento del año se nota un desfase de 1 hora, hay que ajustar el cron en `.github/workflows/daily.yml`.

## Correr manualmente

Desde la pestaña "Actions" del repo → "Boletin Mineria - georreferenciacion diaria" → "Run workflow". También corre localmente con:

```bash
pip install -r requirements.txt
python run_pipeline.py 17-08-2026   # o sin fecha para usar la fecha de hoy
```

## Activar GitHub Pages (una sola vez)

Settings → Pages → Source: "Deploy from a branch" → Branch: `main` / carpeta `/docs`.
