"""
reportGenerator.py
Módulo compartido con toda la lógica de consulta a InfluxDB
y generación del PDF. Importado por reportMonthly.py y reportAnnual.py.
"""
import io
import logging
import os
import smtplib
from datetime import date, datetime, timezone, timedelta, timedelta
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
    # Estilos para el índice (nivel 1 = secciones, nivel 2 = subsecciones)
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

def consultar_uptime_servicios(query_api, org: str, bucket: str,
                                inicio: str, fin: str,
                                log: logging.Logger) -> list[dict]:
    """
    Reconstruye el uptime de cada servicio a partir de alarm_notifications.
    Devuelve lista de dicts: nombre, uptime_pct, tiempo_caido_min, n_incidencias.
    """
    query = f'''
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["categoria"] == "servicio")
          |> filter(fn: (r) => r["_field"] == "estado")
          |> sort(columns: ["_time"])
    '''
    tablas = query_api.query(query, org=org)

    # Agrupar eventos por alarm_id
    por_alarma: dict[str, list] = {}
    por_alarma_nombre: dict[str, str] = {}
    for tabla in tablas:
        for rec in tabla.records:
            aid   = rec.values.get("alarm_id", "")
            ts    = rec.get_time()
            estado = rec.get_value()
            # Nombre de la alarma (field nombre_alarma si existe, si no alarm_id)
            nombre = rec.values.get("nombre_alarma", aid)
            if aid not in por_alarma:
                por_alarma[aid] = []
                por_alarma_nombre[aid] = nombre
            por_alarma[aid].append((ts, estado))

    inicio_dt = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    fin_dt    = datetime.fromisoformat(fin.replace("Z", "+00:00"))
    periodo_total = (fin_dt - inicio_dt).total_seconds()

    resultados = []
    for aid, eventos in por_alarma.items():
        eventos.sort(key=lambda x: x[0])
        tiempo_ok = 0.0
        n_incidencias = 0
        estado_actual = "OK"
        ts_anterior = inicio_dt

        for ts, estado in eventos:
            delta = (ts - ts_anterior).total_seconds()
            if estado_actual == "OK":
                tiempo_ok += delta
            else:
                n_incidencias += 1 if estado == "OK" else 0
            estado_actual = estado
            ts_anterior = ts

        # Último tramo hasta fin del período
        delta_final = (fin_dt - ts_anterior).total_seconds()
        if estado_actual == "OK":
            tiempo_ok += delta_final

        uptime_pct     = round((tiempo_ok / periodo_total) * 100, 3) if periodo_total > 0 else 100.0
        tiempo_caido_m = round((periodo_total - tiempo_ok) / 60, 1)

        resultados.append({
            "alarm_id":        aid,
            "nombre":          por_alarma_nombre[aid],
            "uptime_pct":      uptime_pct,
            "tiempo_caido_min": tiempo_caido_m,
            "n_incidencias":   n_incidencias,
        })

    resultados.sort(key=lambda x: x["uptime_pct"])
    log.debug(f"Uptime calculado para {len(resultados)} alarma(s)")
    return resultados


def consultar_metricas_servidor(query_api, org: str, bucket: str,
                                 inicio: str, fin: str,
                                 log: logging.Logger) -> dict:
    """
    Devuelve medias diarias de CPU y RAM agrupadas por host.
    { host: { "cpu": [(fecha, valor), ...], "ram": [(fecha, valor), ...] } }
    """
    resultado = {}

    for metrica, measurement, field in [
        ("cpu",  "cpu",  "usage_user"),
        ("ram",  "mem",  "used_percent"),
    ]:
        query = f'''
            from(bucket: "{bucket}")
              |> range(start: {inicio}, stop: {fin})
              |> filter(fn: (r) => r["_measurement"] == "{measurement}")
              |> filter(fn: (r) => r["_field"] == "{field}")
              |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
              |> sort(columns: ["_time"])
        '''
        tablas = query_api.query(query, org=org)
        for tabla in tablas:
            host = tabla.records[0].values.get("host", "servidor") if tabla.records else "servidor"
            if host not in resultado:
                resultado[host] = {"cpu": [], "ram": []}
            for rec in tabla.records:
                valor = rec.get_value()
                if valor is not None:
                    resultado[host][metrica].append((rec.get_time(), round(valor, 2)))

    log.debug(f"Métricas de servidor obtenidas para {len(resultado)} host(s)")
    return resultado


