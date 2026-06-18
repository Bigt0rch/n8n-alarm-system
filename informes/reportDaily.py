import argparse
import json
import logging
import os
import re
import sys

from datetime import datetime, timedelta, timezone

from reportGenerator import ejecutar_informe


USO = """
Uso:

python reportDaily.py \
    --alarm-config  alarmas.json \
    --dockers-file  dockers.json \
    --influx-url    <URL> \
    --influx-token  <TOKEN> \
    --influx-org    <ORG> \
    --influx-bucket <BUCKET> \
    --smtp-host     <HOST> \
    --smtp-port     <PUERTO> \
    --smtp-user     <USUARIO> \
    --smtp-password <PASSWORD> \
    --email-from    <REMITENTE> \
    [--output-dir   <RUTA>] \
    [--log-dir      <RUTA>]

El informe diario genera un PDF y un correo por responsable.
La relación responsable → alarmas → dockers → máquinas virtuales se reconstruye
exclusivamente desde alarmas.json y dockers.json, sin consultar responsables en InfluxDB.
"""


def parsear_argumentos():

    parser = argparse.ArgumentParser(
        description="Genera informes diarios por responsable."
    )

    parser.add_argument("--alarm-config", required=True)
    parser.add_argument("--dockers-file", required=True)

    parser.add_argument("--influx-url", required=True)
    parser.add_argument("--influx-token", required=True)
    parser.add_argument("--influx-org", required=True)
    parser.add_argument("--influx-bucket", required=True)

    parser.add_argument("--smtp-host", required=True)
    parser.add_argument("--smtp-port", required=True, type=int)
    parser.add_argument("--smtp-user", required=True)
    parser.add_argument("--smtp-password", required=True)

    parser.add_argument("--email-from", required=True)

    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--log-dir", default=None)

    parser.add_argument(
        "--umbral-uptime",
        type=float,
        default=95.0
    )

    try:
        return parser.parse_args()

    except SystemExit:
        logging.error(USO)
        sys.exit(2)


def configurar_logger(log_directory):

    logger = logging.getLogger("reportDaily")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    if log_directory:

        log_directory = os.path.expanduser(log_directory)

        os.makedirs(
            log_directory,
            exist_ok=True
        )

        logfile = os.path.join(
            log_directory,
            f"{datetime.now():%Y-%m-%d_%H-%M-%S}_reportDaily.log"
        )

        fh = logging.FileHandler(
            logfile,
            encoding="utf-8"
        )

        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)

        logger.addHandler(fh)

    ch = logging.StreamHandler()

    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    logger.addHandler(ch)

    return logger


def cargar_lista_json(ruta_json, descripcion, log):

    ruta_json = os.path.expanduser(ruta_json)

    try:
        with open(ruta_json, encoding="utf-8") as f:
            contenido = json.load(f)
    except Exception as exc:
        log.error(
            f"No se pudo cargar {descripcion} desde {ruta_json}: {exc}",
            exc_info=True
        )
        raise

    if not isinstance(contenido, list):
        raise ValueError(
            f"{descripcion} debe contener una lista JSON."
        )

    log.info(
        f"Cargados {len(contenido)} elemento(s) desde {ruta_json} ({descripcion})"
    )

    return contenido


def normalizar_alarm_id(nombre_alarma):
    """
    Replica el identificador usado por alarmMonitorDBn8n.py.
    """
    return f"alarma:{str(nombre_alarma).replace(' ', '_').lower()}"


def clave_no_vacia(valor):

    if valor is None:
        return None

    valor = str(valor).strip()

    return valor or None


def claves_docker_de_alarma(alarma):
    """
    Devuelve los posibles identificadores de Docker asociados a una alarma.

    Para alarmas de servicio se usa container_name, si existe.
    Para alarmas de docker se usan objetivo y container_name, porque según la
    configuración pueden apuntar al alias de dockers.json o al nombre real del
    contenedor.
    """
    claves = set()

    categoria = clave_no_vacia(alarma.get("categoria"))
    objetivo = clave_no_vacia(alarma.get("objetivo"))
    container_name = clave_no_vacia(alarma.get("container_name"))

    if container_name:
        claves.add(container_name)

    if categoria == "docker" and objetivo:
        claves.add(objetivo)

    return claves


def indexar_dockers(dockers):
    """
    Indexa dockers.json por container_name y alias.
    """
    indice = {}

    for docker in dockers:

        for campo in ("container_name", "alias"):

            clave = clave_no_vacia(docker.get(campo))

            if clave:
                indice[clave] = docker

    return indice


def indexar_alarmas_docker(alarmas):
    """
    Permite localizar alarmas de categoria docker por cualquiera de sus claves
    habituales: objetivo o container_name.
    """
    indice = {}

    for alarma in alarmas:

        if clave_no_vacia(alarma.get("categoria")) != "docker":
            continue

        nombre = clave_no_vacia(alarma.get("nombre"))

        if not nombre:
            continue

        alarm_id = normalizar_alarm_id(nombre)

        for clave in claves_docker_de_alarma(alarma):
            indice.setdefault(clave, set()).add(alarm_id)

    return indice


