import argparse
import sys
import json
import smtplib
import logging
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# --- PARÁMETROS ---

USO = """
Uso:
  echo '<json>' | python alarmNotifiern8n.py \\
      --smtp-host     <HOST>      \\
      --smtp-port     <PUERTO>    \\
      --smtp-user     <USUARIO>   \\
      --smtp-password <CONTRASEÑA>\\
      --email-from    <REMITENTE> \\
      [--log-dir      <RUTA>]

Parámetros obligatorios:
  --smtp-host       Servidor SMTP                      (ej: smtp.serviciodecorreo.es)
  --smtp-port       Puerto SMTP                        (ej: 465)
  --smtp-user       Usuario de autenticación SMTP      (ej: uptime@dominio.com)
  --smtp-password   Contraseña de autenticación SMTP
  --email-from      Dirección y nombre del remitente   (ej: "Alertas <uptime@dominio.com>")

Parámetros opcionales:
  --log-dir         Directorio donde se guardan los logs (ej: ~/.n8n-files/logs)
                    Si se omite, los logs solo se emiten por consola.

Ejemplo:
  echo '[{"nombre":"web","estado_nuevo":"CRITICAL",...}]' | python alarmNotifiern8n.py \\
      --smtp-host smtp.serviciodecorreo.es \\
      --smtp-port 465 \\
      --smtp-user uptime@dominio.com \\
      --smtp-password MiContraseña \\
      --email-from "Sistema de alertas <uptime@dominio.com>" \\
      --log-dir ~/.n8n-files/logs
"""

def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Notificador de alarmas: envía emails ante cambios de estado.",
        add_help=True
    )
    parser.add_argument("--smtp-host",     required=True, help="Servidor SMTP")
    parser.add_argument("--smtp-port",     required=True, type=int, help="Puerto SMTP")
    parser.add_argument("--smtp-user",     required=True, help="Usuario SMTP")
    parser.add_argument("--smtp-password", required=True, help="Contraseña SMTP")
    parser.add_argument("--email-from",    required=True, help="Remitente del email")
    parser.add_argument("--log-dir",       required=False, default=None,
                        help="Directorio de logs (opcional)")

    try:
        return parser.parse_args()
    except SystemExit:
        logging.basicConfig(format="%(asctime)s [%(levelname)-8s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S", level=logging.ERROR)
        logging.error("Faltan parámetros obligatorios. Consulta cómo usar el script:%s", USO)
        sys.exit(2)


# --- LOGGING ---

def configurar_logger(nombre: str, log_directory: str | None) -> logging.Logger:
    logger = logging.getLogger(nombre)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    if log_directory:
        log_directory = os.path.expanduser(log_directory)
        os.makedirs(log_directory, exist_ok=True)
        ts      = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        logfile = os.path.join(log_directory, f"{ts}_{nombre}.log")
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.info(f"Log iniciado → {logfile}")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# --- EMAIL ---

def construir_email(destinatario: str, alarma: dict, email_from: str) -> MIMEMultipart:
    es_critico = alarma["estado_nuevo"] == "CRITICAL"
    log.debug(f"Construyendo email para '{alarma['nombre']}' → {destinatario} "
              f"(estado: {alarma['estado_nuevo']})")

    asunto = (
        f"🚨 ALERTA: {alarma['nombre']}"
        if es_critico else
        f"✅ RECUPERADO: {alarma['nombre']}"
    )

    cuerpo = f"""
    <h2>{'🚨 CRITICAL' if es_critico else '✅ RECUPERADO'}</h2>
    <p><b>Alarma:</b> {alarma['nombre']}</p>
    <p><b>Estado:</b> {alarma['estado_nuevo']}</p>
    <p><b>Valor:</b> {alarma.get('valor')}</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = email_from
    msg["To"]      = destinatario
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))

    return msg


def enviar_email(msg: MIMEMultipart, destinatario: str,
                 smtp_host: str, smtp_port: int,
                 smtp_user: str, smtp_password: str,
                 email_from: str) -> None:
    log.debug(f"Conectando a {smtp_host}:{smtp_port}")
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as servidor:
        servidor.login(smtp_user, smtp_password)
        servidor.sendmail(email_from, destinatario, msg.as_string())
    log.debug(f"Email enviado a {destinatario}")


# --- MAIN ---

def main(args: argparse.Namespace) -> None:
    try:
        cambios = json.load(sys.stdin)
    except Exception as e:
        log.error(f"Error leyendo stdin: {e}", exc_info=True)
        print("[]")
        return

    if not cambios:
        log.info("No hay cambios que notificar")
        return

    log.info(f"Procesando {len(cambios)} cambio(s)")

    for alarma in cambios:
        destinatario = alarma.get("responsable")

        if not destinatario:
            log.debug(f"'{alarma['alarm_id']}': sin responsable, omitiendo")
            continue

        try:
            msg = construir_email(destinatario, alarma, args.email_from)
            enviar_email(
                msg, destinatario,
                args.smtp_host, args.smtp_port,
                args.smtp_user, args.smtp_password,
                args.email_from
            )
            log.info(f"Notificación enviada → '{alarma['nombre']}' "
                     f"({alarma['estado_nuevo']}) a {destinatario}")
        except Exception as e:
            log.error(f"Error notificando '{alarma['nombre']}': {e}", exc_info=True)

    log.info("Ciclo de notificaciones completado")


if __name__ == "__main__":
    args = parsear_argumentos()
    log  = configurar_logger("alarmNotifier", args.log_dir)
    main(args)