def consultar_histograma_alarmas(query_api, org: str, bucket: str,
                                  inicio: str, fin: str,
                                  modo: str,
                                  log: logging.Logger) -> dict:
    """
    Para cada alarma, cuenta cuántas veces ha pasado a CRITICAL agrupado
    por día (modo="diario") o por mes (modo="mensual").

    Devuelve un dict:
      {
        alarm_id: {
          "nombre": str,
          "conteos": { "2026-04-01": 3, "2026-04-15": 1, ... }
        },
        ...
      }
    """
    query = f'''
        from(bucket: "{bucket}")
          |> range(start: {inicio}, stop: {fin})
          |> filter(fn: (r) => r["_measurement"] == "alarm_notifications")
          |> filter(fn: (r) => r["_field"] == "estado")
          |> filter(fn: (r) => r["_value"] == "CRITICAL")
          |> group(columns: ["alarm_id"])
          |> sort(columns: ["_time"])
    '''
    tablas = query_api.query(query, org=org)

    resultado: dict = {}
    for tabla in tablas:
        for rec in tabla.records:
            aid    = rec.values.get("alarm_id", "desconocida")
            nombre = aid.replace("alarma:", "").replace("_", " ").title()
            ts     = rec.get_time()

            # Clave de agrupación: "YYYY-MM-DD" o "YYYY-MM"
            clave = ts.strftime("%Y-%m-%d") if modo == "diario" else ts.strftime("%Y-%m")

            if aid not in resultado:
                resultado[aid] = {"nombre": nombre, "conteos": {}}
            resultado[aid]["conteos"][clave] = resultado[aid]["conteos"].get(clave, 0) + 1

    log.debug(f"Histograma calculado para {len(resultado)} alarma(s) (modo={modo})")
    return resultado


# ── Funciones de análisis para anotaciones ──────────────────────────────────

DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def _dia_semana_mayor_media(puntos: list[tuple]) -> str:
    """
    Dado [(datetime, valor), ...] devuelve el nombre del día de la semana
    cuya media de valores es la más alta.
    """
    from collections import defaultdict
    acum  = defaultdict(list)
    for ts, v in puntos:
        acum[ts.weekday()].append(v)
    if not acum:
        return "–"
    medias = {d: sum(vs) / len(vs) for d, vs in acum.items()}
    return DIAS_ES[max(medias, key=medias.get)]

def _dia_mes_mayor_consumo(puntos: list[tuple]) -> str:
    """
    Dado [(datetime, valor), ...] devuelve el día del mes (número)
    con la media de valores más alta.
    """
    from collections import defaultdict
    acum = defaultdict(list)
    for ts, v in puntos:
        acum[ts.day].append(v)
    if not acum:
        return "–"
    medias = {d: sum(vs) / len(vs) for d, vs in acum.items()}
    return str(max(medias, key=medias.get))

def _dia_semana_mayor_disparos(conteos: dict) -> str:
    """
    Dado {"YYYY-MM-DD": n, ...} devuelve el nombre del día de la semana
    con más disparos totales (suma, no media, porque puede haber pocos datos).
    """
    from collections import defaultdict
    acum = defaultdict(int)
    for clave, n in conteos.items():
        try:
            dt = datetime.strptime(clave, "%Y-%m-%d")
            acum[dt.weekday()] += n
        except ValueError:
            pass  # clave mensual, no aplica
    if not acum:
        return "–"
    return DIAS_ES[max(acum, key=acum.get)]

def _dia_mes_mayor_disparos(conteos: dict) -> str:
    """
    Dado {"YYYY-MM-DD": n, ...} devuelve el día del mes con más disparos.
    """
    if not conteos:
        return "–"
    # Solo claves diarias (formato YYYY-MM-DD)
    diarias = {k: v for k, v in conteos.items() if len(k) == 10}
    if not diarias:
        return "–"
    clave_max = max(diarias, key=diarias.get)
    return str(int(clave_max[8:10]))  # día sin cero inicial


