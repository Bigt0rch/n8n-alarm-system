#!/usr/bin/env python3
import argparse
import json
import logging
import os
import requests
from datetime import datetime
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import sys
from typing import Any

NOTIFICATIONS_MEASUREMENT = "alarm_notifications"
SERVICIO_MEASUREMENT      = "servicio_gen"
DOCKER_STATUS_MEASUREMENT = "docker_gen"  # monitor_docker.py escribe aquí

UMBRAL_RECURSO_CRITICO = 95.0  # % a partir del cual un recurso se considera saturado

# Recursos permitidos para alarmas de tipo metrica.
# Solo se permiten porcentajes de uso: CPU total, RAM y disco raíz.
RECURSOS_SERVIDOR_PERMITIDOS = {"cpu", "ram", "disco"}
RECURSOS_DOCKER_PERMITIDOS   = {"cpu", "ram"}  # Telegraf Docker no aporta % de disco raíz del contenedor

ALIASES_RECURSO = {
    "cpu": "cpu",
    "ram": "ram",
    "mem": "ram",
    "memoria": "ram",
    "disco": "disco",
    "disk": "disco",
}

OPERADORES_VALIDOS = {">", "<", ">=", "<="}

# --- PARÁMETROS ---

USO = """
Uso:
  cat alarms.json | python alarmMonitorDBn8n.py \\
      --influx-url    <URL>          \\
      --influx-token  <TOKEN>        \\
      --influx-org    <ORG>          \\
      --influx-bucket <BUCKET>       \\
      --dockers-file  <dockers.json> \\
      [--log-dir      <RUTA>]

Parámetros obligatorios:
  --influx-url      URL del servidor InfluxDB          (ej: http://localhost:8086)
  --influx-token    Token de autenticación de InfluxDB
  --influx-org      Nombre de la organización en InfluxDB
  --influx-bucket   Nombre del bucket en InfluxDB
  --dockers-file    Ruta al fichero dockers.json

Parámetros opcionales:
  --log-dir         Directorio donde se guardan los logs (ej: ~/.n8n-files/logs)
                    Si se omite, los logs solo se emiten por consola.

Formato de alarmas soportado:
  - servicio + estado:
      objetivo = alias/nombre lógico del servicio
      url      = URL a comprobar
      OK       = 200 o 403

  - docker + estado:
      objetivo = alias definido en dockers.json
      origen   = medición http_response generada por monitor_docker.py
      OK       = solo 200

  - servidor + metrica:
      objetivo = host de Telegraf
      recurso  = cpu | ram | disco
      operador = > | < | >= | <=
      umbral   = porcentaje numérico entre 0 y 100

  - docker + metrica:
      objetivo = nombre real del contenedor, el que aparece en:
                 docker ps -a --format "{{.Names}}"
      recurso  = cpu | ram
      operador = > | < | >= | <=
      umbral   = porcentaje numérico entre 0 y 100
"""


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor de alarmas: evalúa estado y escribe cambios en InfluxDB.",
        add_help=True,
    )
    parser.add_argument("--influx-url",    required=True,  help="URL del servidor InfluxDB")
    parser.add_argument("--influx-token",  required=True,  help="Token de autenticación de InfluxDB")
    parser.add_argument("--influx-org",    required=True,  help="Organización de InfluxDB")
    parser.add_argument("--influx-bucket", required=True,  help="Bucket de InfluxDB")
    parser.add_argument("--dockers-file",  required=True,  help="Ruta al fichero dockers.json")
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
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
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


# --- UTILIDADES ---


def flux_escape(valor: Any) -> str:
    """Escapa un valor para insertarlo como string literal en una query Flux."""
    return str(valor).replace("\\", "\\\\").replace('"', '\\"')


def normalizar_recurso(valor: Any) -> str | None:
    if valor is None:
        return None
    return ALIASES_RECURSO.get(str(valor).strip().lower())


