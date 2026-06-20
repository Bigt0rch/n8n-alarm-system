"""
reportGenerator.py
Módulo compartido con toda la lógica de consulta a InfluxDB
y generación del PDF. Importado por reportMonthly.py, reportAnnual.py y reportDaily.py.
"""
import io
import logging
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from influxdb_client import InfluxDBClient
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, HRFlowable, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents


# ── Paleta de colores ────────────────────────────────────────────────────────
COLOR_PRIMARIO   = colors.HexColor("#1a3a5c")
COLOR_SECUNDARIO = colors.HexColor("#2e86c1")
COLOR_OK         = colors.HexColor("#1e8449")
COLOR_CRITICAL   = colors.HexColor("#c0392b")
COLOR_FONDO      = colors.HexColor("#f2f3f4")

def flux_escape(valor) -> str:
    """Escapa un valor para insertarlo como string literal en Flux."""
    return str(valor).replace("\\", "\\\\").replace('"', '\\"')


def nombre_visible_alarma(valor) -> str:
    """Convierte alarma:web_corporativa en web_corporativa."""
    if valor is None:
        return ""
    texto = str(valor)
    if texto.startswith("alarma:"):
        texto = texto[len("alarma:"):]
    return texto


def _detalle_metricas_por_periodo(inicio_dt: datetime, fin_dt: datetime) -> str:
    segundos = (fin_dt - inicio_dt).total_seconds()
    return "raw" if segundos <= 2 * 86400 else "daily"


# ── Estilos ──────────────────────────────────────────────────────────────────
def crear_estilos():
    estilos = getSampleStyleSheet()
    estilos.add(ParagraphStyle(
        name="Titulo",
        fontSize=26, textColor=COLOR_PRIMARIO,
        spaceAfter=6, spaceBefore=0, leading=32,
        fontName="Helvetica-Bold"
    ))
    estilos.add(ParagraphStyle(
        name="Subtitulo",
        fontSize=13, textColor=COLOR_SECUNDARIO,
        spaceAfter=4, spaceBefore=0,
        fontName="Helvetica"
    ))
    estilos.add(ParagraphStyle(
        name="Seccion",
        fontSize=14, textColor=COLOR_PRIMARIO,
        spaceAfter=6, spaceBefore=14,
        fontName="Helvetica-Bold"
    ))
    estilos.add(ParagraphStyle(
        name="Subseccion",
        fontSize=11, textColor=COLOR_SECUNDARIO,
        spaceAfter=4, spaceBefore=8,
        fontName="Helvetica-Bold"
    ))
    estilos.add(ParagraphStyle(
        name="Nota",
        fontSize=8, textColor=colors.grey,
        spaceAfter=2, spaceBefore=0,
        fontName="Helvetica-Oblique"
    ))
    estilos.add(ParagraphStyle(
        name="FalloItem",
        fontSize=8, textColor=colors.HexColor("#4a4a4a"),
        spaceAfter=2, spaceBefore=1, leftIndent=12,
        fontName="Helvetica"
    ))
    estilos.add(ParagraphStyle(
        name="TOC1",
        fontSize=11, textColor=COLOR_PRIMARIO,
        spaceAfter=4, spaceBefore=2, leftIndent=0,
        fontName="Helvetica-Bold"
    ))
    estilos.add(ParagraphStyle(
        name="TOC2",
        fontSize=9, textColor=COLOR_SECUNDARIO,
        spaceAfter=2, spaceBefore=0, leftIndent=16,
        fontName="Helvetica"
    ))
    return estilos


# ── Consultas InfluxDB ───────────────────────────────────────────────────────

def consultar_uptime_por_categoria(query_api, org: str, bucket: str,
                                    inicio: str, fin: str,
                                    categoria: str,
                                    log: logging.Logger,
                                    alarm_ids: list[str] | None = None) -> list[dict]:
    """
    Reconstruye el uptime de cada alarma de una categoría concreta.
    El estado inicial del período se obtiene con el último estado anterior a inicio;
    si no existe se asume OK. n_incidencias cuenta solo activaciones OK → CRITICAL.
    """
    inicio_dt     = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    fin_dt        = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    periodo_total = (fin_dt - inicio_dt).total_seconds()

    alarm_filter = ""
    if alarm_ids:
        ids = [flux_escape(aid) for aid in alarm_ids]
        condiciones = " or ".join([f'r["alarm_id"] == "{aid}"' for aid in ids])
        alarm_filter = f"\n          |> filter(fn: (r) => {condiciones})" if condiciones else ""

    query = f"""
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["categoria"] == "{flux_escape(categoria)}")
          |> filter(fn: (r) => r["_field"] == "estado")
          {alarm_filter}
          |> sort(columns: ["_time"])
    """
    tablas = query_api.query(query, org=org)

    por_alarma: dict[str, list] = {}
    por_alarma_nombre: dict[str, str] = {}
    for tabla in tablas:
        for rec in tabla.records:
            aid = rec.values.get("alarm_id", "")
            if not aid:
                continue
            ts     = rec.get_time()
            estado = rec.get_value()
            nombre = rec.values.get("nombre_alarma", aid)
            por_alarma.setdefault(aid, []).append((ts, estado))
            por_alarma_nombre.setdefault(aid, nombre)

    if alarm_ids:
        for aid in alarm_ids:
            if aid in por_alarma:
                continue
            q_last = f"""
                from(bucket: "{bucket}")
                  |> range(start: 0, stop: {fin})
                  |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
                  |> filter(fn: (r) => r["categoria"] == "{flux_escape(categoria)}")
                  |> filter(fn: (r) => r["alarm_id"] == "{flux_escape(aid)}")
                  |> filter(fn: (r) => r["_field"] == "estado")
                  |> last()
            """
            tablas_last = query_api.query(q_last, org=org)
            encontrado = False
            for tabla in tablas_last:
                if tabla.records:
                    rec = tabla.records[-1]
                    por_alarma.setdefault(aid, [])
                    por_alarma_nombre.setdefault(aid, rec.values.get("nombre_alarma", aid))
                    encontrado = True
                    break
            if encontrado:
                continue

    resultados = []
    for aid, eventos in por_alarma.items():
        eventos.sort(key=lambda x: x[0])

        q_prev = f"""
            from(bucket: "{bucket}")
              |> range(start: 0, stop: {inicio})
              |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
              |> filter(fn: (r) => r["categoria"] == "{flux_escape(categoria)}")
              |> filter(fn: (r) => r["alarm_id"] == "{flux_escape(aid)}")
              |> filter(fn: (r) => r["_field"] == "estado")
              |> last()
        """
        estado_actual = "OK"
        tablas_prev = query_api.query(q_prev, org=org)
        for tabla in tablas_prev:
            if tabla.records:
                estado_actual = tabla.records[-1].get_value() or "OK"
                por_alarma_nombre.setdefault(aid, tabla.records[-1].values.get("nombre_alarma", aid))
                break

        tiempo_ok     = 0.0
        n_incidencias = 0
        ts_anterior   = inicio_dt

        for ts, estado in eventos:
            if ts < inicio_dt or ts > fin_dt:
                continue
            delta = (ts - ts_anterior).total_seconds()
            if estado_actual == "OK":
                tiempo_ok += max(0.0, delta)
            if estado_actual == "OK" and estado == "CRITICAL":
                n_incidencias += 1
            estado_actual = estado
            ts_anterior   = ts

        delta_final = (fin_dt - ts_anterior).total_seconds()
        if estado_actual == "OK":
            tiempo_ok += max(0.0, delta_final)

        uptime_pct     = round((tiempo_ok / periodo_total) * 100, 3) if periodo_total > 0 else 100.0
        tiempo_caido_m = round((periodo_total - tiempo_ok) / 60, 1)
        nombre_base    = por_alarma_nombre.get(aid, aid)

        resultados.append({
            "alarm_id":         aid,
            "nombre":           nombre_visible_alarma(nombre_base or aid),
            "uptime_pct":       uptime_pct,
            "tiempo_caido_min": tiempo_caido_m,
            "n_incidencias":    n_incidencias,
        })

    resultados.sort(key=lambda x: x["uptime_pct"])
    log.debug(f"Uptime [{categoria}] calculado para {len(resultados)} alarma(s)")
    return resultados


