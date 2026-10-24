"""Línea de comandos de QuantSport AI.

Ejemplos (desde backend/):
    python -m app.cli importar https://www.football-data.co.uk/new/MEX.csv
    python -m app.cli sincronizar --proveedor api_football --competicion mex_liga_mx --tipo results --temporadas 2022,2023,2024
    python -m app.cli equipos --competicion mex_liga_mx
    python -m app.cli fusionar --de 41 --en 7
    python -m app.cli validar --competicion mex_liga_mx
    python -m app.cli sincronizar --proveedor openfootball --competicion esp_la_liga --tipo results --temporadas 2024,2025
    python -m app.cli importar ~/Descargas/clubelo_2026-10-23.csv     # ratings Club Elo descargados a mano
    python -m app.cli tarea diaria        # ejecuta ahora una tarea del programador (diaria | cuotas | previa)
    python -m app.cli programador         # deja el programador corriendo en esta terminal (Ctrl+C para salir)

Nada se contrata ni se apuesta: los comandos solo leen fuentes configuradas y calculan.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.runtime_settings import get_thresholds
from app.db.migrate import upgrade_head
from app.db.models import Competition
from app.db.session import session_scope
from app.ingestion import jobs, teams
from app.ingestion.download import MAX_DOWNLOAD_BYTES, DownloadError, download
from app.ingestion.store import Ingestor, seed_sports
from app.providers.base import ProviderError
from app.providers.registry import SEASON_AWARE, get_provider
from app.reporting.validation_report import build_markdown
from app.scheduler import runner as scheduler_runner
from app.sports.football.service import football_service


def _init() -> None:
    settings = get_settings()
    configure_logging("WARNING", False)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_dir.mkdir(parents=True, exist_ok=True)
    upgrade_head()
    with session_scope() as s:
        seed_sports(s)
        if not settings.is_demo:
            jobs.wipe_synthetic(s)


def _read_source(src: str) -> tuple[bytes, str]:
    if src.startswith(("http://", "https://")):
        try:
            content, name = download(src)
        except DownloadError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Descargado {src} → {get_settings().imports_dir / name}")
        return content, name
    with open(src, "rb") as fh:
        content = fh.read(MAX_DOWNLOAD_BYTES + 1)
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise SystemExit("Archivo demasiado grande (máximo 10 MB)")
    return content, src.rsplit("/", 1)[-1]


def cmd_importar(a: argparse.Namespace) -> int:
    content, name = _read_source(a.fuente)
    with session_scope() as s:
        rep = jobs.import_csv(s, content, name)
    if rep.get("format") == "clubelo":
        print(f"Ratings Club Elo: {rep['ratings_inserted']} nuevos ({rep['ratings_duplicates']} duplicados), "
              f"{rep['unmatched_clubs']} clubes sin equipo en la base")
        return 0
    print(f"Eventos nuevos: {rep['events_created']} · actualizados: {rep['events_updated']} · "
          f"cuotas nuevas: {rep['odds_inserted']} (duplicadas: {rep['odds_duplicates']})")
    for w in rep.get("warnings", [])[:10]:
        print(f"  aviso: {w}")
    return 0


def cmd_sincronizar(a: argparse.Namespace) -> int:
    settings = get_settings()
    p = get_provider(settings, a.proveedor)
    if p is None:
        raise SystemExit(f"Proveedor desconocido: {a.proveedor}")
    seasons = [x.strip() for x in (a.temporadas or "").split(",") if x.strip()] or [None]
    code = 0
    for season in seasons:
        fn = {"results": p.fetch_results, "fixtures": p.fetch_fixtures, "odds": p.fetch_odds}[a.tipo]
        kwargs = {"season": season} if season and (a.tipo == "results" or a.proveedor in SEASON_AWARE) else {}
        try:
            batch = fn(a.competicion, **kwargs)
        except ProviderError as exc:
            print(f"[{season or 'actual'}] error: {exc}")
            code = 1
            continue
        with session_scope() as s:
            rep = Ingestor(s).ingest(batch)
        print(f"[{season or 'actual'}] eventos nuevos {rep.events_created}, actualizados {rep.events_updated}, "
              f"cuotas {rep.odds_inserted}, sin emparejar {rep.odds_unmatched}")
        for w in rep.warnings[:10]:
            print(f"  aviso: {w}")
    return code


def cmd_equipos(a: argparse.Namespace) -> int:
    with session_scope() as s:
        try:
            r = teams.review(s, a.competicion, get_thresholds(s).min_team_matches)
        except teams.TeamMergeError as exc:
            raise SystemExit(str(exc)) from exc
    print(f"{r['competition']}: {len(r['teams'])} equipos, {r['pending_review']} por revisar "
          f"(con partidos próximos y menos de {r['min_matches']} resultados)")
    for t in r["teams"]:
        flag = "REVISAR" if t["needs_review"] else "ok"
        print(f"  [{t['team_id']:>4}] {t['name']:<28} resultados {t['finished']:>4} · próximos {t['scheduled']:>2} · {flag}")
        for sug in t["suggestions"]:
            print(f"         ¿es {sug['name']} [{sug['team_id']}]? similitud {sug['similarity']:.2f} "
                  f"→ python -m app.cli fusionar --de {t['team_id']} --en {sug['team_id']}")
    return 0


def cmd_fusionar(a: argparse.Namespace) -> int:
    with session_scope() as s:
        try:
            rep = teams.merge(s, a.de, a.en)
        except teams.TeamMergeError as exc:
            raise SystemExit(str(exc)) from exc
    print(f"{rep['from']} → {rep['into']}: {rep['events_moved']} partidos movidos, {rep['events_merged']} duplicados "
          f"unidos, {rep['odds_moved']} cuotas movidas")
    return 0


def cmd_validar(a: argparse.Namespace) -> int:
    with session_scope() as s:
        comp = s.scalar(select(Competition).where(Competition.key == a.competicion))
        if comp is None:
            raise SystemExit(f"No hay datos de {a.competicion}. Importa primero un CSV o sincroniza un proveedor.")
        print("Ajustando el modelo y ejecutando walk-forward (puede tardar unos minutos)…")
        st = football_service.state(s, comp, get_thresholds(s), force=True)
        md = build_markdown(st)
    out_dir = get_settings().reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"validacion_{a.competicion}_{datetime.now(UTC):%Y%m%d_%H%M}.md"
    path.write_text(md, encoding="utf-8")
    print(md)
    print(f"Informe guardado en {path}")
    return 0


def cmd_tarea(a: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.is_demo:
        raise SystemExit("El programador solo funciona con DATA_MODE=real (pon DATA_MODE=real en .env).")
    plan = monthly_plan_text()
    print(plan)
    res = scheduler_runner.run_task(a.nombre, manual=True, settings=settings)
    print(f"Estado: {res['status']}")
    print(res.get("detail") or res.get("reason", ""))
    return 0 if res["status"] in ("ok", "skipped") else 1


def monthly_plan_text() -> str:
    from app.scheduler.tasks import monthly_plan

    with session_scope() as s:
        p = monthly_plan(s, get_settings())
    fits = "dentro" if p["fits"] else "FUERA"
    return (f"Créditos estimados al mes: {p['total']} (cuotas {p['odds']}, resultados {p['scores']}, previas "
            f"{p['prekickoff']}) · disponibles {p['available']} ({fits} del límite) · usados este mes "
            f"{p['used_this_month']:.0f}")


def cmd_programador(a: argparse.Namespace) -> int:
    import signal
    import time

    settings = get_settings()
    if settings.is_demo:
        raise SystemExit("El programador solo funciona con DATA_MODE=real (pon DATA_MODE=real en .env).")
    configure_logging("INFO", False)
    print(monthly_plan_text())
    if not scheduler_runner.start(settings.model_copy(update={"scheduler_enabled": True})):
        return 1
    for j in scheduler_runner.next_runs():
        print(f"  {j['id']}: próxima {j['next_run']}")
    print("Programador en marcha. Ctrl+C para detenerlo.")
    # systemd y kill mandan SIGTERM: se trata igual que Ctrl+C para cerrar el programador en orden
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler_runner.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="QuantSport AI · herramientas de datos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("importar", help="Importa un CSV (ruta local o URL de football-data.co.uk)")
    p.add_argument("fuente")
    p.set_defaults(fn=cmd_importar)
    p = sub.add_parser("sincronizar", help="Descarga datos de un proveedor configurado en .env")
    p.add_argument("--proveedor", required=True, choices=["api_football", "the_odds_api", "football_data_org", "openfootball"])
    p.add_argument("--competicion", required=True)
    p.add_argument("--tipo", default="results", choices=["results", "fixtures", "odds"])
    p.add_argument("--temporadas", help="Lista separada por comas, p. ej. 2022,2023,2024")
    p.set_defaults(fn=cmd_sincronizar)
    p = sub.add_parser("equipos", help="Lista equipos sin historial y sugiere fusiones")
    p.add_argument("--competicion", required=True)
    p.set_defaults(fn=cmd_equipos)
    p = sub.add_parser("fusionar", help="Fusiona un equipo duplicado en otro")
    p.add_argument("--de", type=int, required=True, help="id del equipo a eliminar")
    p.add_argument("--en", type=int, required=True, help="id del equipo que se conserva")
    p.set_defaults(fn=cmd_fusionar)
    p = sub.add_parser("validar", help="Ajusta el modelo, valida (walk-forward) y escribe un informe")
    p.add_argument("--competicion", required=True)
    p.set_defaults(fn=cmd_validar)
    p = sub.add_parser("tarea", help="Ejecuta ahora una tarea del programador")
    p.add_argument("nombre", choices=["diaria", "cuotas", "previa"])
    p.set_defaults(fn=cmd_tarea)
    p = sub.add_parser("programador", help="Mantiene el programador corriendo en esta terminal")
    p.set_defaults(fn=cmd_programador)
    a = ap.parse_args(argv)
    _init()
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