def obtener_nombre_alarma(alarma: dict, indice: int | None = None) -> str:
    if isinstance(alarma, dict) and alarma.get("nombre"):
        return str(alarma["nombre"])
    if indice is not None:
        return f"#{indice}"
    return "<sin nombre>"


def query_last_value(query_api, influx_org: str, query: str, log: logging.Logger,
                     descripcion: str) -> Any | None:
    """Ejecuta una query Flux que termina en last() y devuelve el valor, o None si no hay datos."""
    log.debug(f"Query {descripcion}: {query}")
    tablas = query_api.query(query, org=influx_org)
    if not tablas:
        log.debug(f"{descripcion}: sin tablas")
        return None

    for tabla in tablas:
        if tabla.records:
            valor = tabla.records[-1].get_value()
            log.debug(f"{descripcion}: valor obtenido = {valor}")
            return valor

    log.debug(f"{descripcion}: sin registros")
    return None


# --- CARGA DE CONFIGURACIÓN ---


def cargar_dockers(ruta: str, log: logging.Logger) -> dict:
    """
    Carga dockers.json y devuelve un dict indexado por container_name:
    {
      container_name: { alias, keywords, maquina_virtual, ... }
    }
    """
    ruta = os.path.expanduser(ruta)
    try:
        with open(ruta, encoding="utf-8") as f:
            lista = json.load(f)
    except Exception as e:
        log.error(f"No se pudo cargar dockers.json desde {ruta}: {e}", exc_info=True)
        return {}

    if not isinstance(lista, list):
        log.error(f"dockers.json debe contener una lista de contenedores. Ruta: {ruta}")
        return {}

    indice = {d["container_name"]: d for d in lista
              if isinstance(d, dict) and "container_name" in d}
    log.debug(f"dockers.json cargado: {len(indice)} contenedor(es)")
    return indice


# --- VALIDACIÓN DE ALARMAS ---