# ── Gráficas ─────────────────────────────────────────────────────────────────

def _fig_to_image(fig, ancho_cm=16) -> Image:
    """Convierte una figura matplotlib en un objeto Image de ReportLab."""
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
    """
    Calcula la regresión lineal de [(datetime, valor), ...].
    Devuelve (pendiente_por_dia, media_estimada_siguiente_mes, tendencia_str).
    """
    import numpy as np
    if len(puntos) < 2:
        return 0.0, None, "estable"
    ts0    = puntos[0][0].timestamp()
    xs     = [(ts.timestamp() - ts0) / 86400 for ts, _ in puntos]  # días desde inicio
    ys     = [v for _, v in puntos]
    n      = len(xs)
    sum_x  = sum(xs);  sum_y  = sum(ys)
    sum_xx = sum(x*x for x in xs);  sum_xy = sum(x*y for x, y in zip(xs, ys))
    denom  = n * sum_xx - sum_x ** 2
    if denom == 0:
        return 0.0, None, "estable"
    pendiente   = (n * sum_xy - sum_x * sum_y) / denom
    intercepto  = (sum_y - pendiente * sum_x) / n
    dias_sig    = xs[-1] + 30           # +30 días = siguiente mes
    media_est   = round(intercepto + pendiente * dias_sig, 1)
    media_est   = max(0.0, min(100.0, media_est))
    if pendiente > 0.1:
        tendencia = "creciente"
    elif pendiente < -0.1:
        tendencia = "decreciente"
    else:
        tendencia = "estable"
    return pendiente, media_est, tendencia


def _subplot_metrica(ax, puntos: list[tuple], color: str, label: str):
    """Dibuja únicamente la serie temporal real en un eje dado."""
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


def _grafica_regresion(puntos_cpu: list[tuple], puntos_ram: list[tuple], host: str) -> Image:
    """
    Genera una figura separada con la proyección de regresión lineal de CPU y RAM
    extendida 30 días hacia el futuro (próximo mes).
    """
    from datetime import timedelta

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), sharex=False)
    fig.suptitle(f"Proyección próximo mes — {host}", fontsize=12,
                 color="#1a3a5c", fontweight="bold")

    for ax, puntos, color, label in [
        (ax1, puntos_cpu, "#2e86c1", "CPU"),
        (ax2, puntos_ram, "#1e8449", "RAM"),
    ]:
        if not puntos or len(puntos) < 2:
            ax.set_ylabel(f"{label} (%)", fontsize=8)
            ax.text(0.5, 0.5, "Sin datos suficientes", ha="center", va="center",
                    transform=ax.transAxes, color="grey", fontsize=9)
            continue

        fechas_real = [ts for ts, _ in puntos]
        vals_real   = [v  for _, v  in puntos]
        ts0 = puntos[0][0].timestamp()
        xs  = [(ts.timestamp() - ts0) / 86400 for ts, _ in puntos]

        n = len(xs); sum_x = sum(xs); sum_y = sum(vals_real)
        sum_xx = sum(x*x for x in xs)
        sum_xy = sum(x*y for x, y in zip(xs, vals_real))
        denom  = n * sum_xx - sum_x ** 2
        if denom == 0:
            continue
        pend = (n * sum_xy - sum_x * sum_y) / denom
        inte = (sum_y - pend * sum_x) / n

        # Datos históricos atenuados
        ax.plot(fechas_real, vals_real, color=color, linewidth=1,
                alpha=0.35, linestyle="-", marker="o", markersize=2, label="Histórico")

        # Línea de regresión sobre el período histórico
        reg_hist = [max(0, min(100, inte + pend * x)) for x in xs]
        ax.plot(fechas_real, reg_hist, color=color, linewidth=1.5,
                linestyle="--", alpha=0.7)

        # Proyección: 30 días adicionales
        ultimo_ts  = puntos[-1][0]
        fechas_fut = [ultimo_ts + timedelta(days=d) for d in range(1, 31)]
        xs_fut     = [xs[-1] + d for d in range(1, 31)]
        reg_fut    = [max(0, min(100, inte + pend * x)) for x in xs_fut]

        ax.plot(fechas_fut, reg_fut, color=color, linewidth=2,
                linestyle="-", label="Proyección")

        # Banda de confianza simple (±desviación estándar de residuos)
        residuos = [y - (inte + pend * x) for x, y in zip(xs, vals_real)]
        std_res  = (sum(r*r for r in residuos) / len(residuos)) ** 0.5
        ax.fill_between(fechas_fut,
                        [max(0,   v - std_res) for v in reg_fut],
                        [min(100, v + std_res) for v in reg_fut],
                        color=color, alpha=0.12, label=f"±{round(std_res, 1)}%")

        ax.axvline(x=ultimo_ts, color="grey", linewidth=0.8, linestyle=":", alpha=0.7)
        ax.set_ylabel(f"{label} (%)", fontsize=8)
        ax.set_ylim(0, 100)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.legend(fontsize=7, loc="upper left")

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    return _fig_to_image(fig)


