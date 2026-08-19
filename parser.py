#!/usr/bin/env python3
"""
Parser unificado para Pedimentos Mineros, Manifestaciones Mineras y
Solicitudes de Mensura publicados en el Boletín Oficial de Minería (Chile).

Entrada: all_texts.json  { filename: raw_pdf_text }
Salida:  parsed_final.json  { filename: {tipo, ...campos...} }
"""
import json, re, sys

F = re.IGNORECASE

def norm(t):
    return re.sub(r'\s+', ' ', t)

NUM = r'(\d[\d\.,]*)'
UNIT = r'(?:metros|mts?\.?|m\.?)'

def clean_num(s):
    s = s.strip()
    s = s.strip('.,')
    s = s.replace('.', '').replace(',', '.')
    return float(s)

# ---------------------------------------------------------------------------
# Datum / Huso detection
# ---------------------------------------------------------------------------
def find_datum(t):
    if re.search(r'PSAD\s*56|Provisorio.{0,15}Canoa|La\s+Canoa|el\s*ipsoide\s+Internacional\s+de\s+1924', t, F):
        datum = 'PSAD56'
    elif re.search(r'WGS\s*-?\s*84|SIRGAS', t, F):
        datum = 'WGS84'
    else:
        datum = 'WGS84'  # default asumido
    # Bug detectado el 19-08-2026: 7 mensuras de un mismo estudio (Valdivia,
    # Huso 18) salieron georreferenciadas en Argentina porque el regex solo
    # buscaba la palabra "Zona" -- estos documentos dicen "Huso 18 Sur", no
    # "Zona 18 Sur". Sin matchear nada, huso caia al default (19), corriendo
    # la longitud ~6 grados al este del valor real. Distintos estudios
    # juridicos usan una u otra palabra para lo mismo, asi que hay que
    # aceptar ambas.
    huso_m = re.search(r'(?:Huso|[Zz]ona)\s*(\d{1,2})\s*(?:S|Sur|N|Norte)?', t, F)
    huso = int(huso_m.group(1)) if huso_m else 19
    hemis_m = re.search(r'(?:Huso|[Zz]ona)\s*\d{1,2}\s*(S|Sur|N|Norte)', t, F)
    hemis = 'S'
    if hemis_m and hemis_m.group(1).upper().startswith('N'):
        hemis = 'N'
    return datum, huso, hemis

def epsg_for(datum, huso, hemis):
    if datum == 'PSAD56':
        # PSAD56 / UTM zone 19S = EPSG:24879 (only common zones defined; fallback to WGS84 equivalent)
        table = {18: 24878, 19: 24879, 20: 24880}
        return table.get(huso, 24879)
    else:
        base = 32700 if hemis == 'S' else 32600
        return base + huso

# ---------------------------------------------------------------------------
# Coordinate pair finder (Punto Medio / Punto de Interés)
# ---------------------------------------------------------------------------
NUM_ONLY = re.compile(NUM)

def _label_num_near(seg, label_pat, min_val=None, max_val=None, window=35):
    """Busca la primera ocurrencia de label_pat y devuelve el numero mas
    cercano (antes o despues) dentro de una ventana de caracteres, filtrado por rango.
    No exige que el numero vaya seguido de la unidad 'metros' (a menudo se omite
    en el segundo numero de un par Norte/Este)."""
    for lm in re.finditer(label_pat, seg, F):
        lo, hi = max(0, lm.start() - window), min(len(seg), lm.end() + window)
        local = seg[lo:hi]
        base = lm.start() - lo
        cands = []
        for nm in NUM_ONLY.finditer(local):
            try:
                v = clean_num(nm.group(1))
            except ValueError:
                continue
            if min_val is not None and v < min_val:
                continue
            if max_val is not None and v > max_val:
                continue
            mid = (nm.start() + nm.end()) / 2
            cands.append((abs(mid - base), v))
        if cands:
            cands.sort()
            return cands[0][1]
    return None

def find_first_coord_pair(t):
    """Devuelve (norte, este) del primer par de coordenadas UTM Norte/Este que aparece,
    usando cercania de numero a la etiqueta Norte/Este (soporta cualquier orden/puntuacion)
    y desambigua por rango de magnitud tipico de coordenadas UTM en Chile."""
    anchor = re.search(r'punto\s+medio|punto\s+de\s+inter[eé]s|coordenadas', t, F)
    start = anchor.start() if anchor else 0
    seg = t[start:start + 700]
    norte = _label_num_near(seg, r'\bNORTE\b', min_val=1_000_000, max_val=9_000_000)
    este = _label_num_near(seg, r'\bEst(?:e|os)\b', min_val=50_000, max_val=999_999)
    if norte is not None and este is not None:
        return norte, este
    # fallback: buscar en todo el documento
    norte = norte or _label_num_near(t, r'\bNORTE\b', min_val=1_000_000, max_val=9_000_000)
    este = este or _label_num_near(t, r'\bEst(?:e|os)\b', min_val=50_000, max_val=999_999)
    return norte, este