def validar_alarma(alarma: Any, indice: int, indice_dockers: dict) -> tuple[list[str], list[str]]:
    errores: list[str] = []
    avisos: list[str] = []

    if not isinstance(alarma, dict):
        return [f"Alarma #{indice}: debe ser un objeto JSON"], []

    nombre = obtener_nombre_alarma(alarma, indice)

    obligatorios = ["nombre", "tipo", "categoria", "objetivo", "responsable"]
    for campo in obligatorios:
        if campo not in alarma or alarma[campo] in (None, ""):
            errores.append(f"Alarma '{nombre}': falta el campo obligatorio '{campo}'")

    if errores:
        return errores, avisos

    tipo = str(alarma["tipo"]).strip().lower()
    categoria = str(alarma["categoria"]).strip().lower()

    if tipo not in ("estado", "metrica"):
        errores.append(f"Alarma '{nombre}': tipo inválido '{alarma['tipo']}'. Debe ser 'estado' o 'metrica'")

    if categoria not in ("servicio", "docker", "servidor"):
        errores.append(f"Alarma '{nombre}': categoria inválida '{alarma['categoria']}'. Debe ser 'servicio', 'docker' o 'servidor'")

    if errores:
        return errores, avisos

    # Combinaciones permitidas
    if categoria == "servicio" and tipo != "estado":
        errores.append(f"Alarma '{nombre}': categoria 'servicio' solo soporta tipo 'estado'")

    if categoria == "servidor" and tipo != "metrica":
        errores.append(f"Alarma '{nombre}': categoria 'servidor' solo soporta tipo 'metrica'")

    if categoria == "servicio":
        if "url" not in alarma or not alarma["url"]:
            errores.append(f"Alarma '{nombre}': falta 'url' para categoria 'servicio'")

    if tipo == "estado":
        if categoria not in ("servicio", "docker"):
            errores.append(f"Alarma '{nombre}': tipo 'estado' solo está soportado para 'servicio' o 'docker'")

    if tipo == "metrica":
        for campo in ["recurso", "operador", "umbral"]:
            if campo not in alarma or alarma[campo] in (None, ""):
                errores.append(f"Alarma '{nombre}': falta '{campo}' para tipo 'metrica'")

        recurso = normalizar_recurso(alarma.get("recurso"))
        if recurso is None:
            errores.append(
                f"Alarma '{nombre}': recurso inválido '{alarma.get('recurso')}'. "
                "Valores permitidos: cpu, ram, disco"
            )
        else:
            if categoria == "servidor" and recurso not in RECURSOS_SERVIDOR_PERMITIDOS:
                errores.append(
                    f"Alarma '{nombre}': recurso '{recurso}' no permitido para servidor. "
                    "Permitidos: cpu, ram, disco"
                )

            if categoria == "docker" and recurso not in RECURSOS_DOCKER_PERMITIDOS:
                errores.append(
                    f"Alarma '{nombre}': recurso '{recurso}' no permitido para docker. "
                    "Permitidos actualmente: cpu, ram. "
                    "No se permite disco porque Telegraf Docker no proporciona % de disco raíz del contenedor."
                )

            if categoria == "servicio":
                errores.append(f"Alarma '{nombre}': no se permiten métricas de categoria 'servicio'")

        if alarma.get("operador") not in OPERADORES_VALIDOS:
            errores.append(
                f"Alarma '{nombre}': operador inválido '{alarma.get('operador')}'. "
                "Permitidos: >, <, >=, <="
            )

        try:
            umbral = float(alarma.get("umbral"))
            if not 0 <= umbral <= 100:
                errores.append(f"Alarma '{nombre}': umbral debe ser un porcentaje entre 0 y 100")
        except (TypeError, ValueError):
            errores.append(f"Alarma '{nombre}': umbral debe ser numérico")

    if categoria == "docker" and tipo == "estado":
        alias_objetivo = str(alarma["objetivo"])
        aliases = {str(d.get("alias")) for d in indice_dockers.values() if d.get("alias")}
        if aliases and alias_objetivo not in aliases:
            avisos.append(
                f"Alarma '{nombre}': objetivo='{alias_objetivo}' no aparece como alias en dockers.json. "
                "Se procesará igualmente, pero puede no encontrar datos en http_response."
            )

    if categoria == "docker" and tipo == "metrica":
        # En métricas Docker el objetivo debe ser el nombre real del contenedor, no el alias.
        container_names = {str(k) for k in indice_dockers.keys()}
        objetivo = str(alarma["objetivo"])
        if container_names and objetivo not in container_names:
            avisos.append(
                f"Alarma '{nombre}': objetivo='{objetivo}' no aparece como container_name en dockers.json. "
                "Debe coincidir con docker ps -a --format '{{.Names}}'. Se procesará igualmente."
            )

    return errores, avisos


# --- CHEQUEO HTTP DE SERVICIOS ---


def verificar_servicio(url: str, log: logging.Logger) -> int:
    """
    Realiza una petición HTTP GET a la URL del servicio y devuelve el status_code.
    Ante cualquier error de conexión devuelve 500.
    """
    try:
        response = requests.get(url, timeout=2)
        log.debug(f"HTTP GET {url} → {response.status_code}")
        return int(response.status_code)
    except requests.exceptions.RequestException as e:
        log.debug(f"HTTP GET {url} → Error: {e}")
        return 500


def escribir_servicio_gen(write_api, influx_org: str, influx_bucket: str,
                           nombre: str, status_code: int,
                           log: logging.Logger) -> None:
    """Escribe el resultado del chequeo HTTP en la medición servicio_gen."""
    point = (
        Point(SERVICIO_MEASUREMENT)
        .tag("nombre", nombre)
        .field("status_code", int(status_code))
    )
    write_api.write(bucket=influx_bucket, org=influx_org, record=point)
    log.debug(f"{SERVICIO_MEASUREMENT}[{nombre}].status_code = {status_code} escrito en InfluxDB")


# --- CONSULTAS Y ESCRITURAS A alarm_notifications ---