def grafica_metricas_host(host: str, datos: dict) -> tuple:
    """
    Gráfica de CPU y RAM (línea real + línea de tendencia lineal).
    Devuelve (Image, lista_anotaciones).
    """
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    fig.suptitle(f"Métricas diarias — {host}", fontsize=12, color="#1a3a5c", fontweight="bold")

    _subplot_metrica(axes[0], datos["cpu"], "#2e86c1", "CPU")
    _subplot_metrica(axes[1], datos["ram"], "#1e8449", "RAM")

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    img = _fig_to_image(fig)

    anotaciones = []
    for metrica, label, color in [("cpu", "CPU", "#2e86c1"), ("ram", "RAM", "#1e8449")]:
        puntos = datos[metrica]
        if not puntos:
            continue
        vals_solo  = [v for _, v in puntos]
        media      = round(sum(vals_solo) / len(vals_solo), 1)
        maximo     = round(max(vals_solo), 1)
        ts_max     = puntos[[v for _, v in puntos].index(max(vals_solo))][0]
        dia_max    = ts_max.strftime("%d/%m")
        dia_semana = _dia_semana_mayor_media(puntos)
        dia_mes    = _dia_mes_mayor_consumo(puntos)
        _, media_est, tendencia = _regresion_lineal(puntos)

        anotaciones.append(
            f"<b>{label}</b> — El consumo medio durante el período ha sido de <b>{media}%</b>, "
            f"con un máximo puntual de <b>{maximo}%</b> el día <b>{dia_max}</b>."
        )
        anotaciones.append(
            f"El día de la semana con mayor consumo de {label} es el <b>{dia_semana}</b>. "
            f"El día del mes con mayor consumo ha sido el <b>{dia_mes}</b>."
        )
        if media_est is not None:
            anotaciones.append(
                f"La tendencia de uso de {label} es <b>{tendencia}</b>, "
                f"estimándose una media de <b>{media_est}%</b> para el próximo mes."
            )

    img_reg = _grafica_regresion(datos["cpu"], datos["ram"], host)
    return img, img_reg, anotaciones


# Alarmas por página (cada una con su propia gráfica de barras)
_ALARMAS_POR_PAGINA = 4