def consultar_metricas_servidor(query_api, org: str, bucket: str,
                                 inicio: str, fin: str,
                                 log: logging.Logger,
                                 hosts: list[str] | None = None,
                                 detalle: str = "daily") -> dict:
    """
    Devuelve métricas agrupadas por host. detalle="raw" usa puntos reales
    del período; detalle="daily" usa medias diarias.
    """
    resultado = {}
    usar_raw = detalle == "raw"

    for metrica, measurement, field in [
        ("cpu", "cpu", "usage_user"),
        ("ram", "mem", "used_percent"),
    ]:
        filtro_cpu_total = '|> filter(fn: (r) => r["cpu"] == "cpu-total")' if measurement == "cpu" else ""
        agregacion = "" if usar_raw else '|> aggregateWindow(every: 1d, fn: mean, createEmpty: false)'
        query = f"""
            from(bucket: "{bucket}")
              |> range(start: {inicio}, stop: {fin})
              |> filter(fn: (r) => r["_measurement"] == "{measurement}")
              |> filter(fn: (r) => r["_field"] == "{field}")
              {filtro_cpu_total}
              {agregacion}
              |> sort(columns: ["_time"])
        """
        tablas = query_api.query(query, org=org)
        for tabla in tablas:
            host = tabla.records[0].values.get("host", "servidor") if tabla.records else "servidor"
            if hosts is not None and host not in hosts:
                continue
            if host not in resultado:
                resultado[host] = {"cpu": [], "ram": [], "disco": {}}
            for rec in tabla.records:
                valor = rec.get_value()
                if valor is not None:
                    resultado[host][metrica].append((rec.get_time(), round(float(valor), 2)))

    agregacion_disco = "" if usar_raw else '|> aggregateWindow(every: 1d, fn: mean, createEmpty: false)'
    query_disco = f"""
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "disk")
          |> filter(fn: (r) => r["_field"] == "used_percent")
          {agregacion_disco}
          |> sort(columns: ["_time"])
    """
    tablas_disco = query_api.query(query_disco, org=org)
    for tabla in tablas_disco:
        if not tabla.records:
            continue
        host = tabla.records[0].values.get("host", "servidor")
        if hosts is not None and host not in hosts:
            continue
        path = tabla.records[0].values.get("path", "/")
        if host not in resultado:
            resultado[host] = {"cpu": [], "ram": [], "disco": {}}
        resultado[host].setdefault("disco", {}).setdefault(path, [])
        for rec in tabla.records:
            valor = rec.get_value()
            if valor is not None:
                resultado[host]["disco"][path].append((rec.get_time(), round(float(valor), 2)))

    for h in resultado:
        resultado[h].setdefault("disco", {})

    log.debug(f"Métricas de servidor obtenidas para {len(resultado)} host(s) detalle={detalle}")
    return resultado


def consultar_metricas_historico_completo(query_api, org: str, bucket: str,
                                          log: logging.Logger,
                                          hosts: list[str] | None = None,
                                          fin: str | None = None,
                                          dias_historico: int = 365) -> dict:
    """
    Trae histórico diario para ARIMA.

    La predicción se entrena siempre con una ventana máxima de los últimos
    `dias_historico` días anteriores al final del período del informe.
    Esto evita que un informe diario entrene solo con el día anterior y evita
    que un informe mensual/anual use datos posteriores al período informado.
    """
    resultado = {}

    if fin:
        fin_dt = datetime.fromisoformat(fin.replace("Z", "+00:00"))
        inicio_dt = fin_dt - timedelta(days=dias_historico)
        inicio_hist = inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        rango = f"range(start: {inicio_hist}, stop: {fin})"
    else:
        # Fallback para usos externos: ventana relativa a now().
        rango = f"range(start: -{dias_historico}d)"

    for metrica, measurement, field in [
        ("cpu", "cpu", "usage_user"),
        ("ram", "mem", "used_percent"),
    ]:
        filtro_cpu_total = '|> filter(fn: (r) => r["cpu"] == "cpu-total")' if measurement == "cpu" else ""
        query = f"""
            from(bucket: "{bucket}")
              |> {rango}
              |> filter(fn: (r) => r["_measurement"] == "{measurement}")
              |> filter(fn: (r) => r["_field"] == "{field}")
              {filtro_cpu_total}
              |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
              |> sort(columns: ["_time"])
        """
        tablas = query_api.query(query, org=org)
        for tabla in tablas:
            host = tabla.records[0].values.get("host", "servidor") if tabla.records else "servidor"
            if hosts is not None and host not in hosts:
                continue
            if host not in resultado:
                resultado[host] = {"cpu": [], "ram": [], "disco": {}}
            for rec in tabla.records:
                valor = rec.get_value()
                if valor is not None:
                    resultado[host][metrica].append((rec.get_time(), round(float(valor), 2)))

    query_disco = f"""
        from(bucket: "{bucket}")
          |> {rango}
          |> filter(fn: (r) => r["_measurement"] == "disk")
          |> filter(fn: (r) => r["_field"] == "used_percent")
          |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
          |> sort(columns: ["_time"])
    """
    tablas_disco = query_api.query(query_disco, org=org)
    for tabla in tablas_disco:
        if not tabla.records:
            continue
        host = tabla.records[0].values.get("host", "servidor")
        if hosts is not None and host not in hosts:
            continue
        path = tabla.records[0].values.get("path", "/")
        if host not in resultado:
            resultado[host] = {"cpu": [], "ram": [], "disco": {}}
        resultado[host].setdefault("disco", {}).setdefault(path, [])
        for rec in tabla.records:
            valor = rec.get_value()
            if valor is not None:
                resultado[host]["disco"][path].append((rec.get_time(), round(float(valor), 2)))

    for h in resultado:
        resultado[h].setdefault("disco", {})

    n_puntos = sum(len(v["cpu"]) for v in resultado.values())
    log.debug(f"Histórico ARIMA últimos {dias_historico} días: {len(resultado)} host(s), {n_puntos} puntos de CPU")
    return resultado


def _histograma_horario(inicio_dt: datetime, fin_dt: datetime, modo: str) -> bool:
    """
    Devuelve True cuando el histograma debe agruparse por horas.

    reportDaily.py y reportMonthly.py usan modo_histograma="diario". Para no
    romper el mensual, la granularidad horaria se activa solo cuando el período
    informado es corto (hasta 2 días), que corresponde al informe diario.
    """
    return modo == "diario" and (fin_dt - inicio_dt).total_seconds() <= 2 * 86400