def obtener_estado_anterior(query_api, influx_org: str, influx_bucket: str,
                             alarm_id: str, log: logging.Logger) -> str:
    """
    Recupera el último estado registrado en alarm_notifications para esta alarma.
    Si no existe ningún registro previo, asume OK.
    """
    query = f'''
        from(bucket: "{flux_escape(influx_bucket)}")
          |> range(start: 1970-01-01T00:00:00Z)
          |> filter(fn: (r) => r["_measurement"] == "{NOTIFICATIONS_MEASUREMENT}")
          |> filter(fn: (r) => r["alarm_id"] == "{flux_escape(alarm_id)}")
          |> filter(fn: (r) => r["_field"] == "estado")
          |> last()
    '''
    valor = query_last_value(query_api, influx_org, query, log,
                             f"estado anterior alarm_id={alarm_id}")
    if valor is None:
        log.debug(f"'{alarm_id}': sin historial en {NOTIFICATIONS_MEASUREMENT}, asumiendo OK")
        return "OK"
    return str(valor)


def escribir_cambio_estado(write_api, influx_org: str, influx_bucket: str,
                            alarm_id: str, nombre_alarma: str,
                            nuevo_estado: str, ultimo_valor: Any,
                            categoria: str, contexto: str,
                            log: logging.Logger) -> None:
    """
    Escribe un nuevo punto en alarm_notifications marcando el cambio de estado.
    notificado=False indica que el alarmNotifier aún no ha enviado el correo.
    """
    point = (
        Point(NOTIFICATIONS_MEASUREMENT)
        .tag("alarm_id", alarm_id)
        .tag("categoria", categoria)
        .field("nombre_alarma", nombre_alarma)
        .field("estado",        nuevo_estado)
        .field("ultimo_valor",  str(ultimo_valor))
        .field("contexto",      contexto)
        .field("notificado",    False)
    )
    write_api.write(bucket=influx_bucket, org=influx_org, record=point)
    log.debug(f"'{alarm_id}': punto escrito en {NOTIFICATIONS_MEASUREMENT} "
              f"(estado={nuevo_estado}, notificado=False)")


# --- CONSULTAS DE ESTADO DOCKER ---


def obtener_estado_docker(query_api, influx_org: str, influx_bucket: str,
                           alias: str, log: logging.Logger) -> str | None:
    """
    Consulta el último status_code registrado por monitor_docker.py en http_response.
    En monitor_docker.py el tag nombre corresponde al alias de dockers.json.
    """
    query = f'''
        from(bucket: "{flux_escape(influx_bucket)}")
          |> range(start: 1970-01-01T00:00:00Z)
          |> filter(fn: (r) => r["_measurement"] == "{DOCKER_STATUS_MEASUREMENT}")
          |> filter(fn: (r) => r["nombre"] == "{flux_escape(alias)}")
          |> filter(fn: (r) => r["_field"] == "status_code")
          |> last()
    '''
    codigo = query_last_value(query_api, influx_org, query, log,
                              f"estado docker alias={alias}")
    if codigo is None:
        log.debug(f"{DOCKER_STATUS_MEASUREMENT}: sin datos para alias '{alias}'")
        return None

    ESTADOS = {
        200: "running (OK)",
        500: "no running (CRITICAL)",
        404: "no encontrado (CRITICAL)",
    }
    try:
        codigo_int = int(float(codigo))
    except (TypeError, ValueError):
        descripcion = f"código no numérico ({codigo})"
    else:
        descripcion = ESTADOS.get(codigo_int, f"código desconocido ({codigo_int})")

    log.debug(f"{DOCKER_STATUS_MEASUREMENT}[{alias}].status_code={codigo} → {descripcion}")
    return descripcion


# --- CONSULTAS DE RECURSOS TELEGRAF ---