def construir_relaciones_por_responsable(alarmas, dockers, log):
    """
    Construye, solo desde alarmas.json y dockers.json:

      responsable -> alarm_ids que debe ver
      responsable -> hosts/VMs que debe ver

    Cada responsable recibe:
      - sus alarmas declaradas en alarmas.json;
      - las alarmas docker asociadas a los contenedores referenciados por sus
        alarmas, aunque esa alarma docker sea una relación indirecta;
      - las máquinas virtuales indicadas en sus alarmas y en dockers.json para
        los contenedores asociados.
    """
    dockers_por_clave = indexar_dockers(dockers)
    alarmas_docker_por_clave = indexar_alarmas_docker(alarmas)

    resultado = {}

    for alarma in alarmas:

        responsable = clave_no_vacia(alarma.get("responsable"))
        nombre = clave_no_vacia(alarma.get("nombre"))

        if not responsable or not nombre:
            log.warning(
                "Alarma ignorada por no tener responsable o nombre: %s",
                alarma
            )
            continue

        datos = resultado.setdefault(
            responsable,
            {
                "alarm_ids": set(),
                "hosts_vm": set(),
                "dockers": set(),
            }
        )

        # 1. La alarma declarada pertenece siempre a su responsable.
        datos["alarm_ids"].add(normalizar_alarm_id(nombre))

        # 2. La máquina virtual indicada directamente en la alarma también.
        host = clave_no_vacia(alarma.get("maquina_virtual"))
        if host:
            datos["hosts_vm"].add(host)

        # 3. Relación con Docker, si la alarma referencia un contenedor o es una
        #    alarma de categoria docker.
        for clave_docker in claves_docker_de_alarma(alarma):

            datos["dockers"].add(clave_docker)

            docker = dockers_por_clave.get(clave_docker)

            if docker:
                container_name = clave_no_vacia(docker.get("container_name"))
                alias = clave_no_vacia(docker.get("alias"))
                host_docker = clave_no_vacia(docker.get("maquina_virtual"))

                if container_name:
                    datos["dockers"].add(container_name)
                if alias:
                    datos["dockers"].add(alias)
                if host_docker:
                    datos["hosts_vm"].add(host_docker)

                claves_relacionadas = {
                    c for c in (clave_docker, container_name, alias) if c
                }
            else:
                claves_relacionadas = {clave_docker}
                log.debug(
                    "No se encontró '%s' en dockers.json al construir relaciones",
                    clave_docker
                )

            # 4. Si existe una alarma docker para ese contenedor, se añade para
            #    que el PDF incluya uptime/disparos del docker asociado.
            for clave_relacionada in claves_relacionadas:
                for alarm_id_docker in alarmas_docker_por_clave.get(
                    clave_relacionada,
                    set()
                ):
                    datos["alarm_ids"].add(alarm_id_docker)

    relaciones = {
        email: {
            "alarm_ids": sorted(datos["alarm_ids"]),
            "hosts_vm": sorted(datos["hosts_vm"]),
            "dockers": sorted(datos["dockers"]),
        }
        for email, datos in resultado.items()
    }

    for email, datos in relaciones.items():
        log.debug(
            "%s: %s alarma(s), %s host(s), %s docker(s)",
            email,
            len(datos["alarm_ids"]),
            len(datos["hosts_vm"]),
            len(datos["dockers"]),
        )

    return relaciones


def nombre_fichero_seguro(texto):

    texto = texto.strip().lower()
    texto = re.sub(r"[^a-z0-9._-]+", "_", texto)

    return texto.strip("_") or "responsable"


if __name__ == "__main__":

    args = parsear_argumentos()

    log = configurar_logger(
        args.log_dir
    )

    alarmas = cargar_lista_json(
        args.alarm_config,
        "alarmas.json",
        log
    )

    dockers = cargar_lista_json(
        args.dockers_file,
        "dockers.json",
        log
    )

    relaciones = construir_relaciones_por_responsable(
        alarmas,
        dockers,
        log
    )

    if not relaciones:
        log.warning(
            "No se encontró ningún responsable en alarmas.json"
        )
        sys.exit(0)

    ayer = (
        datetime.now(timezone.utc).date()
        - timedelta(days=1)
    )

    inicio_dt = datetime(
        ayer.year,
        ayer.month,
        ayer.day,
        0,
        0,
        0,
        tzinfo=timezone.utc
    )

    fin_dt = datetime(
        ayer.year,
        ayer.month,
        ayer.day,
        23,
        59,
        59,
        tzinfo=timezone.utc
    )

    for email, datos in relaciones.items():

        alarm_ids = datos["alarm_ids"]
        hosts_vm = datos["hosts_vm"]

        if not alarm_ids:

            log.warning(
                f"{email}: sin alarmas asociadas"
            )

            continue

        log.info(
            f"Generando informe para {email} "
            f"({len(alarm_ids)} alarma(s), {len(hosts_vm)} VM(s))"
        )

        try:

            ejecutar_informe(
                args=args,

                inicio_dt=inicio_dt,
                fin_dt=fin_dt,

                titulo="Informe diario de monitorización",

                periodo_str=ayer.strftime(
                    "%d/%m/%Y"
                ),

                nombre_fichero=(
                    f"informe_diario_"
                    f"{ayer:%Y%m%d}_"
                    f"{nombre_fichero_seguro(email)}.pdf"
                ),

                modo_histograma="diario",

                umbral_uptime=args.umbral_uptime,

                log=log,

                alarm_ids=alarm_ids,

                hosts_vm=hosts_vm,

                email_to_override=email
            )

        except Exception as exc:

            log.error(
                f"Error generando informe para "
                f"{email}: {exc}",
                exc_info=True
            )