# ---------------------------------------------------------------------------
# Lados N-S / E-O finder (pedimentos y manifestaciones)
# ---------------------------------------------------------------------------
DIR_NS = re.compile(r'Norte\s*[-–]?\s*(?:a\s+)?Sur', F)
DIR_EO = re.compile(r'Este\s*[-–]?\s*(?:a\s+)?Oeste', F)
NUM_UNIT = re.compile(rf'{NUM}\s*{UNIT}', F)

def find_sides(t):
    """Devuelve (lado_ns, lado_eo) en metros.

    La frase que describe el rectangulo varia mucho entre documentos: el numero
    puede ir antes o despues de la mencion de la orientacion, y a veces ambos
    numeros quedan "sandwiched" entre las dos menciones de direccion (p.ej.
    "Norte-Sur miden 1000 metros por 3000 metros los lados en sentido Este-Oeste").
    Estrategia robusta: ubicar la primera mencion de Norte-Sur y de Este-Oeste,
    y asignar cada numero+unidad candidato (dentro de una ventana razonable) a
    la mencion de direccion mas cercana en texto.
    """
    m_ns = DIR_NS.search(t)
    m_eo = DIR_EO.search(t)
    if not m_ns or not m_eo:
        return None, None
    # Ventana amplia que cubre ambas menciones de direccion mas un margen antes
    # de la primera y despues de la segunda (el numero puede ir antes de su
    # propia mencion de direccion, o despues de la OTRA mencion de direccion).
    span_lo = min(m_ns.start(), m_eo.start())
    span_hi = max(m_ns.end(), m_eo.end())
    lo = int(max(0, span_lo - 60))
    hi = int(min(len(t), span_hi + 160))

    cands = []
    for nm in NUM_UNIT.finditer(t[lo:hi]):
        try:
            v = clean_num(nm.group(1))
        except ValueError:
            continue
        if not (10 < v < 50_000):
            continue
        pos = lo + nm.start()
        cands.append((pos, v))
    cands.sort()
    if len(cands) < 2:
        return None, None
    # El orden textual de los dos numeros coincide con el orden Norte-Sur,
    # Este-Oeste en las tres variantes de redaccion observadas (numero antes o
    # despues de su propia mencion de direccion, o ambos numeros intercalados
    # entre las dos menciones de direccion).
    return cands[0][1], cands[1][1]

# ---------------------------------------------------------------------------
# Mensura: tabla de vertices
# ---------------------------------------------------------------------------
HEADER_PAT = re.compile(
    r'(?:V[EÉeé]RTICES?|PUNTO)\s+NORTE\s*(?:\([^)]*\))?\s+ESTE\s*(?:\([^)]*\))?', F)

PI_LABEL = re.compile(r'\bP\.?\s*[IL]\.?\b', F)

def parse_mensura_vertices(t):
    """Devuelve (verts, punto_interes) donde verts son SOLO los vertices del
    perimetro (excluye la fila 'P.I.' / Punto de Interes, que suele listarse
    junto a la tabla pero no es un vertice del poligono)."""
    verts = []
    punto_interes = None
    header_m = HEADER_PAT.search(t)
    if header_m:
        sub = t[header_m.end():header_m.end() + 2500]
        toks = [(m.start(), m.end(), m.group(1)) for m in NUM_ONLY.finditer(sub)]
        prev_end = 0
        i = 0
        while i < len(toks):
            s0, e0, raw0 = toks[i]
            try:
                v0 = clean_num(raw0)
            except ValueError:
                i += 1
                continue
            if 1_000_000 < v0 < 9_000_000 and i + 1 < len(toks):
                s1, e1, raw1 = toks[i + 1]
                if s1 - e0 <= 8:
                    try:
                        v1 = clean_num(raw1)
                    except ValueError:
                        v1 = None
                    if v1 is not None and 50_000 < v1 < 999_999:
                        label = sub[prev_end:s0]
                        if PI_LABEL.search(label):
                            punto_interes = (v0, v1)
                        else:
                            verts.append((v0, v1))
                        prev_end = e1
                        i += 2
                        continue
            prev_end = e0
            i += 1
        if len(verts) >= 3:
            return verts, punto_interes

    # Fallback: formato inline "L1 Norte: X m. Este: Y m." repetido (sin tabla con
    # encabezado VERTICE/PUNTO). Ancla cerca de la primera mencion de 'coordenadas'
    # y excluye la entrada etiquetada 'Punto de Interes'/'P.I.' igual que en el
    # pase de tabla.
    anchor = re.search(r'coordenadas', t, F)
    zstart = anchor.start() if anchor else 0
    zone = t[zstart:zstart + 3000]
    inline_pat = re.compile(
        rf'NORTE\s*[:=]?\s*{NUM}\s*{UNIT}?\s*[,;]?\s*Est(?:e|os)\s*[:=]?\s*{NUM}\s*{UNIT}?', F)
    prev_end = 0
    verts2 = []
    pi2 = None
    for m in inline_pat.finditer(zone):
        try:
            n, e = clean_num(m.group(1)), clean_num(m.group(2))
        except ValueError:
            continue
        if not (1_000_000 < n < 9_000_000 and 50_000 < e < 999_999):
            continue
        label = zone[prev_end:m.start()]
        if re.search(r'Punto\s+de\s+Inter[eé]s', label, F) or PI_LABEL.search(label):
            pi2 = (n, e)
        else:
            verts2.append((n, e))
        prev_end = m.end()
    if len(verts2) >= 3:
        return verts2, pi2
    return verts, punto_interes