def consultar_histograma_alarmas(query_api, org: str, bucket: str,
                                  inicio: str, fin: str,
                                  modo: str,
                                  log: logging.Logger,
                                  alarm_ids: list[str] | None = None) -> dict:
    """
    Cuenta activaciones reales de alarma: transiciones OK → CRITICAL.

    Para decidir si el primer evento del período cuenta como activación,
    consulta el último estado anterior al inicio. Si no existe, asume OK.

    Granularidad:
      - informe diario: por hora;
      - informe mensual: por día;
      - informe anual: por mes.
    """
    inicio_dt = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    fin_dt    = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    horario   = _histograma_horario(inicio_dt, fin_dt, modo)

    alarm_filter = ""
    if alarm_ids:
        ids = [flux_escape(aid) for aid in alarm_ids]
        condiciones = " or ".join([f'r["alarm_id"] == "{aid}"' for aid in ids])
        alarm_filter = f"\n          |> filter(fn: (r) => {condiciones})" if condiciones else ""

    query = f"""
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["_field"] == "estado")
          {alarm_filter}
          |> group(columns: ["alarm_id"])
          |> sort(columns: ["_time"])
    """
    tablas = query_api.query(query, org=org)

    eventos_por_alarma: dict[str, list] = {}
    nombres_por_alarma: dict[str, str] = {}

    for tabla in tablas:
        for rec in tabla.records:
            aid = rec.values.get("alarm_id", "")
            if not aid:
                continue
            eventos_por_alarma.setdefault(aid, []).append((rec.get_time(), rec.get_value()))
            nombres_por_alarma.setdefault(aid, nombre_visible_alarma(rec.values.get("nombre_alarma", aid)))

    resultado: dict = {}

    for aid, eventos in eventos_por_alarma.items():
        eventos.sort(key=lambda x: x[0])

        q_prev = f"""
            from(bucket: "{bucket}")
              |> range(start: 0, stop: {inicio})
              |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
              |> filter(fn: (r) => r["alarm_id"] == "{flux_escape(aid)}")
              |> filter(fn: (r) => r["_field"] == "estado")
              |> last()
        """
        estado_actual = "OK"
        tablas_prev = query_api.query(q_prev, org=org)
        for tabla in tablas_prev:
            if tabla.records:
                rec_prev = tabla.records[-1]
                estado_actual = rec_prev.get_value() or "OK"
                nombres_por_alarma.setdefault(aid, nombre_visible_alarma(rec_prev.values.get("nombre_alarma", aid)))
                break

        for ts, estado in eventos:
            if ts < inicio_dt or ts > fin_dt:
                continue

            if estado_actual == "OK" and estado == "CRITICAL":
                if horario:
                    clave = ts.strftime("%Y-%m-%d %H:00")
                elif modo == "diario":
                    clave = ts.strftime("%Y-%m-%d")
                else:
                    clave = ts.strftime("%Y-%m")

                if aid not in resultado:
                    resultado[aid] = {"nombre": nombres_por_alarma.get(aid, nombre_visible_alarma(aid)), "conteos": {}}
                resultado[aid]["conteos"][clave] = resultado[aid]["conteos"].get(clave, 0) + 1

            estado_actual = estado

    granularidad = "horaria" if horario else "diaria" if modo == "diario" else "mensual"
    log.debug(f"Histograma de activaciones OK→CRITICAL calculado para {len(resultado)} alarma(s) (granularidad={granularidad})")
    return resultado

def consultar_fallos_con_contexto(query_api, org: str, bucket: str,
                                   inicio: str, fin: str,
                                   log: logging.Logger,
                                   alarm_ids: list[str] | None = None) -> dict:
    """
    Recupera los eventos CRITICAL que tienen un campo 'contexto' no vacío.
    Solo los servicios con docker/VM asociados generan contexto en
    alarmMonitorDBn8n.py; las demás alarmas no aparecerán aquí.

    Las alarmas sin contexto NO se incluyen: así evitamos mostrar fechas
    sueltas sin información diagnóstica útil.

    Devuelve: { alarm_id: [ {"ts": datetime, "contexto": str}, ... ] }

    alarm_ids: filtro opcional por responsable.
    """
    query = f'''
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["_field"] == "contexto")
          |> sort(columns: ["_time"])
    '''
    tablas = query_api.query(query, org=org)

    resultado: dict = {}
    for tabla in tablas:
        for rec in tabla.records:
            aid      = rec.values.get("alarm_id", "")
            contexto = rec.get_value() or ""
            # Descartamos contextos vacíos: no tiene sentido mostrar la fecha sola
            if not contexto.strip():
                continue
            if alarm_ids is not None and aid not in alarm_ids:
                continue
            contexto = contexto.replace('@@NL@@', '')
            resultado.setdefault(aid, []).append({
                "ts":       rec.get_time(),
                "contexto": contexto,
            })

    for aid in resultado:
        resultado[aid].sort(key=lambda x: x["ts"])

    log.debug(f"Fallos con contexto encontrados para {len(resultado)} alarma(s)")
    return resultado


def consultar_alarm_ids_por_responsable(query_api, org: str, bucket: str,
                                         inicio: str, fin: str,
                                         log: logging.Logger) -> dict[str, list[str]]:
    """
    Lee el field 'responsable' de alarm_notifications y agrupa los alarm_id
    por email. Usado por reportDaily.py para determinar a quién enviar cada
    informe y qué alarmas incluir.

    Nota: 'responsable' está guardado como field (no tag) en
    alarmMonitorDBn8n.py. La query toma el último valor por alarm_id.

    Devuelve: { "email@ejemplo.com": ["alarma:a", "alarma:b"], ... }
    """
    query = f'''
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["_field"] == "responsable")
          |> group(columns: ["alarm_id"])
          |> last()
    '''
    tablas = query_api.query(query, org=org)

    resultado: dict[str, list[str]] = {}
    for tabla in tablas:
        for rec in tabla.records:
            aid         = rec.values.get("alarm_id", "")
            responsable = rec.get_value() or ""
            if not responsable or not aid:
                continue
            resultado.setdefault(responsable, [])
            if aid not in resultado[responsable]:
                resultado[responsable].append(aid)

    log.debug(
        f"Responsables encontrados: {len(resultado)} "
        f"({sum(len(v) for v in resultado.values())} alarmas en total)"
    )
    return resultado


# ── Funciones de análisis para anotaciones ──────────────────────────────────

DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _dia_semana_mayor_media(puntos: list[tuple]) -> str:
    from collections import defaultdict
    acum = defaultdict(list)
    for ts, v in puntos:
        acum[ts.weekday()].append(v)
    if not acum:
        return "–"
    medias = {d: sum(vs) / len(vs) for d, vs in acum.items()}
    return DIAS_ES[max(medias, key=medias.get)]


def _dia_mes_mayor_consumo(puntos: list[tuple]) -> str:
    from collections import defaultdict
    acum = defaultdict(list)
    for ts, v in puntos:
        acum[ts.day].append(v)
    if not acum:
        return "–"
    medias = {d: sum(vs) / len(vs) for d, vs in acum.items()}
    return str(max(medias, key=medias.get))