def obtener_porcentaje_servidor(query_api, influx_org: str, influx_bucket: str,
                                 host: str, recurso: str,
                                 log: logging.Logger) -> float | None:
    """
    Devuelve el porcentaje de uso del recurso de un host.

    Recursos soportados:
      - cpu:   CPU total activa = 100 - cpu.usage_idle, usando cpu=cpu-total si existe.
      - ram:   mem.used_percent.
      - disco: disk.used_percent con path='/'.
    """
    host_e = flux_escape(host)
    bucket_e = flux_escape(influx_bucket)

    if recurso == "cpu":
        query = f'''
            from(bucket: "{bucket_e}")
              |> range(start: 1970-01-01T00:00:00Z)
              |> filter(fn: (r) => r["_measurement"] == "cpu")
              |> filter(fn: (r) => r["host"] == "{host_e}")
              |> filter(fn: (r) => r["_field"] == "usage_idle")
              |> filter(fn: (r) => not exists r.cpu or r.cpu == "cpu-total")
              |> last()
        '''
        idle = query_last_value(query_api, influx_org, query, log,
                                f"cpu total host={host}")
        if idle is None:
            return None
        return round(max(0.0, min(100.0, 100.0 - float(idle))), 1)

    if recurso == "ram":
        query = f'''
            from(bucket: "{bucket_e}")
              |> range(start: 1970-01-01T00:00:00Z)
              |> filter(fn: (r) => r["_measurement"] == "mem")
              |> filter(fn: (r) => r["host"] == "{host_e}")
              |> filter(fn: (r) => r["_field"] == "used_percent")
              |> last()
        '''
        valor = query_last_value(query_api, influx_org, query, log,
                                 f"ram host={host}")
        return None if valor is None else round(float(valor), 1)

    if recurso == "disco":
        query = f'''
            from(bucket: "{bucket_e}")
              |> range(start: 1970-01-01T00:00:00Z)
              |> filter(fn: (r) => r["_measurement"] == "disk")
              |> filter(fn: (r) => r["host"] == "{host_e}")
              |> filter(fn: (r) => r["path"] == "/")
              |> filter(fn: (r) => r["_field"] == "used_percent")
              |> last()
        '''
        valor = query_last_value(query_api, influx_org, query, log,
                                 f"disco raiz host={host}")
        return None if valor is None else round(float(valor), 1)

    raise ValueError(f"Recurso servidor no soportado: {recurso}")


def obtener_porcentaje_docker(query_api, influx_org: str, influx_bucket: str,
                               container_name: str, recurso: str,
                               log: logging.Logger) -> float | None:
    """
    Devuelve el porcentaje de uso de un recurso de contenedor desde Telegraf Docker.

    Recursos soportados:
      - cpu: docker_container_cpu.usage_percent
      - ram: docker_container_mem.usage_percent

    El objetivo debe coincidir con el nombre real del contenedor, tal como aparece en:
      docker ps -a --format "{{.Names}}"
    """
    if recurso == "disco":
        raise ValueError("No se soportan alarmas de disco para docker: Telegraf Docker no aporta % de disco raíz del contenedor")

    mapping = {
        "cpu": {
            "measurement": "docker_container_cpu",
            "field": "usage_percent",
            "container_tag": "container_name",
        },
        "ram": {
            "measurement": "docker_container_mem",
            "field": "usage_percent",
            "container_tag": "container_name",
        },
    }

    if recurso not in mapping:
        raise ValueError(f"Recurso docker no soportado: {recurso}")

    cfg = mapping[recurso]
    query = f'''
        from(bucket: "{flux_escape(influx_bucket)}")
          |> range(start: 1970-01-01T00:00:00Z)
          |> filter(fn: (r) => r["_measurement"] == "{cfg['measurement']}")
          |> filter(fn: (r) => r["{cfg['container_tag']}"] == "{flux_escape(container_name)}")
          |> filter(fn: (r) => r["_field"] == "{cfg['field']}")
          |> last()
    '''
    valor = query_last_value(query_api, influx_org, query, log,
                             f"docker {recurso} container_name={container_name}")
    return None if valor is None else round(float(valor), 1)


