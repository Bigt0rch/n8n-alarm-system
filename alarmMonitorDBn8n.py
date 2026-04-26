import argparse
import json
import logging
import os
from datetime import datetime, timezone
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import sys

NOTIFICATIONS_MEASUREMENT = "alarm_notifications"


# --- PARÁMETROS ---

USO = """
Uso:
  echo '<json>' | python alarmMonitorDBn8n.py \\
      --influx-url    <URL>     \\
      --influx-token  <TOKEN>   \\
      --influx-org    <ORG>     \\
      --influx-bucket <BUCKET>  \\
      --log-dir       <RUTA>

Parámetros obligatorios:
  --influx-url      URL del servidor InfluxDB          (ej: http://localhost:8086)
  --influx-token    Token de autenticación de InfluxDB
  --influx-org      Nombre de la organización en InfluxDB
  --influx-bucket   Nombre del bucket en InfluxDB

Parámetros opcionales:
  --log-dir         Directorio donde se guardan los logs (ej: ~/.n8n-files/logs)
                    Si se omite, los logs solo se emiten por consola.

Ejemplo:
  echo '[{"nombre":"web","categoria":"servicio",...}]' | python alarmMonitorDBn8n.py \\
      --influx-url http://localhost:8086 \\
      --influx-token mUcHAs_L3tr4s== \\
      --influx-org MiOrg \\
      --influx-bucket MiBucket \\
      --log-dir ~/.n8n-files/logs
"""

def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor de alarmas: evalúa estado y escribe cambios en InfluxDB.",
        add_help=True
    )
    parser.add_argument("--influx-url",    required=True, help="URL del servidor InfluxDB")
    parser.add_argument("--influx-token",  required=True, help="Token de autenticación de InfluxDB")
    parser.add_argument("--influx-org",    required=True, help="Organización de InfluxDB")
    parser.add_argument("--influx-bucket", required=True, help="Bucket de InfluxDB")
    parser.add_argument("--log-dir",       required=False, default=None,
                        help="Directorio de logs (opcional)")

    try:
        return parser.parse_args()
    except SystemExit:
        # argparse ya imprimió el error; añadimos el log de uso detallado
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


# --- CONSULTAS A alarm_notifications ---

def obtener_estado_anterior(query_api, influx_org: str, influx_bucket: str,
                             alarm_id: str, log: logging.Logger) -> str:
    """
    Recupera el último estado registrado en alarm_notifications para esta alarma.
    Si no existe ningún registro previo, asume OK (primera ejecución).
    """
    query = f'''
        from(bucket: "{influx_bucket}")
          |> range(start: 1970-01-01T00:00:00Z)
          |> filter(fn: (r) => r["_measurement"] == "{NOTIFICATIONS_MEASUREMENT}")
          |> filter(fn: (r) => r["alarm_id"] == "{alarm_id}")
          |> filter(fn: (r) => r["_field"] == "estado")
          |> last()
    '''
    tablas = query_api.query(query, org=influx_org)
    if tablas and len(tablas) > 0 and len(tablas[0].records) > 0:
        return tablas[0].records[0].get_value()
    log.debug(f"'{alarm_id}': sin historial en alarm_notifications, asumiendo OK")
    return "OK"

def escribir_cambio_estado(write_api, influx_org: str, influx_bucket: str,
                            alarm_id: str, nombre_alarma: str,
                            nuevo_estado: str, ultimo_valor,
                            log: logging.Logger) -> None:
    """
    Escribe un nuevo punto en alarm_notifications marcando el cambio de estado.
    notificado=False indica que el alarmNotifier aún no ha enviado el correo.
    """
    point = (
        Point(NOTIFICATIONS_MEASUREMENT)
        .tag("alarm_id", alarm_id)
        .field("nombre_alarma", nombre_alarma)
        .field("estado",        nuevo_estado)
        .field("ultimo_valor",  str(ultimo_valor))
        .field("notificado",    False)
    )
    write_api.write(bucket=influx_bucket, org=influx_org, record=point)
    log.debug(f"'{alarm_id}': punto escrito en {NOTIFICATIONS_MEASUREMENT} "
              f"(estado={nuevo_estado}, notificado=False)")


# --- CARGA Y EVALUACIÓN ---

def cargar_alarmas(log: logging.Logger) -> list:
    log.debug("Leyendo alarmas desde stdin")
    try:
        alarmas = json.load(sys.stdin)
    except Exception as e:
        log.error(f"Error leyendo alarmas desde stdin: {e}", exc_info=True)
        raise
    log.info(f"Cargadas {len(alarmas)} alarma(s) desde stdin")
    return alarmas

