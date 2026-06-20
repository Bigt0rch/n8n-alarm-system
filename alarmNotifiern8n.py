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

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

MARCADOR_SALTO_LINEA = "@@NL@@"

def construir_email(destinatario: str, alarma: dict, email_from: str) -> MIMEMultipart:
    estado = str(alarma.get("estado_nuevo", "DESCONOCIDO"))
    es_critico = estado == "CRITICAL"

    nombre = str(alarma.get("nombre", "Alarma sin nombre"))
    valor = alarma.get("valor", "N/A")
    contexto = (
        str(alarma.get("contexto") or "")
        .replace(MARCADOR_SALTO_LINEA, "\n")
        .strip()
    )

    nombre_html = escape(nombre)
    estado_html = escape(estado)
    valor_html = escape(str(valor))
    contexto_html = escape(contexto)

    contexto_html = contexto_html.replace("\n", "<br>")

    log.debug(
        f"Construyendo email para '{nombre}' → {destinatario} "
        f"(estado: {estado})"
    )

    asunto = (
        f"🚨 ALERTA: {nombre}"
        if es_critico else
        f"✅ RECUPERADO: {nombre}"
    )

    color_principal = "#dc3545" if es_critico else "#198754"
    color_fondo = "#fff5f5" if es_critico else "#f3fff8"
    icono = "🚨" if es_critico else "✅"
    titulo = "ALARMA CRÍTICA" if es_critico else "ALARMA RECUPERADA"

    bloque_contexto = ""
    if contexto:
        bloque_contexto = f"""
        <tr>
            <td style="padding: 10px 0; color: #555; font-weight: bold; width: 140px;">
                Contexto
            </td>
            <td style="padding: 10px 0; color: #222;">
                {contexto_html}
            </td>
        </tr>
        """

    cuerpo_html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>{escape(asunto)}</title>
    </head>
    <body style="margin: 0; padding: 0; background-color: #f4f6f8; font-family: Arial, Helvetica, sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f4f6f8; padding: 30px 0;">
            <tr>
                <td align="center">
                    <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 10px; overflow: hidden; border: 1px solid #e1e4e8;">
                        
                        <tr>
                            <td style="background-color: {color_principal}; padding: 22px 28px; color: #ffffff;">
                                <h1 style="margin: 0; font-size: 22px; font-weight: 700;">
                                    {icono} {titulo}
                                </h1>
                            </td>
                        </tr>

                        <tr>
                            <td style="padding: 28px;">
                                <div style="background-color: {color_fondo}; border-left: 5px solid {color_principal}; padding: 16px 18px; border-radius: 6px; margin-bottom: 24px;">
                                    <p style="margin: 0; color: #333; font-size: 15px;">
                                        Se ha detectado un cambio de estado en la siguiente alarma.
                                    </p>
                                </div>

                                <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse; font-size: 14px;">
                                    <tr>
                                        <td style="padding: 10px 0; color: #555; font-weight: bold; width: 140px;">
                                            Alarma
                                        </td>
                                        <td style="padding: 10px 0; color: #222;">
                                            {nombre_html}
                                        </td>
                                    </tr>

                                    <tr>
                                        <td style="padding: 10px 0; color: #555; font-weight: bold;">
                                            Estado
                                        </td>
                                        <td style="padding: 10px 0;">
                                            <span style="display: inline-block; padding: 5px 10px; border-radius: 14px; background-color: {color_principal}; color: #ffffff; font-weight: bold;">
                                                {estado_html}
                                            </span>
                                        </td>
                                    </tr>

                                    <tr>
                                        <td style="padding: 10px 0; color: #555; font-weight: bold;">
                                            Valor
                                        </td>
                                        <td style="padding: 10px 0; color: #222;">
                                            {valor_html}
                                        </td>
                                    </tr>

                                    {bloque_contexto}
                                </table>
                            </td>
                        </tr>

                        <tr>
                            <td style="padding: 16px 28px; background-color: #f8f9fa; color: #777; font-size: 12px; text-align: center;">
                                Mensaje generado automáticamente por el sistema de monitorización.
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    cuerpo_texto = f"""
{titulo}

Alarma: {nombre}
Estado: {estado}
Valor: {valor}
{f"Contexto: {contexto}" if contexto else ""}

Mensaje generado automáticamente por el sistema de monitorización.
""".strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = email_from
    msg["To"] = destinatario

    msg.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

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