def _dia_semana_mayor_disparos(conteos: dict) -> str:
    from collections import defaultdict
    acum = defaultdict(int)
    for clave, n in conteos.items():
        try:
            dt = datetime.strptime(clave, "%Y-%m-%d")
            acum[dt.weekday()] += n
        except ValueError:
            pass
    if not acum:
        return "–"
    return DIAS_ES[max(acum, key=acum.get)]


def _dia_mes_mayor_disparos(conteos: dict) -> str:
    if not conteos:
        return "–"
    diarias = {k: v for k, v in conteos.items() if len(k) == 10}
    if not diarias:
        return "–"
    clave_max = max(diarias, key=diarias.get)
    return str(int(clave_max[8:10]))


def _hora_mayor_disparos(conteos: dict) -> str:
    """Devuelve la hora con más transiciones para histogramas horarios."""
    if not conteos:
        return "–"
    horarios = {k: v for k, v in conteos.items() if len(k) == 16 and k[10] == " "}
    if not horarios:
        return "–"
    clave_max = max(horarios, key=horarios.get)
    return clave_max[11:16]


# ── Gráficas ─────────────────────────────────────────────────────────────────

def _fig_to_image(fig, ancho_cm=16) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth  = ancho_cm * cm
    img.drawHeight = ancho_cm * cm * ratio
    return img


def _regresion_lineal(puntos: list[tuple]) -> tuple:
    import numpy as np
    if len(puntos) < 2:
        return 0.0, None, "estable"
    ts0   = puntos[0][0].timestamp()
    xs    = [(ts.timestamp() - ts0) / 86400 for ts, _ in puntos]
    ys    = [v for _, v in puntos]
    n     = len(xs)
    sx    = sum(xs);  sy  = sum(ys)
    sxx   = sum(x*x for x in xs);  sxy = sum(x*y for x, y in zip(xs, ys))
    denom = n * sxx - sx ** 2
    if denom == 0:
        return 0.0, None, "estable"
    pendiente  = (n * sxy - sx * sy) / denom
    intercepto = (sy - pendiente * sx) / n
    media_est  = round(intercepto + pendiente * (xs[-1] + 30), 1)
    media_est  = max(0.0, min(100.0, media_est))
    tendencia  = "creciente" if pendiente > 0.1 else "decreciente" if pendiente < -0.1 else "estable"
    return pendiente, media_est, tendencia


def _subplot_metrica(ax, puntos: list[tuple], color: str, label: str):
    if not puntos:
        ax.set_ylabel(f"{label} (%)", fontsize=8)
        return
    fechas, vals = zip(*puntos)
    ax.plot(fechas, vals, color=color, linewidth=1.5, marker="o", markersize=2)
    ax.fill_between(fechas, vals, alpha=0.12, color=color)
    ax.set_ylabel(f"{label} (%)", fontsize=8)
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax.grid(axis="y", linestyle="--", alpha=0.5)


def _subplot_arima(ax, puntos_full: list, puntos_vista: list, color: str, label: str):
    import numpy as np
    from pmdarima import auto_arima

    MIN_PUNTOS = 10
    if len(puntos_full) < MIN_PUNTOS:
        puntos_full = puntos_vista

    if not puntos_full or len(puntos_full) < 3:
        ax.set_ylabel(f"{label} (%)", fontsize=8)
        ax.text(0.5, 0.5, "Sin datos suficientes para predicción",
                ha="center", va="center", transform=ax.transAxes, color="grey", fontsize=9)
        return

    horizonte  = 30
    vals_full  = np.array([v for _, v in puntos_full], dtype=float)
    fechas_vis = [ts for ts, _ in puntos_vista] if puntos_vista else [ts for ts, _ in puntos_full[-30:]]
    vals_vis   = np.array([v for _, v in puntos_vista], dtype=float) if puntos_vista else vals_full[-30:]
    ultimo_ts  = puntos_full[-1][0]
    fechas_fut = [ultimo_ts + timedelta(days=d) for d in range(1, horizonte + 1)]

    ts0        = puntos_full[0][0].timestamp()
    xs_full    = np.array([(ts.timestamp() - ts0) / 86400 for ts, _ in puntos_full])
    pend, inte = np.polyfit(xs_full, vals_full, 1)
    xs_vis     = np.array([(ts.timestamp() - ts0) / 86400 for ts in fechas_vis])
    reg_vis    = np.clip(inte + pend * xs_vis, 0, 100)
    xs_fut_rel = np.array([xs_full[-1] + d for d in range(1, horizonte + 1)])
    reg_fut    = np.clip(inte + pend * xs_fut_rel, 0, 100)

    try:
        modelo = auto_arima(vals_full, start_p=0, max_p=5, start_q=0, max_q=5, d=None,
                            seasonal=False, information_criterion="aic",
                            stepwise=True, suppress_warnings=True, error_action="ignore")
        forecast, conf_int = modelo.predict(n_periods=horizonte, return_conf_int=True, alpha=0.05)
        forecast   = np.clip(forecast, 0, 100)
        ci_low     = np.clip(conf_int[:, 0], 0, 100)
        ci_high    = np.clip(conf_int[:, 1], 0, 100)
        orden_str  = str(modelo.order)
        arima_ok   = True
    except Exception:
        arima_ok   = False
        orden_str  = "?"

    ax.plot(fechas_vis, vals_vis, color=color, linewidth=1,
            alpha=0.4, marker="o", markersize=2, label="Período (real)")
    if len(xs_vis) > 0:
        ax.plot(fechas_vis, reg_vis, color=color, linewidth=1.2,
                linestyle="--", alpha=0.5, label="Tendencia lineal (histórico completo)")
    ax.plot(fechas_fut, reg_fut, color=color, linewidth=1.5, linestyle="--", alpha=0.75)
    if arima_ok:
        ax.plot(fechas_fut, forecast, color=color, linewidth=2,
                linestyle="-", label=f"ARIMA {orden_str}")
        ax.fill_between(fechas_fut, ci_low, ci_high, color=color, alpha=0.13, label="IC 95%")
    ax.axvline(x=ultimo_ts, color="grey", linewidth=0.8, linestyle=":", alpha=0.7)
    ax.set_ylabel(f"{label} (%)", fontsize=8)
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(fontsize=7, loc="upper left")


def _grafica_arima(puntos_periodo: dict, puntos_historico: dict, host: str) -> Image:
    COLORES_DISCO = ["#8e44ad", "#d35400", "#16a085", "#c0392b", "#7f8c8d", "#2c3e50", "#f39c12"]
    disco_p       = puntos_periodo.get("disco", {})
    disco_h       = puntos_historico.get("disco", {})
    particiones   = sorted(set(disco_p.keys()) | set(disco_h.keys()), key=lambda p: (p != "/", p))
    n_plots       = 2 + len(particiones)
    fig, axes     = plt.subplots(n_plots, 1, figsize=(10, 3 + 2.8 * n_plots), sharex=False)
    if n_plots == 1:
        axes = [axes]
    fig.suptitle(f"Predicción próximo mes (ARIMA) — {host}", fontsize=12,
                 color="#1a3a5c", fontweight="bold")
    _subplot_arima(axes[0], puntos_historico.get("cpu", []), puntos_periodo.get("cpu", []), "#2e86c1", "CPU")
    _subplot_arima(axes[1], puntos_historico.get("ram", []), puntos_periodo.get("ram", []), "#1e8449", "RAM")
    for i, path in enumerate(particiones):
        _subplot_arima(axes[2 + i], disco_h.get(path, []), disco_p.get(path, []),
                       COLORES_DISCO[i % len(COLORES_DISCO)], f"Disco {path}")
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    return _fig_to_image(fig)