def grafica_histograma_alarmas(datos: dict, modo: str, inicio_dt: datetime, fin_dt: datetime) -> list[tuple]:
    """
    Genera una gráfica de barras verticales por alarma mostrando cuántas
    veces ha pasado a CRITICAL cada día (modo="diario") o cada mes
    (modo="mensual"). Agrupa _ALARMAS_POR_PAGINA alarmas por imagen
    para que cada página del PDF sea legible.

    Devuelve una lista de objetos Image.
    """
    if not datos:
        fig, ax = plt.subplots(figsize=(10, 2))
        ax.text(0.5, 0.5, "Sin alarmas en el período", ha="center", va="center")
        ax.axis("off")
        return [_fig_to_image(fig)]

    alarmas  = list(datos.items())  # [(alarm_id, {nombre, conteos}), ...]
    paginas  = []  # lista de (Image, [anotaciones]) — una entrada por alarma

    # Rango completo de claves (calculado una sola vez)
    if modo == "diario":
        cur = inicio_dt.date()
        fin_d = fin_dt.date()
        claves_completas = []
        while cur <= fin_d:
            claves_completas.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        etiquetas_base = [c[8:] for c in claves_completas]
        xlabel = "Día del mes"
    else:
        meses_abrev = ["Ene","Feb","Mar","Abr","May","Jun",
                       "Jul","Ago","Sep","Oct","Nov","Dic"]
        anyo_i, mes_i = inicio_dt.year, inicio_dt.month
        anyo_f, mes_f = fin_dt.year,   fin_dt.month
        claves_completas = []
        y, m = anyo_i, mes_i
        while (y, m) <= (anyo_f, mes_f):
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
            ax.text(0.5, 0.5, "Sin disparos en el período", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="grey")
            ax.axis("off")
        else:
            #color_barras = ["#c0392b" if v > 5 else "#e67e22" if v > 2 else "#2e86c1" for v in valores]
            color_barras = ["#2e86c1" for v in valores]
            bars = ax.bar(etiquetas_base, valores, color=color_barras, edgecolor="white", width=0.6)
            ax.bar_label(bars, padding=2, fontsize=7)
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel("Disparos", fontsize=8)
            ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
            ax.grid(axis="y", linestyle="--", alpha=0.4)
            ax.tick_params(axis="x", labelsize=7)
            ax.tick_params(axis="y", labelsize=7)

        fig.tight_layout()
        img = _fig_to_image(fig, ancho_cm=16)
        plt.close(fig)

        # Anotaciones de esta alarma
        anotaciones = []
        if any(valores):
            total = sum(valores)
            anotaciones.append(
                f"Esta alarma ha saltado un total de <b>{total}</b> veces durante el período."
            )
            if modo == "diario" and conteos:
                dia_semana = _dia_semana_mayor_disparos(conteos)
                dia_mes    = _dia_mes_mayor_disparos(conteos)
                anotaciones.append(
                    f"El día de la semana en que más ha saltado es el <b>{dia_semana}</b>. "
                    f"El día del mes con más disparos ha sido el <b>{dia_mes}</b>."
                )

        paginas.append((img, anotaciones))

    return paginas


# ── Construcción del PDF ─────────────────────────────────────────────────────

class _EntradaTOC(Flowable):
    """
    Flowable invisible que, en el momento del renderizado, registra una
    entrada en el TableOfContents y crea un bookmark PDF en la página actual.
    Debe colocarse justo antes del Paragraph de título visible.
    """
    def __init__(self, toc: TableOfContents, texto: str, nivel: int, anchor: str):
        Flowable.__init__(self)
        self._toc    = toc
        self._texto  = texto
        self._nivel  = nivel
        self._anchor = anchor
        self.width   = 0
        self.height  = 0

    def draw(self):
        # Bookmark PDF (permite navegación directa en el visor)
        self.canv.bookmarkPage(self._anchor)
        # Notificar al TOC: (nivel, texto, número_de_página, anchor)
        self._toc.notify("TOCEntry", (self._nivel, self._texto,
                                      self.canv.getPageNumber(), self._anchor))


def _entrada_toc(toc: TableOfContents, texto: str, estilo_titulo,
                 nivel: int, anchor: str) -> list:
    """
    Devuelve [flowable_invisible_de_registro, párrafo_de_título_visible].
    """
    return [_EntradaTOC(toc, texto, nivel, anchor), Paragraph(texto, estilo_titulo)]


