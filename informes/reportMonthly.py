import argparse
import logging
import os
import sys
from calendar import monthrange
from datetime import datetime, timezone

from reportGenerator import ejecutar_informe


USO = """
Uso:
  python reportMonthly.py \\
      --influx-url    <URL>          \\
      --influx-token  <TOKEN>        \\
      --influx-org    <ORG>          \\
      --influx-bucket <BUCKET>       \\
      --smtp-host     <HOST>         \\
      --smtp-port     <PUERTO>       \\
      --smtp-user     <USUARIO>      \\
      --smtp-password <CONTRASEÑA>   \\
      --email-from    <REMITENTE>    \\
      --email-to      <DESTINATARIO> \\
      [--output-dir   <RUTA>]        \\
      [--log-dir      <RUTA>]

Por defecto cubre el mes natural anterior a la fecha de ejecución.
"""


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera y envía el informe mensual de monitorización."
    )
    parser.add_argument("--influx-url",    required=True)
    parser.add_argument("--influx-token",  required=True)
    parser.add_argument("--influx-org",    required=True)
    parser.add_argument("--influx-bucket", required=True)
    parser.add_argument("--smtp-host",     required=True)
    parser.add_argument("--smtp-port",     required=True, type=int)
    parser.add_argument("--smtp-user",     required=True)
    parser.add_argument("--smtp-password", required=True)
    parser.add_argument("--email-from",    required=True)
    parser.add_argument("--email-to",      required=True)
    parser.add_argument("--output-dir",    required=False, default=None)
    parser.add_argument("--log-dir",       required=False, default=None)

    try:
        return parser.parse_args()
    except SystemExit:
        logging.basicConfig(format="%(asctime)s [%(levelname)-8s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S", level=logging.ERROR)
        logging.error("Faltan parámetros obligatorios.%s", USO)
        sys.exit(2)


def configurar_logger(log_directory: str | None) -> logging.Logger:
    logger = logging.getLogger("reportMonthly")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)-8s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    if log_directory:
        log_directory = os.path.expanduser(log_directory)
        os.makedirs(log_directory, exist_ok=True)
        ts      = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        logfile = os.path.join(log_directory, f"{ts}_reportMonthly.log")
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


if __name__ == "__main__":
    args = parsear_argumentos()
    log  = configurar_logger(args.log_dir)

    # Mes anterior
    hoy     = datetime.now(timezone.utc)
    mes     = hoy.month - 1 or 12
    anyo    = hoy.year if hoy.month > 1 else hoy.year - 1
    _, dias = monthrange(anyo, mes)

    inicio_dt = datetime(anyo, mes, 1,  0, 0, 0, tzinfo=timezone.utc)
    fin_dt    = datetime(anyo, mes, dias, 23, 59, 59, tzinfo=timezone.utc)

    meses_es  = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                 "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    periodo_str    = f"{meses_es[mes - 1]} {anyo}"
    nombre_fichero = f"informe_mensual_{anyo}_{mes:02d}.pdf"

    try:
        ejecutar_informe(
            args, inicio_dt, fin_dt,
            titulo="Informe mensual de monitorización",
            periodo_str=periodo_str,
            nombre_fichero=nombre_fichero,
            modo_histograma="diario",
            log=log
        )
    except Exception as e:
        log.error(f"Error generando el informe: {e}", exc_info=True)
        raise