# ---------------------------------------------------------------------------
# Metadatos comunes
# ---------------------------------------------------------------------------
def find_solicitante(t):
    m = re.search(r'a\s+solicitud\s+de\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\.\s&\-]{4,80}?),', t)
    return m.group(1).strip() if m else None

def find_comuna_provincia_region(t):
    # Hallazgo adicional del 19-08-2026 al revisar el bug de Huso: esta
    # busqueda exigia "Comuna" con mayuscula inicial y perdia redacciones
    # como "...se ubican en la comuna de Valdivia..." (minuscula), dejando
    # comuna=None aunque el dato si estaba en el texto.
    m = re.search(r'Comuna\s+de\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{2,40}?),\s*Provincia\s+(?:de|del)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{2,40}?),\s*Regi[oó]n\s+de\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{2,40}?)[\.\,]', t, F)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return None, None, None

def find_superficie(t):
    """Superficie total en hectareas. Prioriza frases que explicitamente dicen
    'superficie total' para evitar capturar la superficie de UNA pertenencia
    individual (p.ej. '30 pertenencias de 10 hectareas cada una')."""
    for pat in [
        rf'superficie\s+total[^.]{{0,20}}?{NUM}\s*[Hh]ect',
        rf'{NUM}\s*[Hh]ect[aá]reas[^.]{{0,10}}?total',
        rf'total[^.]{{0,20}}?{NUM}\s*[Hh]ect[aá]reas',
    ]:
        m = re.search(pat, t, F)
        if m:
            try:
                return clean_num(m.group(1))
            except ValueError:
                pass
    m = re.search(rf'{NUM}\s*[Hh]ect[aá]reas', t)
    return clean_num(m.group(1)) if m else None

def find_nombre(t):
    m = re.search(r'(?:PEDIMENTO|MANIFESTACION|MANIFESTACIÓN)\s*[:\-]?\s*\n?\s*([A-ZÁÉÍÓÚÑ0-9][A-ZÁÉÍÓÚÑ0-9\s\.\-]{2,60})', t)
    return m.group(1).strip() if m else None

# ---------------------------------------------------------------------------
def parse_pedimento(fname, t):
    tt = norm(t)
    norte, este = find_first_coord_pair(tt)
    ns, eo = find_sides(tt)
    datum, huso, hemis = find_datum(tt)
    comuna, provincia, region = find_comuna_provincia_region(tt)
    sup_text = find_superficie(tt)
    sup_calc = round(ns * eo / 10000, 4) if (ns and eo) else None
    superficie = sup_calc if sup_calc is not None else sup_text
    rec = dict(
        tipo='pedimento', archivo=fname,
        punto_medio_norte=norte, punto_medio_este=este,
        lado_ns=ns, lado_eo=eo,
        datum=datum, huso=huso, hemisferio=hemis,
        comuna=comuna, provincia=provincia, region=region,
        superficie_ha=superficie, superficie_ha_texto=sup_text,
        solicitante=find_solicitante(tt),
    )
    rec['ok'] = all(v is not None for v in (norte, este, ns, eo))
    return rec

