#!/usr/bin/env python3
"""
Backfill del historico: recorre dias habiles (lun-vie) de un rango de fechas
y corre el pipeline completo (run_pipeline.process_date) para cada uno,
fusionando todo en docs/data/boletin_mineria_historico.* / docs/historico.html
SIN tocar el "mapa del dia" en vivo (ver update_latest=False mas abajo -- ese
sigue siendo el de la fecha real mas reciente, no la fecha vieja que se esta
reprocesando).

Uso:
    python backfill.py [--start DD-MM-AAAA] [--end DD-MM-AAAA] [--no-push]

Por defecto: 01-01-2026 -> 16-08-2026 (el hueco anterior a que el historico
en vivo empezara a cubrirse dia a dia, el 17-08-2026).

Resumible: guarda progreso en .backfill_state.json (fecha -> resultado) Y
ademas detecta fechas ya hechas mirando si existe
docs/data/reporte_<AAAAMMDD>.json (ya commiteado). Esto ultimo es lo que
permite resumir en un checkout limpio de CI, donde el archivo de estado
local no persiste entre corridas del workflow.

Publica (git add + commit + push de docs/data/ y docs/historico.html) al
terminar cada mes calendario procesado, para que el sitio se vaya
actualizando progresivamente en vez de esperar a que termine todo el rango.
"""
import argparse
import json
import os
import subprocess
import time
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

import run_pipeline as RP
import scraper as S

STATE_PATH = ".backfill_state.json"
DEFAULT_START = "01-01-2026"
DEFAULT_END = "16-08-2026"
SLEEP_BETWEEN_DAYS = 6.0  # cortesia con el sitio, ademas del delay entre PDFs
                          # (subido de 2.0 a 6.0 tras el bloqueo anti-bot del
                          # 26-08-2026 -- ver commit fcc3d92)
MAX_ATTEMPTS_PER_DAY = 2  # 1 intento + 1 reintento

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def parse_ddmmyyyy(s):
    d, m, y = s.split("-")
    return date(int(y), int(m), int(d))


def fmt_ddmmyyyy(d):
    return d.strftime("%d-%m-%Y")


def business_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0=lunes ... 4=viernes (Python: Monday=0)
            yield d
        d += timedelta(days=1)


def date_compact(date_str):
    d, m, y = date_str.split("-")
    return f"{y}{m}{d}"


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def already_done(date_str, state):
    """True si esta fecha ya se proceso con exito. Ademas del estado local
    (.backfill_state.json, gitignored) revisa si ya existe el reporte
    commiteado docs/data/reporte_<AAAAMMDD>.json -- asi un checkout limpio
    en CI (donde el archivo de estado no persiste entre corridas) tambien
    puede resumir sin reprocesar fechas que ya quedaron en el historico."""
    prev = state.get(date_str)
    if prev and prev.get("status") == "ok":
        return True
    reporte_path = f"docs/data/reporte_{date_compact(date_str)}.json"
    if os.path.exists(reporte_path):
        try:
            with open(reporte_path, "r", encoding="utf-8") as f:
                r = json.load(f)
            state[date_str] = {
                "status": "ok",
                "total_publicaciones": r.get("total_publicaciones"),
                "georreferenciadas": r.get("georreferenciadas"),
            }
            return True
        except Exception:
            pass
    return False


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def has_staged_changes():
    r = subprocess.run(["git", "diff", "--cached", "--quiet"])
    return r.returncode != 0


def commit_month(month_label, total_new, push):
    """Commitea (y opcionalmente pushea) lo acumulado de un mes. Un fallo
    aca (red caida, push rechazado, etc.) se reporta pero NO debe tumbar el
    backfill completo -- si el commit ya se hizo localmente, el push se
    puede reintentar despues a mano sin perder nada; los datos siguen
    seguros en el working tree/commit local de todos modos."""
    try:
        subprocess.run(["git", "add", "-A", "--",
                         "docs/data", "docs/historico.html"], check=True)
        if not has_staged_changes():
            print(f"  (nada nuevo que commitear para {month_label})")
            return
        msg = f"Backfill historico: {month_label} — {total_new} publicaciones georreferenciadas"
        subprocess.run(["git", "commit", "-m", msg], check=True)
        print(f"  commit creado: {msg}")
        if push:
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print("  push OK")
    except subprocess.CalledProcessError as e:
        print(f"  AVISO: fallo git add/commit/push para {month_label} ({e}); "
              f"se sigue con el backfill, revisar y pushear a mano despues.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--max-days", type=int, default=None,
                     help="Parar despues de INTENTAR (exito o error) esta "
                          "cantidad de fechas nuevas -- las ya hechas se "
                          "saltan gratis y no cuentan. Uso: correr de a "
                          "poco desde un cron de baja frecuencia (ver "
                          "backfill-drip.yml), imitando el mismo patron "
                          "'una fecha por corrida' que ya usa daily.yml.")
    args = ap.parse_args()

    start = parse_ddmmyyyy(args.start)
    end = parse_ddmmyyyy(args.end)
    push = not args.no_push

    state = load_state()
    days = list(business_days(start, end))
    print(f"Backfill: {len(days)} dias habiles entre {args.start} y {args.end}")

    current_month = None
    month_new_pubs = 0
    total_ok = total_skipped = total_failed = 0

    def flush_month():
        nonlocal month_new_pubs
        if current_month is None:
            return
        year, month = current_month
        label = f"{MESES[month]} {year}"
        commit_month(label, month_new_pubs, push)
        month_new_pubs = 0

    # UNA sola sesion de Playwright (un solo challenge anti-bot resuelto)
    # reusada para TODAS las fechas del rango -- ver scrape_day_with_page()
    # en scraper.py para el porque: abrir un browser nuevo por fecha fue lo
    # que hizo fallar el 97% del backfill del 26-08-2026.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=S.USER_AGENT)
        page = context.new_page()
        try:
            for d in days:
                date_str = fmt_ddmmyyyy(d)
                ym = (d.year, d.month)
                if current_month is not None and ym != current_month:
                    flush_month()
                current_month = ym

                if already_done(date_str, state):
                    total_skipped += 1
                    continue

                if args.max_days is not None and total_ok + total_failed >= args.max_days:
                    print(f"\n(limite de --max-days {args.max_days} alcanzado, "
                          f"parando aca -- la proxima corrida sigue desde {date_str})")
                    break

                print(f"\n--- {date_str} ---")
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        report = RP.process_date(date_str, update_latest=False,
                                                  page=page, context=context)
                        state[date_str] = {
                            "status": "ok",
                            "total_publicaciones": report["total_publicaciones"],
                            "georreferenciadas": report["georreferenciadas"],
                        }
                        month_new_pubs += report["georreferenciadas"]
                        total_ok += 1
                        save_state(state)
                        break
                    except Exception as e:
                        print(f"  ERROR (intento {attempt}/{MAX_ATTEMPTS_PER_DAY}): {e}")
                        if attempt >= MAX_ATTEMPTS_PER_DAY:
                            state[date_str] = {"status": "error", "error": str(e)}
                            total_failed += 1
                            save_state(state)
                            break
                        time.sleep(5)

                time.sleep(SLEEP_BETWEEN_DAYS)

            flush_month()

        finally:
            save_state(state)
            browser.close()

    print("\n=== Backfill terminado ===")
    print(f"OK: {total_ok}  saltados (ya hechos): {total_skipped}  fallidos: {total_failed}")
    failed = sorted(k for k, v in state.items() if v.get("status") == "error")
    if failed:
        print(f"Fechas con error (revisar manualmente): {failed}")


if __name__ == "__main__":
    main()