def grafica_metricas_host(host: str, datos: dict, modo: str = "mensual",
                          historico: dict | None = None) -> tuple:
    """
    Gráfica de CPU, RAM y Disco.
    modo "mensual" o "diario" → incluye predicción ARIMA.
    modo "anual"              → solo serie histórica, sin predicción.
    Devuelve (Image_historico, Image_prediccion_o_None, lista_anotaciones).
    """
    COLORES_DISCO = ["#8e44ad", "#d35400", "#16a085", "#c0392b", "#7f8c8d", "#2c3e50", "#f39c12"]
    disco       = datos.get("disco", {})
    particiones = sorted(disco.keys(), key=lambda p: (p != "/", p))
    n_subplots  = 2 + len(particiones)
    fig, axes   = plt.subplots(n_subplots, 1, figsize=(10, 3 + 2.5 * n_subplots), sharex=True)
    if n_subplots == 1:
        axes = [axes]
    titulo_graf = "Métricas intradía" if modo == "diario" else "Métricas diarias"
    fig.suptitle(f"{titulo_graf} — {host}", fontsize=12, color="#1a3a5c", fontweight="bold")
    _subplot_metrica(axes[0], datos["cpu"], "#2e86c1", "CPU")
    _subplot_metrica(axes[1], datos["ram"], "#1e8449", "RAM")
    for i, path in enumerate(particiones):
        _subplot_metrica(axes[2 + i], disco[path], COLORES_DISCO[i % len(COLORES_DISCO)], f"Disco {path}")
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    img = _fig_to_image(fig)

    anotaciones = []
    for metrica, label, color in [("cpu", "CPU", "#2e86c1"), ("ram", "RAM", "#1e8449")]:
        puntos = datos[metrica]
        if not puntos:
            continue
        vals      = [v for _, v in puntos]
        media     = round(sum(vals) / len(vals), 1)
        maximo    = round(max(vals), 1)
        ts_max    = puntos[vals.index(max(vals))][0]
        dia_max   = ts_max.strftime("%d/%m")
        _, __, tendencia = _regresion_lineal(puntos)

        anotaciones.append(
            f"<b>{label}</b> — Consumo medio: <b>{media}%</b>, "
            f"máximo: <b>{maximo}%</b> el día <b>{dia_max}</b>."
        )
        anotaciones.append(
            f"Mayor consumo de {label}: <b>{_dia_semana_mayor_media(puntos)}</b> (día de la semana), "
            f"día <b>{_dia_mes_mayor_consumo(puntos)}</b> del mes."
        )
        if modo in ("mensual", "diario"):
            n_hist = len((historico or {}).get(host, {}).get(metrica, []))
            fuente = f"últimos 365 días disponibles ({n_hist} días)" if n_hist > len(puntos) else "período del informe"
            anotaciones.append(
                f"Tendencia de {label}: <b>{tendencia}</b>. "
                f"Predicción ARIMA entrenada con {fuente}."
            )

    for path in particiones:
        puntos_d = disco[path]
        if not puntos_d:
            continue
        vals_d     = [v for _, v in puntos_d]
        media_d    = round(sum(vals_d) / len(vals_d), 1)
        max_d      = round(max(vals_d), 1)
        min_d      = round(min(vals_d), 1)
        pend_d, _, tend_d = _regresion_lineal(puntos_d)
        texto_disco = (
            f"<b>Disco {path}</b> — Uso medio: <b>{media_d}%</b>, "
            f"máximo: <b>{max_d}%</b>, mínimo: <b>{min_d}%</b>. "
            f"Tendencia: <b>{tend_d}</b>."
        )
        if tend_d == "creciente" and pend_d > 0:
            ts0_d        = puntos_d[0][0].timestamp()
            xs_d         = [(ts.timestamp() - ts0_d) / 86400 for ts, _ in puntos_d]
            intercepto_d = (sum(vals_d) - pend_d * sum(xs_d)) / len(xs_d)
            dias_100     = (100.0 - (intercepto_d + pend_d * xs_d[-1])) / pend_d
            if dias_100 > 0:
                alerta = "&#9888; " if dias_100 < 30 else "&#9654; " if dias_100 < 90 else ""
                texto_disco += (
                    f" {alerta}A este ritmo el disco alcanzará el 100%% "
                    f"en aproximadamente <b>{int(round(dias_100))} días</b>."
                )
        anotaciones.append(texto_disco)

    img_pred = None
    if modo in ("mensual", "diario"):
        datos_hist_host = (historico or {}).get(host, datos)
        img_pred = _grafica_arima(datos, datos_hist_host, host)

    return img, img_pred, anotaciones