def generar_pdf(titulo: str, periodo_str: str,
                uptime: list[dict],
                metricas: dict,
                histograma: dict,
                modo_histograma: str,
                inicio_dt: datetime,
                fin_dt: datetime,
                umbral_uptime: float,
                log: logging.Logger) -> bytes:
    """
    Genera el PDF completo y lo devuelve como bytes.
    """
    estilos = crear_estilos()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
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
    toc.dotsMinLevel = 0   # puntos de relleno en todos los niveles
    story.append(toc)
    story.append(PageBreak())

    # ── Sección 1: Uptime de servicios ───────────────────────────────────────
    story.extend(_entrada_toc(toc, "1. Uptime de servicios", estilos["Seccion"], 0, "sec1"))

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

    def _color_uptime(pct: float) -> colors.HexColor:
        return COLOR_OK if pct >= umbral_uptime else COLOR_CRITICAL

    if uptime:
        # ── Tabla de uptime: peor uptime primero ─────────────────────────────
        uptime_ord = sorted(uptime, key=lambda d: d["uptime_pct"])

        n_bajo_umbral = sum(1 for d in uptime_ord if d["uptime_pct"] < umbral_uptime)
        story.append(Paragraph(
            f"<b>{n_bajo_umbral}</b> de los <b>{len(uptime_ord)}</b> servicios han tenido un uptime "
            f"inferior al {umbral_uptime}% durante el período.",
            estilos["Nota"]
        ))
        peor = uptime_ord[0]
        story.append(Paragraph(
            f"El servicio con peor disponibilidad ha sido <b>{peor['nombre']}</b>, "
            f"con un uptime del <b>{peor['uptime_pct']}%</b>.",
            estilos["Nota"]
        ))
        story.append(Spacer(1, 0.3*cm))

        filas_up = [["Alarma / Servicio", "Uptime (%)", "Tiempo caído (min)"]]
        for d in uptime_ord:
            color_up = _color_uptime(d["uptime_pct"])
            filas_up.append([
                Paragraph(d["nombre"], estilos["Normal"]),
                Paragraph(f'<font color="{color_up.hexval()}">{d["uptime_pct"]}%</font>', estilos["Normal"]),
                str(d["tiempo_caido_min"]),
            ])

        t_up = Table(filas_up, colWidths=[9*cm, 4*cm, 5*cm], repeatRows=1)
        t_up.setStyle(TABLA_STYLE)
        story.append(t_up)
    else:
        story.append(Paragraph("Sin datos de uptime en el período.", estilos["Normal"]))

    story.append(PageBreak())

    # ── Sección 2: Activaciones de alarmas ───────────────────────────────────
    story.extend(_entrada_toc(toc, "2. Activaciones de alarmas", estilos["Seccion"], 0, "sec2"))

    if uptime:
        # ── Tabla de incidencias: más incidencias primero ─────────────────────
        inc_ord = sorted(uptime, key=lambda d: d["n_incidencias"], reverse=True)

        mas_inc = inc_ord[0]
        story.append(Paragraph(
            f"La alarma con más activaciones ha sido <b>{mas_inc['nombre']}</b>, "
            f"con <b>{mas_inc['n_incidencias']}</b> incidencias en el período.",
            estilos["Nota"]
        ))
        story.append(Spacer(1, 0.3*cm))

        # Colorear incidencias: más incidencias = peor
        incidencias_vals = sorted([d["n_incidencias"] for d in uptime])
        n_inc     = len(incidencias_vals)
        media_inc = sum(incidencias_vals) / n_inc if n_inc else 0
        q3_inc    = incidencias_vals[int(n_inc * 0.75)] if n_inc else 0

        def _color_incidencias(n: int) -> str:
            if n <= media_inc:
                return COLOR_OK.hexval()
            elif n <= q3_inc:
                return colors.HexColor("#e67e22").hexval()
            else:
                return COLOR_CRITICAL.hexval()

        filas_inc = [["Alarma / Servicio", "Incidencias"]]
        for d in inc_ord:
            color_inc = _color_incidencias(d["n_incidencias"])
            filas_inc.append([
                Paragraph(d["nombre"], estilos["Normal"]),
                Paragraph(f'<font color="{color_inc}"><b>{d["n_incidencias"]}</b></font>', estilos["Normal"]),
            ])

        t_inc = Table(filas_inc, colWidths=[13*cm, 5*cm], repeatRows=1)
        t_inc.setStyle(TABLA_STYLE)
        story.append(t_inc)
    else:
        story.append(Paragraph("Sin datos de incidencias en el período.", estilos["Normal"]))

    story.append(PageBreak())

    # ── Sección 3: Métricas de servidores (una página por host) ──────────────
    story.extend(_entrada_toc(toc, "3. Métricas de servidores y VMs", estilos["Seccion"], 0, "sec3_metricas"))
    story.append(Paragraph(
        "A continuación se detalla el rendimiento de cada servidor y máquina virtual.",
        estilos["Nota"]
    ))

    if metricas:
        for idx_host, (host, datos) in enumerate(sorted(metricas.items())):
            anchor_host = f"sec3_{idx_host + 1}"
            story.append(PageBreak())
            story.extend(_entrada_toc(toc, f"3.{idx_host + 1}  {host}", estilos["Seccion"], 1, anchor_host))
            story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_SECUNDARIO, spaceAfter=8))
            img_host, img_reg_host, anotaciones_host = grafica_metricas_host(host, datos)
            story.append(img_host)
            story.append(Spacer(1, 0.4*cm))
            for anotacion in anotaciones_host:
                story.append(Paragraph(anotacion, estilos["Normal"]))
                story.append(Spacer(1, 0.15*cm))
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph("Proyección para el próximo mes", estilos["Subseccion"]))
            story.append(img_reg_host)
    else:
        story.append(Paragraph("Sin datos de métricas en el período.", estilos["Normal"]))

    story.append(PageBreak())

    # ── Sección 4: Disparos de alarmas (una página por alarma) ───────────────
    story.extend(_entrada_toc(toc, "4. Disparos de alarmas en el período", estilos["Seccion"], 0, "sec4"))
    unidad = "día" if modo_histograma == "diario" else "mes"

    paginas_alarmas = grafica_histograma_alarmas(histograma, modo_histograma, inicio_dt, fin_dt)
    for idx_al, (img_alarma, anotaciones_al) in enumerate(paginas_alarmas):
        # Título de la alarma (extraído de los datos originales)
        nombre_al = list(histograma.values())[idx_al]["nombre"]
        anchor_al = f"sec4_{idx_al + 1}"
        story.extend(_entrada_toc(toc, f"4.{idx_al + 1}  {nombre_al}", estilos["Seccion"], 1, anchor_al))
        story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_SECUNDARIO, spaceAfter=8))
        story.append(img_alarma)
        story.append(Spacer(1, 0.4*cm))
        for anotacion in anotaciones_al:
            story.append(Paragraph(anotacion, estilos["Normal"]))
            story.append(Spacer(1, 0.15*cm))
        if (idx_al+1)%2 == 0:
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
                     log: logging.Logger) -> None:
    inicio = inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fin    = fin_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    log.info(f"Generando informe: {titulo} | {periodo_str}")
    log.debug(f"Rango: {inicio} → {fin}")

    client    = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    query_api = client.query_api()

    log.info("Consultando uptime de servicios...")
    uptime = consultar_uptime_servicios(query_api, args.influx_org, args.influx_bucket, inicio, fin, log)

    log.info("Consultando métricas de servidores...")
    metricas = consultar_metricas_servidor(query_api, args.influx_org, args.influx_bucket, inicio, fin, log)

    log.info("Consultando histograma de alarmas...")
    histograma = consultar_histograma_alarmas(query_api, args.influx_org, args.influx_bucket, inicio, fin, modo_histograma, log)

    log.info("Generando PDF...")
    pdf_bytes = generar_pdf(titulo, periodo_str, uptime, metricas, histograma, modo_histograma, inicio_dt, fin_dt, umbral_uptime, log)

    if args.output_dir:
        output_dir = os.path.expanduser(args.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        ruta = os.path.join(output_dir, nombre_fichero)
        with open(ruta, "wb") as f:
            f.write(pdf_bytes)
        log.info(f"PDF guardado en {ruta}")

    log.info("Enviando informe por email...")
    enviar_informe(
        pdf_bytes, nombre_fichero,
        args.smtp_host, args.smtp_port,
        args.smtp_user, args.smtp_password,
        args.email_from, args.email_to,
        f"{titulo} — {periodo_str}",
        log
    )

    log.info("Informe completado")