def construir_query_flux(alarma: dict, influx_bucket: str, log: logging.Logger) -> str:
    categoria = alarma["categoria"]
    objetivo  = alarma["objetivo"]

    query = f'from(bucket: "{influx_bucket}") |> range(start: 1970-01-01T00:00:00Z)'

    if categoria == "servicio":
        query += f' |> filter(fn: (r) => r["_measurement"] == "servicio_gen")'
        query += f' |> filter(fn: (r) => r["nombre"] == "{objetivo}")'
        query += f' |> filter(fn: (r) => r["_field"] == "status_code")'

    elif categoria == "docker":
        query += f' |> filter(fn: (r) => r["_measurement"] == "docker_gen")'
        query += f' |> filter(fn: (r) => r["nombre"] == "{objetivo}")'
        if alarma["tipo"] == "estado":
            query += f' |> filter(fn: (r) => r["_field"] == "status_code")'
        else:
            metrica = alarma["metrica"]
            query += f' |> filter(fn: (r) => r["_field"] == "{metrica}")'

    elif categoria == "servidor":
        metrica = alarma["metrica"]
        query += f' |> filter(fn: (r) => r["_field"] == "{metrica}")'

    query += ' |> last()'
    log.debug(f"Query para '{alarma['nombre']}': {query}")
    return query

def evaluar_condicion(alarma: dict, valor, log: logging.Logger) -> str:
    if valor is None:
        log.debug(f"'{alarma['nombre']}': sin datos en InfluxDB → CRITICAL")
        return "CRITICAL"

    if alarma["tipo"] == "estado":
        resultado = "OK" if valor == 200 else "CRITICAL"
        log.debug(f"'{alarma['nombre']}': status_code={valor} → {resultado}")
        return resultado

    elif alarma["tipo"] == "metrica":
        op     = alarma["operador"]
        umbral = float(alarma["umbral"])
        v      = float(valor)
        operadores = {
            ">":  v > umbral,
            "<":  v < umbral,
            ">=": v >= umbral,
            "<=": v <= umbral,
        }
        es_critico = operadores.get(op)
        if es_critico is None:
            raise ValueError(f"Operador desconocido: '{op}'")
        resultado = "CRITICAL" if es_critico else "OK"
        log.debug(f"'{alarma['nombre']}': {v} {op} {umbral} → {resultado}")
        return resultado

    return "OK"


def procesar_alarmas(query_api, write_api, influx_org: str, influx_bucket: str,
                     log: logging.Logger) -> list:
    alarmas = cargar_alarmas(log)
    cambios = []

    for alarma in alarmas:
        nombre_alarma = alarma["nombre"]
        alarm_id      = f"alarma:{nombre_alarma.replace(' ', '_').lower()}"

        log.debug(f"Procesando '{nombre_alarma}'")

        try:
            # 1. Valor actual desde InfluxDB
            query        = construir_query_flux(alarma, influx_bucket, log)
            tablas       = query_api.query(query, org=influx_org)
            valor_actual = None
            if tablas and len(tablas) > 0 and len(tablas[0].records) > 0:
                valor_actual = tablas[0].records[0].get_value()
                log.debug(f"'{nombre_alarma}': valor obtenido = {valor_actual}")
            else:
                log.debug(f"'{nombre_alarma}': query sin resultados")

            # 2. Evaluar estado actual
            nuevo_estado = evaluar_condicion(alarma, valor_actual, log)

            # 3. Estado anterior desde alarm_notifications
            estado_anterior = obtener_estado_anterior(
                query_api, influx_org, influx_bucket, alarm_id, log
            )

            # 4. Escribir en InfluxDB solo si hay cambio
            if nuevo_estado != estado_anterior:
                log.info(f"Cambio en '{nombre_alarma}': "
                         f"{estado_anterior} → {nuevo_estado} (valor: {valor_actual})")
                escribir_cambio_estado(
                    write_api, influx_org, influx_bucket,
                    alarm_id, nombre_alarma, nuevo_estado, valor_actual, log
                )
                cambios.append({
                    "alarm_id": alarm_id,
                    "nombre": nombre_alarma,
                    "estado_anterior": estado_anterior,
                    "estado_nuevo": nuevo_estado,
                    "valor": valor_actual,
                    "responsable": alarma["responsable"]
                })
            else:
                log.debug(f"'{nombre_alarma}': sin cambios ({estado_anterior})")

        except Exception as e:
            log.error(f"Error procesando '{nombre_alarma}': {e}", exc_info=True)

    log.info("Ciclo de evaluación completado")
    return cambios


if __name__ == "__main__":
    args = parsear_argumentos()

    log = configurar_logger("alarmMonitor", args.log_dir)

    log.debug(f"Conectando a InfluxDB en {args.influx_url} | org={args.influx_org} | bucket={args.influx_bucket}")
    client_influx = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    query_api     = client_influx.query_api()
    write_api     = client_influx.write_api(write_options=SYNCHRONOUS)

    try:
        cambios = procesar_alarmas(query_api, write_api, args.influx_org, args.influx_bucket, log)
        print(json.dumps(cambios))
    except Exception as e:
        log.error(f"Error general en el evaluador: {e}", exc_info=True)
        raise