def grafica_histograma_alarmas(datos: dict, modo: str,
                                inicio_dt: datetime, fin_dt: datetime,
                                fallos_contexto: dict | None = None) -> list[tuple]:
    """
    Genera una gráfica de barras por alarma y sus anotaciones textuales.

    Granularidad:
      - informe diario: horas del día;
      - informe mensual: días del mes;
      - informe anual: meses del año.

    fallos_contexto: resultado de consultar_fallos_con_contexto(). Cuando una
    alarma tiene entradas aquí, se añade el bloque "Análisis de fallos" con
    fecha + diagnóstico de cada evento. Las alarmas SIN contexto no reciben
    ningún listado de fechas.
    """
    fallos_contexto = fallos_contexto or {}

    if not datos:
        fig, ax = plt.subplots(figsize=(10, 2))
        ax.text(0.5, 0.5, "Sin alarmas en el período", ha="center", va="center")
        ax.axis("off")
        return [(_fig_to_image(fig), [])]

    alarmas = list(datos.items())
    paginas = []
    horario = _histograma_horario(inicio_dt, fin_dt, modo)

    if horario:
        cur = inicio_dt.replace(minute=0, second=0, microsecond=0)
        if cur < inicio_dt:
            cur += timedelta(hours=1)
        claves_completas = []
        while cur <= fin_dt:
            claves_completas.append(cur.strftime("%Y-%m-%d %H:00"))
            cur += timedelta(hours=1)
        # Si el rango no cae exactamente en horas enteras, garantizamos al menos una etiqueta.
        if not claves_completas:
            claves_completas = [inicio_dt.strftime("%Y-%m-%d %H:00")]
        mismo_dia = inicio_dt.date() == fin_dt.date()
        etiquetas_base = [c[11:16] if mismo_dia else f"{c[8:10]} {c[11:16]}" for c in claves_completas]
        xlabel = "Hora del día" if mismo_dia else "Día y hora"
    elif modo == "diario":
        cur, fin_d = inicio_dt.date(), fin_dt.date()
        claves_completas = []
        while cur <= fin_d:
            claves_completas.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        etiquetas_base = [c[8:] for c in claves_completas]
        xlabel = "Día del mes"
    else:
        meses_abrev = ["Ene","Feb","Mar","Abr","May","Jun",
                       "Jul","Ago","Sep","Oct","Nov","Dic"]
        claves_completas = []
        y, m = inicio_dt.year, inicio_dt.month
        while (y, m) <= (fin_dt.year, fin_dt.month):
            claves_completas.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        etiquetas_base = [meses_abrev[int(c[5:7]) - 1] for c in claves_completas]
        xlabel = "Mes"

    for aid, info in alarmas:
        conteos = info["conteos"]
        valores = [conteos.get(c, 0) for c in claves_completas]

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.set_title(info["nombre"], fontsize=12, color="#1a3a5c", fontweight="bold", loc="left")

        if not any(valores):
            ax.text(0.5, 0.5, "Sin activaciones en el período", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="grey")
            ax.axis("off")
        else:
            ancho_barra = 0.75 if horario else 0.6
            bars = ax.bar(etiquetas_base, valores, color="#2e86c1", edgecolor="white", width=ancho_barra)
            ax.bar_label(bars, padding=2, fontsize=7)
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel("Disparos", fontsize=8)
            ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
            ax.grid(axis="y", linestyle="--", alpha=0.4)
            ax.tick_params(axis="x", labelsize=7)
            ax.tick_params(axis="y", labelsize=7)
            if horario and len(etiquetas_base) > 18:
                # En un informe diario hay 24 barras; rotamos etiquetas para que no se pisen.
                plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        fig.tight_layout()
        img = _fig_to_image(fig, ancho_cm=16)
        plt.close(fig)

        anotaciones = []
        if any(valores):
            total = sum(valores)
            anotaciones.append(
                f"Esta alarma ha saltado un total de <b>{total}</b> veces durante el período."
            )
            if horario and conteos:
                anotaciones.append(
                    f"La hora con más disparos ha sido las <b>{_hora_mayor_disparos(conteos)}</b>."
                )
            elif modo == "diario" and conteos:
                anotaciones.append(
                    f"El día de la semana con más disparos ha sido el "
                    f"<b>{_dia_semana_mayor_disparos(conteos)}</b>. "
                    f"El día del mes con más disparos ha sido el "
                    f"<b>{_dia_mes_mayor_disparos(conteos)}</b>."
                )

        # ── Análisis de fallos ───────────────────────────────────────────────
        fallos = fallos_contexto.get(aid, [])
        if fallos:
            anotaciones.append("__FALLOS_INICIO__")
            for fallo in fallos:
                fecha_str = fallo["ts"].strftime("%d/%m/%Y %H:%M")
                anotaciones.append(f"__FALLO__{fecha_str}|{fallo['contexto']}")

        paginas.append((img, anotaciones))

    return paginas


# ── Construcción del PDF ─────────────────────────────────────────────────────

class _EntradaTOC(Flowable):
    def __init__(self, toc, texto, nivel, anchor):
        Flowable.__init__(self)
        self._toc = toc; self._texto = texto
        self._nivel = nivel; self._anchor = anchor
        self.width = 0; self.height = 0

    def draw(self):
        self.canv.bookmarkPage(self._anchor)
        self._toc.notify("TOCEntry", (self._nivel, self._texto,
                                      self.canv.getPageNumber(), self._anchor))


def _entrada_toc(toc, texto, estilo_titulo, nivel, anchor):
    return [_EntradaTOC(toc, texto, nivel, anchor), Paragraph(texto, estilo_titulo)]