def obtener_porcentaje_recurso(alarma: dict, query_api,
                                influx_org: str, influx_bucket: str,
                                log: logging.Logger) -> float | None:
    categoria = str(alarma["categoria"]).strip().lower()
    objetivo = str(alarma["objetivo"])
    recurso = normalizar_recurso(alarma.get("recurso"))

    if recurso is None:
        raise ValueError(f"Recurso inválido: {alarma.get('recurso')}")

    if categoria == "servidor":
        return obtener_porcentaje_servidor(query_api, influx_org, influx_bucket,
                                           objetivo, recurso, log)

    if categoria == "docker":
        return obtener_porcentaje_docker(query_api, influx_org, influx_bucket,
                                         objetivo, recurso, log)

    raise ValueError(f"No se soportan métricas para categoria={categoria}")


def obtener_recursos_vm(query_api, influx_org: str, influx_bucket: str,
                         host: str, log: logging.Logger) -> dict:
    """
    Consulta los últimos porcentajes de CPU total, RAM y Disco / del host indicado.
    Devuelve { "cpu": float|None, "ram": float|None, "disco": float|None }.
    """
    recursos = {
        "cpu": obtener_porcentaje_servidor(query_api, influx_org, influx_bucket, host, "cpu", log),
        "ram": obtener_porcentaje_servidor(query_api, influx_org, influx_bucket, host, "ram", log),
        "disco": obtener_porcentaje_servidor(query_api, influx_org, influx_bucket, host, "disco", log),
    }
    log.debug(f"Recursos VM '{host}': {recursos}")
    return recursos


# --- DIAGNÓSTICO DE CONTEXTO ---


def construir_contexto(alarma: dict, indice_dockers: dict,
                        query_api, influx_org: str, influx_bucket: str,
                        nuevo_estado: str, log: logging.Logger) -> str:
    """
    Genera el string de contexto que acompaña a una transición OK→CRITICAL
    en una alarma de categoría 'servicio'.

    Solo se invoca cuando nuevo_estado == CRITICAL para no hacer consultas
    innecesarias en recuperaciones CRITICAL→OK.
    """
    if nuevo_estado != "CRITICAL":
        return ""

    partes = []

    # Docker asociado al servicio
    container_name = alarma.get("container_name")
    if container_name:
        info_docker = indice_dockers.get(container_name)
        alias_consulta = info_docker.get("alias") if info_docker else container_name
        estado_docker = obtener_estado_docker(
            query_api, influx_org, influx_bucket, alias_consulta, log
        )
        if estado_docker:
            partes.append(f"Docker: '{container_name}' (alias '{alias_consulta}')@@NL@@ Estado docker: {estado_docker}.")
        else:
            partes.append(f"Docker: '{container_name}' (alias '{alias_consulta}')@@NL@@ Estado docker: sin datos en InfluxDB sobre el estado del contenedor, registrelo en dockers.json si desea que se realice un seguimiento de su estado.")
    else:
        partes.append("Sin contenedor Docker asociado a este servicio.")

    partes.append("@@NL@@")

    # Recursos de VM asociada
    maquina_virtual = alarma.get("maquina_virtual")
    if maquina_virtual:
        recursos = obtener_recursos_vm(
            query_api, influx_org, influx_bucket, maquina_virtual, log
        )
        saturados = []
        lineas_recursos = []

        for nombre_rec, valor in [
            ("@@NL@@  • CPU total", recursos["cpu"]),
            ("@@NL@@  • RAM", recursos["ram"]),
            ("@@NL@@  • Disco /", recursos["disco"]),
        ]:
            if valor is None:
                lineas_recursos.append(f"{nombre_rec}: sin datos")
            else:
                lineas_recursos.append(f"{nombre_rec}: {valor}%")
                if valor >= UMBRAL_RECURSO_CRITICO:
                    saturados.append(f"{nombre_rec} ({valor}%)")

        partes.append(f"Maquina virtual: '{maquina_virtual}' — {', '.join(lineas_recursos)}.")

        if saturados:
            partes.append(
                f"@@NL@@POSIBLE CAUSA: los siguientes recursos están saturados "
                f"(su valor es mayor o igual al {UMBRAL_RECURSO_CRITICO}%): "
                f"{', '.join(saturados)}."
            )
        else:
            partes.append(
                "@@NL@@Los recursos de la maquina virtual no parecen estar relacionados con el fallo "
                f"(ninguno supera el {UMBRAL_RECURSO_CRITICO}%)."
            )
    else:
        partes.append("Sin máquina virtual asociada a este servicio. Defina el nombre del host en el archivo alarmas.json")

    return " ".join(partes)