def parse_manifestacion(fname, t):
    tt = norm(t)
    if re.search(r'RECTIFICACI[OÓ]N', tt, F) and not re.search(r'Punto\s+(?:de\s+)?Inter[eé]s', tt, F):
        return dict(tipo='manifestacion', archivo=fname, ok=False, motivo='rectificacion_sin_geometria')
    norte, este = find_first_coord_pair(tt)
    ns, eo = find_sides(tt)
    datum, huso, hemis = find_datum(tt)
    comuna, provincia, region = find_comuna_provincia_region(tt)
    sup_text = find_superficie(tt)
    sup_calc = round(ns * eo / 10000, 4) if (ns and eo) else None
    superficie = sup_calc if sup_calc is not None else sup_text
    rec = dict(
        tipo='manifestacion', archivo=fname,
        punto_interes_norte=norte, punto_interes_este=este,
        lado_ns=ns, lado_eo=eo,
        datum=datum, huso=huso, hemisferio=hemis,
        comuna=comuna, provincia=provincia, region=region,
        superficie_ha=superficie, superficie_ha_texto=sup_text,
        solicitante=find_solicitante(tt),
    )
    rec['ok'] = all(v is not None for v in (norte, este, ns, eo))
    return rec

def parse_sentencia(tipo, fname, t):
    """Sentencias de Exploracion / Explotacion: fallo judicial que resuelve
    una Manifestacion previa, concediendo el area sobre los mismos deslindes
    ya presentados en el expediente. Reutiliza los mismos extractores de
    coordenadas y lados que parse_manifestacion(), porque la sentencia
    tipicamente reproduce el mismo bloque de "Punto de Interes" + lados
    Norte-Sur/Este-Oeste del expediente que resuelve.

    AVISO: sin ejemplos reales de Sentencias disponibles para calibrar el
    regex contra boletines historicos (categoria poco frecuente). Si el
    formato real difiere de lo asumido aqui, 'ok' queda en False y el
    registro cae a sin_georreferenciar para revision manual -- igual que
    pasaba antes de agregar este parser, nunca se fuerza una geometria
    dudosa hacia el mapa.
    """
    tt = norm(t)
    norte, este = find_first_coord_pair(tt)
    ns, eo = find_sides(tt)
    datum, huso, hemis = find_datum(tt)
    comuna, provincia, region = find_comuna_provincia_region(tt)
    sup_text = find_superficie(tt)
    sup_calc = round(ns * eo / 10000, 4) if (ns and eo) else None
    superficie = sup_calc if sup_calc is not None else sup_text
    rec = dict(
        tipo=tipo, archivo=fname,
        punto_interes_norte=norte, punto_interes_este=este,
        lado_ns=ns, lado_eo=eo,
        datum=datum, huso=huso, hemisferio=hemis,
        comuna=comuna, provincia=provincia, region=region,
        superficie_ha=superficie, superficie_ha_texto=sup_text,
        solicitante=find_solicitante(tt),
    )
    rec['ok'] = all(v is not None for v in (norte, este, ns, eo))
    if not rec['ok']:
        rec['motivo'] = 'formato_sentencia_no_reconocido'
    return rec


def parse_sentencia_exploracion(fname, t):
    return parse_sentencia('sentencia_exploracion', fname, t)


def parse_sentencia_explotacion(fname, t):
    return parse_sentencia('sentencia_explotacion', fname, t)


def parse_mensura(fname, t):
    tt = norm(t)
    verts, pi = parse_mensura_vertices(tt)
    datum, huso, hemis = find_datum(tt)
    comuna, provincia, region = find_comuna_provincia_region(tt)
    rec = dict(
        tipo='mensura', archivo=fname,
        vertices=verts,
        punto_interes=pi,
        datum=datum, huso=huso, hemisferio=hemis,
        comuna=comuna, provincia=provincia, region=region,
        superficie_ha=find_superficie(tt),
        solicitante=find_solicitante(tt),
    )
    rec['ok'] = len(verts) >= 3
    return rec

def main():
    texts = json.load(open('all_texts.json'))
    out = {}
    for fname, t in texts.items():
        if fname.startswith('gm_ped_'):
            out[fname] = parse_pedimento(fname, t)
        elif fname.startswith('gm_man_'):
            out[fname] = parse_manifestacion(fname, t)
        elif fname.startswith('gm_mens_'):
            out[fname] = parse_mensura(fname, t)
        else:
            out[fname] = dict(tipo='desconocido', archivo=fname, ok=False)
    json.dump(out, open('parsed_final.json', 'w'), ensure_ascii=False, indent=1)

    fails = {k: v for k, v in out.items() if not v.get('ok')}
    print(f"Total: {len(out)}  OK: {len(out)-len(fails)}  Fails: {len(fails)}")
    for k, v in fails.items():
        print(' -', k, v.get('motivo', ''))

if __name__ == '__main__':
    main()