def generar_pdf(titulo: str, periodo_str: str,
                uptime: list[dict],
                uptime_docker: list[dict],
                metricas: dict,
                histograma: dict,
                modo_histograma: str,
                inicio_dt: datetime,
                fin_dt: datetime,
                umbral_uptime: float,
                log: logging.Logger,
                metricas_historico: dict | None = None,
                fallos_contexto: dict | None = None,
                secciones: set[str] | None = None) -> bytes:
    """
    Genera el PDF completo y lo devuelve como bytes.

    secciones: conjunto con las secciones a incluir:
               "uptime"       → tabla de uptime de servicios y dockers
               "activaciones" → tabla de incidencias
               "recursos"     → gráficas de CPU/RAM/Disco por host
               "proyecciones" → gráfica ARIMA (requiere "recursos")
               "disparos"     → histogramas de disparos de alarmas
               "fallos"       → análisis de fallos bajo el histograma
               Si es None se incluyen todas (retrocompatibilidad).

    fallos_contexto: resultado de consultar_fallos_con_contexto(). Solo se
                     usa cuando "fallos" está en secciones, y solo para las
                     alarmas que realmente tienen contexto (las demás no
                     muestran ningún listado de fechas).
    """
    if secciones is None:
        secciones = {"uptime", "activaciones", "recursos", "proyecciones", "disparos", "fallos"}

    estilos = crear_estilos()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # ── Portada ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 2*cm))
    story.append(Paragraph(titulo, estilos["Titulo"]))
    story.append(Paragraph(f"Período: {periodo_str}", estilos["Subtitulo"]))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y a las %H:%M')}",
        estilos["Nota"]
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_SECUNDARIO, spaceAfter=12))
    story.append(PageBreak())

    # ── Índice ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Índice", estilos["Titulo"]))
    story.append(Spacer(1, 0.4*cm))
    toc = TableOfContents()
    toc.levelStyles = [estilos["TOC1"], estilos["TOC2"]]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())

    TABLA_STYLE = TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  COLOR_PRIMARIO),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, 0),  9),
        ("FONTSIZE",       (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_FONDO]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("ALIGN",          (1, 0), (-1, -1), "CENTER"),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
    ])

    num_sec = 0

    # ── Sección: Uptime ──────────────────────────────────────────────────────
    if "uptime" in secciones:
        num_sec += 1
        s = num_sec

        def _color_uptime(pct):
            return COLOR_OK if pct >= umbral_uptime else COLOR_CRITICAL
        
        def _tabla_uptime(datos_uptime, etiqueta):
            if not datos_uptime:
                story.append(Paragraph(f"Sin datos de uptime de {etiqueta} en el período.", estilos["Normal"]))
                return

            datos_ord = sorted(datos_uptime, key=lambda d: d["uptime_pct"])

            datos_rojos = [d for d in datos_ord if d["uptime_pct"] < umbral_uptime]
            datos_resto = [d for d in datos_ord if d["uptime_pct"] >= umbral_uptime]

            n_bajo = len(datos_rojos)

            story.append(Paragraph(
                f"<b>{n_bajo}</b> de los <b>{len(datos_ord)}</b> {etiqueta} han tenido un uptime "
                f"inferior al {umbral_uptime}% durante el período.", estilos["Nota"]
            ))

            peor = datos_ord[0]
            story.append(Paragraph(
                f"El peor uptime corresponde a <b>{peor['nombre']}</b> "
                f"con un <b>{peor['uptime_pct']}%</b>.", estilos["Nota"]
            ))

            story.append(Spacer(1, 0.3*cm))

            def _pintar_tabla_uptime(datos, titulo):
                if not datos:
                    return

                story.append(Paragraph(f"<b>{titulo}</b>", estilos["Nota"]))

                filas = [["Alarma / Servicio", "Uptime (%)", "Tiempo caído (min)"]]

                for d in datos:
                    c = _color_uptime(d["uptime_pct"])
                    filas.append([
                        Paragraph(d["nombre"], estilos["Normal"]),
                        Paragraph(f'<font color="{c.hexval()}">{d["uptime_pct"]}%</font>', estilos["Normal"]),
                        str(d["tiempo_caido_min"]),
                    ])

                t = Table(filas, colWidths=[9*cm, 4*cm, 5*cm], repeatRows=1)
                t.setStyle(TABLA_STYLE)
                story.append(t)
                story.append(Spacer(1, 0.4*cm))

            # Primero los uptimes críticos en rojo
            _pintar_tabla_uptime(datos_rojos, f"Uptimes críticos de {etiqueta}")

            # Después el resto
            _pintar_tabla_uptime(datos_resto, f"Resto de uptimes de {etiqueta}")

        story.extend(_entrada_toc(toc, f"{s}. Uptime", estilos["Seccion"], 0, f"sec{s}"))
        story.extend(_entrada_toc(toc, f"{s}.1  Servicios", estilos["Subseccion"], 1, f"sec{s}_1"))
        _tabla_uptime(uptime, "servicios")
        story.append(Spacer(1, 0.6*cm))
        story.extend(_entrada_toc(toc, f"{s}.2  Contenedores Docker", estilos["Subseccion"], 1, f"sec{s}_2"))
        _tabla_uptime(uptime_docker, "contenedores Docker")
        story.append(PageBreak())

    # ── Sección: Activaciones de alarmas ─────────────────────────────────────
    if "activaciones" in secciones:
        num_sec += 1
        s = num_sec

        todas  = uptime + uptime_docker
        vals_i = sorted([d["n_incidencias"] for d in todas]) if todas else []
        n_inc  = len(vals_i)
        media_inc = sum(vals_i) / n_inc if n_inc else 0
        q3_inc    = vals_i[int(n_inc * 0.75)] if n_inc else 0

        def _color_inc(n):
            if n <= media_inc:     return COLOR_OK.hexval()
            elif n <= q3_inc:      return colors.HexColor("#e67e22").hexval()
            else:                  return COLOR_CRITICAL.hexval()

        def _tabla_incidencias(datos_uptime, etiqueta):
            if not datos_uptime:
                story.append(Paragraph(f"Sin datos de incidencias de {etiqueta} en el período.", estilos["Normal"]))
                return

            inc_ord = sorted(datos_uptime, key=lambda d: d["n_incidencias"], reverse=True)

            color_critico = COLOR_CRITICAL.hexval().lower()

            inc_rojas = [
                d for d in inc_ord
                if _color_inc(d["n_incidencias"]).lower() == color_critico
            ]

            inc_resto = [
                d for d in inc_ord
                if _color_inc(d["n_incidencias"]).lower() != color_critico
            ]

            story.append(Paragraph(
                f"La alarma con más activaciones ha sido <b>{inc_ord[0]['nombre']}</b>, "
                f"con <b>{inc_ord[0]['n_incidencias']}</b> incidencias en el período.", estilos["Nota"]
            ))

            story.append(Spacer(1, 0.3*cm))

            def _pintar_tabla_incidencias(datos, titulo):
                if not datos:
                    return

                story.append(Paragraph(f"<b>{titulo}</b>", estilos["Nota"]))

                filas = [["Alarma / Servicio", "Incidencias"]]

                for d in datos:
                    c = _color_inc(d["n_incidencias"])
                    filas.append([
                        Paragraph(d["nombre"], estilos["Normal"]),
                        Paragraph(f'<font color="{c}"><b>{d["n_incidencias"]}</b></font>', estilos["Normal"]),
                    ])

                t = Table(filas, colWidths=[13*cm, 5*cm], repeatRows=1)
                t.setStyle(TABLA_STYLE)
                story.append(t)
                story.append(Spacer(1, 0.4*cm))

            # Primero las activaciones críticas en rojo
            _pintar_tabla_incidencias(inc_rojas, f"Activaciones críticas de {etiqueta}")

            # Después el resto
            _pintar_tabla_incidencias(inc_resto, f"Resto de activaciones de {etiqueta}")

        story.extend(_entrada_toc(toc, f"{s}. Activaciones de alarmas", estilos["Seccion"], 0, f"sec{s}"))
        story.extend(_entrada_toc(toc, f"{s}.1  Servicios", estilos["Subseccion"], 1, f"sec{s}_1"))
        _tabla_incidencias(uptime, "servicios")
        story.append(Spacer(1, 0.6*cm))
        story.extend(_entrada_toc(toc, f"{s}.2  Contenedores Docker", estilos["Subseccion"], 1, f"sec{s}_2"))
        _tabla_incidencias(uptime_docker, "contenedores Docker")
        story.append(PageBreak())

    # ── Sección: Métricas de servidores ──────────────────────────────────────
    if "recursos" in secciones:
        num_sec += 1
        s = num_sec
        story.extend(_entrada_toc(toc, f"{s}. Métricas de servidores y VMs",
                                  estilos["Seccion"], 0, f"sec{s}_metricas"))
        story.append(Paragraph(
            "A continuación se detalla el rendimiento de cada servidor y máquina virtual.",
            estilos["Nota"]
        ))
        if metricas:
            # Diario: registros intradía. Mensual: medias diarias. Anual: sin ARIMA.
            if (fin_dt - inicio_dt).total_seconds() <= 2 * 86400:
                modo_metricas = "diario"
            else:
                modo_metricas = "anual" if modo_histograma == "mensual" else "mensual"
            for idx_host, (host, datos) in enumerate(sorted(metricas.items())):
                anchor_host = f"sec{s}_{idx_host + 1}"
                story.extend(_entrada_toc(toc, f"{s}.{idx_host + 1}  {host}",
                                          estilos["Seccion"], 1, anchor_host))
                story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_SECUNDARIO, spaceAfter=8))
                img_host, img_pred_host, anotaciones_host = grafica_metricas_host(
                    host, datos, modo=modo_metricas, historico=metricas_historico
                )
                story.append(img_host)
                story.append(Spacer(1, 0.4*cm))
                for anotacion in anotaciones_host:
                    story.append(Paragraph(anotacion, estilos["Normal"]))
                    story.append(Spacer(1, 0.15*cm))
                story.append(PageBreak())
                if img_pred_host is not None and "proyecciones" in secciones:
                    story.append(Spacer(1, 0.3*cm))
                    story.append(Paragraph("Predicción para el próximo mes (ARIMA)", estilos["Subseccion"]))
                    story.append(img_pred_host)
                story.append(PageBreak())
        else:
            story.append(Paragraph("Sin datos de métricas en el período.", estilos["Normal"]))
        #story.append(PageBreak())

    # ── Sección: Disparos de alarmas + análisis de fallos ────────────────────
    if "disparos" in secciones and histograma:
        num_sec += 1
        s = num_sec
        story.extend(_entrada_toc(toc, f"{s}. Disparos de alarmas en el período",
                                  estilos["Seccion"], 0, f"sec{s}"))

        # Pasamos fallos_contexto solo si la sección "fallos" está activa.
        # grafica_histograma_alarmas solo añade marcadores para alarmas que
        # tienen contexto real; el resto no muestra ninguna fecha.
        fc = fallos_contexto if "fallos" in secciones else {}
        paginas_alarmas = grafica_histograma_alarmas(
            histograma, modo_histograma, inicio_dt, fin_dt, fc
        )

        for idx_al, (img_alarma, anotaciones_al) in enumerate(paginas_alarmas):
            nombre_al = list(histograma.values())[idx_al]["nombre"]
            anchor_al = f"sec{s}_{idx_al + 1}"
            story.extend(_entrada_toc(toc, f"{s}.{idx_al + 1}  {nombre_al}",
                                      estilos["Seccion"], 1, anchor_al))
            story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_SECUNDARIO, spaceAfter=8))
            story.append(img_alarma)
            story.append(Spacer(1, 0.4*cm))

            for anotacion in anotaciones_al:
                if anotacion == "__FALLOS_INICIO__":
                    story.append(Spacer(1, 0.4*cm))
                    story.append(Paragraph("Análisis de fallos", estilos["Subseccion"]))
                    story.append(Paragraph(
                        "Eventos CRITICAL con diagnóstico de estado del contenedor y "
                        "recursos de la máquina virtual asociada.",
                        estilos["Nota"]
                    ))
                    story.append(Spacer(1, 0.2*cm))
                elif anotacion.startswith("__FALLO__"):
                    payload   = anotacion[len("__FALLO__"):]
                    fecha_str, contexto = payload.split("|", 1)
                    story.append(Paragraph(
                        f'<font color="{COLOR_CRITICAL.hexval()}"><b>▶ {fecha_str}</b></font>'
                        f' — {contexto}',
                        estilos["FalloItem"]
                    ))
                    story.append(Spacer(1, 0.1*cm))
                else:
                    story.append(Paragraph(anotacion, estilos["Normal"]))
                    story.append(Spacer(1, 0.15*cm))

            #if (idx_al + 1) % 2 == 0:
            story.append(PageBreak())

    doc.multiBuild(story)
    log.debug("PDF generado correctamente")
    return buf.getvalue()


