# Globo decorativo 3D en el hero — diseño

## Contexto

`landing.html` fusionó el mapa Leaflet real dentro del hero unas cuantas
iteraciones atrás (ver historial de commits recientes: "mapa en vivo dentro
del hero"). El usuario ahora quiere que el hero abra con un momento más
"futurista y dinámico" — un globo 3D con curvatura real, tipo la vista de
Google Earth/ISS — en vez de (o compitiendo con) el mapa plano funcional.

Se comparó en vivo, vía la herramienta de brainstorming visual, contra 3
enfoques (globo 3D real con MapLibre GL, recorte circular + halo en CSS
sobre el Leaflet existente, globo decorativo hecho a mano con Three.js) y
2 estilos de superficie (foto satelital real vs. dot-matrix/wireframe). El
usuario eligió explícitamente **globo decorativo con Three.js + foto
satelital real** en ambas rondas.

## Decisiones validadas

1. **Enfoque:** globo decorativo (Three.js), separado del mapa funcional
   real. No reemplaza la lógica/datos del mapa — es una pieza visual nueva.
2. **Superficie:** foto satelital real. Ya descargada: NASA Blue Marble
   2002, proyección equirectangular, dominio público (NASA), reproyectada
   por el usuario de Wikimedia "Mdf" —
   `docs/images/earth-equirectangular.jpg` (redimensionada a 1024×512,
   ~100KB, desde el original 2048×1025 de Wikimedia Commons). Crédito a
   agregar junto a las demás atribuciones de fotos de la landing.
3. **Ubicación:** el globo **reemplaza** `.hero__map` dentro del hero. El
   mapa Leaflet real (mismo zoom/leyenda/popups ya calibrados, sin tocar
   `make_map.py` ni el encuadre) se muda a su propia `<section>` más abajo
   en el scroll — recupera la estructura que tuvo antes de la fusión con
   el hero, con su propio `section__head` y el CTA "Ver mapa de hoy ↓"
   apuntando ahí (`#mapa-hoy`).

## Arquitectura

- **Nueva dependencia:** Three.js vía CDN (`<script type="importmap">` +
  `three` desde `unpkg.com` o `cdn.jsdelivr.net`), mismo patrón que ya usa
  Leaflet (CDN, sin build step — restricción dura del proyecto).
- **Nuevo componente HTML:** `.hero__globe` reemplaza `.hero__map` como
  segunda columna del hero. Contiene un `<canvas id="hero-globe-canvas">`.
- **Escena Three.js:**
  - `THREE.SphereGeometry` + `MeshPhongMaterial`/`MeshStandardMaterial`
    con la textura `earth-equirectangular.jpg`.
  - Una luz direccional simple (simula el sol) + luz ambiental tenue.
  - Rotación automática lenta sobre el eje Y (ambient, no interactiva —
    sin drag/zoom, es decorativo, no compite con el mapa real).
  - Un marcador (pequeño `THREE.Mesh` esférico con glow, o sprite) en la
    posición real de Chile (~-33.5 lat, -70.6 lon, convertida a
    coordenadas 3D estándar de la esfera) — conecta el globo con el dato
    real sin necesitar toda la capa de publicaciones ahí.
- **Atmósfera y estrellas: CSS, no shader.** Igual que en el mockup
  validado — un halo (`box-shadow`/`radial-gradient`) alrededor del
  contenedor circular del canvas, y un fondo de estrellas vía
  `radial-gradient` puntual sobre `.hero__globe`. Evita la complejidad de
  un shader GLSL custom para un efecto que el CSS ya resuelve bien.
- **Casos límite:**
  - **Sin WebGL:** feature-detect (`canvas.getContext('webgl2') ||
    getContext('webgl')`); si falla, ocultar el canvas y mostrar
    `earth-equirectangular.jpg` como `<img>` estática de respaldo (mismo
    halo/estrellas alrededor vía CSS, sin rotación).
  - **`prefers-reduced-motion: reduce`:** no se inicia el loop de
    rotación automática — el globo queda estático en su orientación
    inicial (Chile visible, orientado hacia el observador).
  - **Sin JS:** `<noscript>` con la misma imagen estática de respaldo,
    para no dejar el hero vacío — la promesa "funciona sin JS" del
    proyecto se degrada con gracia (el efecto 3D en sí es inherentemente
    JS-dependiente, pero el hero nunca queda en blanco).

## Fuera de alcance

- El globo no es interactivo (sin popups, sin click, sin drag/zoom).
- No se toca `make_map.py`, el encuadre (`CHILE_MINERO`), la leyenda ni
  los datos del mapa real — solo cambia su ubicación en el documento.
- No se cambia copy, cifras ni atribuciones existentes (solo se agrega
  el crédito de la nueva foto satelital).

## Testing

- Revisión visual manual vía servidor local (sin navegador conectado en
  esta sesión de momento).
- Verificación de sintaxis JS (`node --check`) y balance de tags HTML,
  como en las iteraciones anteriores.
- Confirmar que el fallback sin-WebGL y `prefers-reduced-motion` se
  puedan revisar leyendo el código (no hay forma de emular "sin WebGL"
  fácilmente en revisión manual, así que se revisa por lectura).
