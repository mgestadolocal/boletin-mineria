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

Resumible: guarda progreso en .backfill_state.json (fecha -> resultado). Si
el proceso se corta a mitad de camino, correr de nuevo salta lo que ya quedo
marcado "ok" y sigue donde se quedo -- no reprocesa nada de mas.

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

import run_pipeline as RP

STATE_PATH = ".backfill_state.json"
DEFAULT_START = "01-01-2026"
DEFAULT_END = "16-08-2026"
SLEEP_BETWEEN_DAYS = 2.0  # cortesia con el sitio, ademas del delay entre PDFs
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


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def has_staged_changes():
    r = subprocess.run(["git", "diff", "--cached", "--quiet"])
    return r.returncode != 0


def commit_month(month_label, total_new, push):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--no-push", action="store_true")
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

    try:
        for d in days:
            date_str = fmt_ddmmyyyy(d)
            ym = (d.year, d.month)
            if current_month is not None and ym != current_month:
                flush_month()
            current_month = ym

            prev = state.get(date_str)
            if prev and prev.get("status") == "ok":
                total_skipped += 1
                continue

            print(f"\n--- {date_str} ---")
            attempt = 0
            while True:
                attempt += 1
                try:
                    report = RP.process_date(date_str, update_latest=False)
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

    print("\n=== Backfill terminado ===")
    print(f"OK: {total_ok}  saltados (ya hechos): {total_skipped}  fallidos: {total_failed}")
    failed = sorted(k for k, v in state.items() if v.get("status") == "error")
    if failed:
        print(f"Fechas con error (revisar manualmente): {failed}")


if __name__ == "__main__":
    main()