# --- CARGA Y EVALUACIÓN ---


def cargar_alarmas(log: logging.Logger) -> list:
    log.debug("Leyendo alarmas desde stdin")
    try:
        alarmas = json.load(sys.stdin)
    except Exception as e:
        log.error(f"Error leyendo alarmas desde stdin: {e}", exc_info=True)
        raise

    if not isinstance(alarmas, list):
        raise ValueError("La entrada por stdin debe ser una lista JSON de alarmas")

    log.info(f"Cargadas {len(alarmas)} alarma(s) desde stdin")
    return alarmas


def construir_query_estado_docker(alarma: dict, influx_bucket: str, log: logging.Logger) -> str:
    """
    Construye la query de estado Docker.
    Para docker+estado, objetivo debe ser el alias de dockers.json porque monitor_docker.py
    escribe http_response con tag nombre=<alias>.
    """
    objetivo = alarma["objetivo"]
    query = f'''
        from(bucket: "{flux_escape(influx_bucket)}")
          |> range(start: 1970-01-01T00:00:00Z)
          |> filter(fn: (r) => r["_measurement"] == "{DOCKER_STATUS_MEASUREMENT}")
          |> filter(fn: (r) => r["nombre"] == "{flux_escape(objetivo)}")
          |> filter(fn: (r) => r["_field"] == "status_code")
          |> last()
    '''
    log.debug(f"Query estado docker para '{alarma['nombre']}': {query}")
    return query


def evaluar_condicion(alarma: dict, valor: Any, log: logging.Logger) -> str:
    nombre = alarma["nombre"]
    tipo = str(alarma["tipo"]).strip().lower()
    categoria = str(alarma["categoria"]).strip().lower()

    if valor is None:
        log.debug(f"'{nombre}': sin datos en InfluxDB → CRITICAL")
        return "CRITICAL"

    if tipo == "estado":
        try:
            codigo = int(float(valor))
        except (TypeError, ValueError):
            log.debug(f"'{nombre}': status_code no numérico ({valor}) → CRITICAL")
            return "CRITICAL"

        if categoria == "servicio":
            codigos_ok = {200, 403}
        elif categoria == "docker":
            codigos_ok = {200}
        else:
            codigos_ok = {200}

        resultado = "OK" if codigo in codigos_ok else "CRITICAL"
        log.debug(f"'{nombre}': status_code={codigo}, categoria={categoria} → {resultado}")
        return resultado

    if tipo == "metrica":
        op = alarma["operador"]
        umbral = float(alarma["umbral"])
        v = float(valor)

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
        log.debug(f"'{nombre}': {v}% {op} {umbral}% → {resultado}")
        return resultado

    return "OK"


# --- PROCESAMIENTO DE UNA ALARMA ---


def obtener_valor_actual(alarma: dict, query_api, write_api,
                         influx_org: str, influx_bucket: str,
                         log: logging.Logger) -> Any | None:
    categoria = str(alarma["categoria"]).strip().lower()
    tipo = str(alarma["tipo"]).strip().lower()

    if categoria == "servicio" and tipo == "estado":
        url = alarma["url"]
        valor_actual = verificar_servicio(url, log)
        escribir_servicio_gen(write_api, influx_org, influx_bucket,
                              alarma["objetivo"], valor_actual, log)
        return valor_actual

    if categoria == "docker" and tipo == "estado":
        query = construir_query_estado_docker(alarma, influx_bucket, log)
        return query_last_value(query_api, influx_org, query, log,
                                f"estado docker alarma={alarma['nombre']}")

    if tipo == "metrica":
        return obtener_porcentaje_recurso(alarma, query_api, influx_org,
                                          influx_bucket, log)

    raise ValueError(f"Combinación no soportada: categoria={categoria}, tipo={tipo}")