# ── Envío por email ──────────────────────────────────────────────────────────

def enviar_informe(pdf_bytes: bytes, nombre_fichero: str,
                   smtp_host: str, smtp_port: int,
                   smtp_user: str, smtp_password: str,
                   email_from: str, email_to: str,
                   asunto: str, log: logging.Logger) -> None:
    msg = MIMEMultipart()
    msg["Subject"] = asunto
    msg["From"]    = email_from
    msg["To"]      = email_to
    msg.attach(MIMEText(
        f"Adjunto encontrarás el informe de monitorización correspondiente al período indicado.\n\n"
        f"Generado automáticamente el {datetime.now().strftime('%d/%m/%Y a las %H:%M')}.",
        "plain", "utf-8"
    ))
    adjunto = MIMEBase("application", "pdf")
    adjunto.set_payload(pdf_bytes)
    encoders.encode_base64(adjunto)
    adjunto.add_header("Content-Disposition", "attachment", filename=nombre_fichero)
    msg.attach(adjunto)

    log.debug(f"Conectando a {smtp_host}:{smtp_port}")
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as servidor:
        servidor.login(smtp_user, smtp_password)
        servidor.sendmail(email_from, email_to, msg.as_string())
    log.info(f"Informe enviado a {email_to}")


# ── Función principal compartida ─────────────────────────────────────────────

def ejecutar_informe(args, inicio_dt: datetime, fin_dt: datetime,
                     titulo: str, periodo_str: str, nombre_fichero: str,
                     modo_histograma: str,
                     umbral_uptime: float,
                     log: logging.Logger,
                     secciones: set[str] | None = None,
                     alarm_ids: list[str] | None = None,
                     hosts_vm: list[str] | None = None,
                     email_to_override: str | None = None) -> None:
    """
    Orquesta todas las consultas, genera el PDF y lo envía por email.

    secciones:         secciones a incluir (ver generar_pdf).
    alarm_ids:         filtro de alarmas por responsable (informes diarios).
    hosts_vm:          filtro de hosts por responsable (informes diarios).
    email_to_override: email destino alternativo; si es None usa args.email_to.
    """
    inicio = inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fin    = fin_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    log.info(f"Generando informe: {titulo} | {periodo_str}")
    log.debug(f"Rango: {inicio} → {fin} | secciones={secciones} | alarm_ids={alarm_ids}")

    client    = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    query_api = client.query_api()

    if secciones is None:
        secciones = {"uptime", "activaciones", "recursos", "proyecciones", "disparos", "fallos"}

    log.info("Consultando uptime de servicios...")
    uptime = consultar_uptime_por_categoria(
        query_api, args.influx_org, args.influx_bucket, inicio, fin, "servicio", log,
        alarm_ids=alarm_ids
    )

    log.info("Consultando uptime de contenedores Docker...")
    uptime_docker = consultar_uptime_por_categoria(
        query_api, args.influx_org, args.influx_bucket, inicio, fin, "docker", log,
        alarm_ids=alarm_ids
    )

    metricas           = {}
    metricas_historico = None
    if "recursos" in secciones or "proyecciones" in secciones:
        log.info("Consultando métricas de servidores...")
        detalle_metricas = _detalle_metricas_por_periodo(inicio_dt, fin_dt)
        log.info(f"Detalle de métricas: {detalle_metricas}")
        metricas = consultar_metricas_servidor(
            query_api, args.influx_org, args.influx_bucket, inicio, fin, log,
            hosts=hosts_vm,
            detalle=detalle_metricas
        )
        if "proyecciones" in secciones:
            log.info("Consultando histórico de los últimos 365 días para predicción ARIMA...")
            metricas_historico = consultar_metricas_historico_completo(
                query_api, args.influx_org, args.influx_bucket, log,
                hosts=hosts_vm,
                fin=fin
            )

    histograma = {}
    if "disparos" in secciones:
        log.info("Consultando histograma de alarmas...")
        histograma = consultar_histograma_alarmas(
            query_api, args.influx_org, args.influx_bucket, inicio, fin, modo_histograma, log,
            alarm_ids=alarm_ids
        )

    fallos_contexto = {}
    if "fallos" in secciones:
        log.info("Consultando fallos con contexto...")
        fallos_contexto = consultar_fallos_con_contexto(
            query_api, args.influx_org, args.influx_bucket, inicio, fin, log,
            alarm_ids=alarm_ids
        )

    log.info("Generando PDF...")
    pdf_bytes = generar_pdf(
        titulo, periodo_str,
        uptime, uptime_docker,
        metricas, histograma,
        modo_histograma, inicio_dt, fin_dt,
        umbral_uptime, log,
        metricas_historico=metricas_historico,
        fallos_contexto=fallos_contexto,
        secciones=secciones,
    )

    if args.output_dir:
        output_dir = os.path.expanduser(args.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        ruta = os.path.join(output_dir, nombre_fichero)
        with open(ruta, "wb") as f:
            f.write(pdf_bytes)
        log.info(f"PDF guardado en {ruta}")

    destinatario = email_to_override or args.email_to
    log.info(f"Enviando informe a {destinatario}...")
    enviar_informe(
        pdf_bytes, nombre_fichero,
        args.smtp_host, args.smtp_port,
        args.smtp_user, args.smtp_password,
        args.email_from, destinatario,
        f"{titulo} — {periodo_str}",
        log
    )

    log.info("Informe completado")