def procesar_alarma(alarma: dict, indice_dockers: dict,
                    query_api, write_api,
                    influx_org: str, influx_bucket: str,
                    log: logging.Logger) -> dict | None:
    nombre_alarma = alarma["nombre"]
    alarm_id      = f"alarma:{nombre_alarma.replace(' ', '_').lower()}"
    categoria     = str(alarma["categoria"]).strip().lower()

    log.debug(f"Procesando '{nombre_alarma}'")

    # 1. Obtener valor actual
    valor_actual = obtener_valor_actual(
        alarma, query_api, write_api, influx_org, influx_bucket, log
    )

    # 2. Evaluar estado actual
    nuevo_estado = evaluar_condicion(alarma, valor_actual, log)

    # 3. Estado anterior desde alarm_notifications
    estado_anterior = obtener_estado_anterior(
        query_api, influx_org, influx_bucket, alarm_id, log
    )

    # 4. Si hay transición → construir contexto, escribir y devolver
    if nuevo_estado != estado_anterior:
        log.info(f"Cambio en '{nombre_alarma}': "
                 f"{estado_anterior} → {nuevo_estado} (valor: {valor_actual})")

        contexto = ""
        if categoria == "servicio" and nuevo_estado == "CRITICAL":
            contexto = construir_contexto(
                alarma, indice_dockers,
                query_api, influx_org, influx_bucket,
                nuevo_estado, log
            )
            if contexto:
                log.info(f"  Contexto: {contexto}")

        escribir_cambio_estado(
            write_api, influx_org, influx_bucket,
            alarm_id, nombre_alarma, nuevo_estado, valor_actual,
            categoria, contexto, log
        )
        return {
            "alarm_id":        alarm_id,
            "nombre":          nombre_alarma,
            "estado_anterior": estado_anterior,
            "estado_nuevo":    nuevo_estado,
            "valor":           valor_actual,
            "responsable":     alarma["responsable"],
            "contexto":        contexto,
        }

    log.debug(f"'{nombre_alarma}': sin cambios ({estado_anterior})")
    return None


def procesar_alarmas(indice_dockers: dict,
                     query_api, write_api,
                     influx_org: str, influx_bucket: str,
                     log: logging.Logger) -> list:
    alarmas = cargar_alarmas(log)
    cambios = []

    for i, alarma in enumerate(alarmas, start=1):
        errores, avisos = validar_alarma(alarma, i, indice_dockers)

        for aviso in avisos:
            log.warning(aviso)

        if errores:
            for error in errores:
                log.error(error)
            continue

        try:
            resultado = procesar_alarma(
                alarma, indice_dockers,
                query_api, write_api,
                influx_org, influx_bucket,
                log,
            )
            if resultado:
                cambios.append(resultado)
        except Exception as e:
            nombre = obtener_nombre_alarma(alarma, i)
            log.error(f"Error procesando '{nombre}': {e}", exc_info=True)

    log.info("Ciclo de evaluación completado")
    return cambios


if __name__ == "__main__":
    args = parsear_argumentos()

    log = configurar_logger("alarmMonitor", args.log_dir)

    log.debug(f"Conectando a InfluxDB en {args.influx_url} | "
              f"org={args.influx_org} | bucket={args.influx_bucket}")
    client_influx = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    query_api     = client_influx.query_api()
    write_api     = client_influx.write_api(write_options=SYNCHRONOUS)

    indice_dockers = cargar_dockers(args.dockers_file, log)

    try:
        cambios = procesar_alarmas(
            indice_dockers,
            query_api, write_api,
            args.influx_org, args.influx_bucket,
            log,
        )
        print(json.dumps(cambios, ensure_ascii=False))
    except Exception as e:
        log.error(f"Error general en el evaluador: {e}", exc_info=True)
        raise

