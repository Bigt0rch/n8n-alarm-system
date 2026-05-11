# Guía de definición de alarmas (`alarmas.json`)

## Estructura general

Todas las alarmas se definirán en el fichero `alarmas.json`. Los campos `nombre`, `tipo`, `categoria`, `objetivo` y `responsable` son **obligatorios en todas las alarmas**. El resto depende de la combinación de `tipo` y `categoria`.

```json
{
  "nombre":      "Nombre de la alarma",
  "tipo":        "estado | metrica",
  "categoria":   "servicio | docker | servidor",
  "objetivo":    "identificador del recurso a monitorizar",
  "responsable": "correo@ejemplo.com"
}
```

---

## Campo `tipo`

| Valor     | Descripción |
|-----------|-------------|
| `estado`  | Comprueba si un recurso está vivo o responde correctamente. La condición es binaria: `status_code` en `200` o `403` → OK, cualquier otro valor → CRITICAL. |
| `metrica` | Evalúa un valor numérico contra un umbral con un operador. Requiere los campos adicionales `metrica`, `operador` y `umbral`. |

---

## Tipos de alarma por categoría

### 1. `categoria: "servicio"` — Estado de un servicio HTTP

Monitoriza si un servicio web responde correctamente mediante una petición HTTP GET directa. El campo `objetivo` debe coincidir con el alias definido en `servicios.json`. El campo `url` es **obligatorio** para esta categoría e indica la dirección a la que se realizará el chequeo.

Los códigos que se consideran OK son `200` y `403`. Cualquier otro código, así como errores de conexión, se registran como `500` y disparan CRITICAL.

> El resultado del chequeo se persiste automáticamente en la medición `servicio_gen` de InfluxDB para su visualización en Grafana.

```json
{
  "nombre":      "Web Principal Down",
  "tipo":        "estado",
  "categoria":   "servicio",
  "objetivo":    "web_corporativa",
  "url":         "https://web-corporativa.ejemplo.com",
  "responsable": "admin@ejemplo.com"
}
```

---

### 2. `categoria: "docker"` — Estado de un contenedor

Monitoriza si un contenedor Docker responde correctamente. El campo `objetivo` debe coincidir con el `alias` definido en `dockers.json` (no con el nombre real del contenedor).

La evaluación es idéntica a la de servicios: `status_code == 200` → OK. Los códigos posibles que puede registrar `monitor_docker.py` son:

| Código | Situación                                   |
|--------|---------------------------------------------|
| `200`  | Contenedor en estado `running`              |
| `500`  | Contenedor existe pero no está en `running` |
| `404`  | Contenedor no encontrado                    |

```json
{
  "nombre":      "Contenedor Base de Datos",
  "tipo":        "estado",
  "categoria":   "docker",
  "objetivo":    "mongodb_container",
  "responsable": "admin@ejemplo.com"
}
```

---

### 3. `categoria: "servidor"` — Métrica de sistema (CPU, RAM, disco)

Monitoriza valores numéricos del host recogidos por Telegraf. Requiere los campos `metrica`, `operador` y `umbral`.

```json
{
  "nombre":      "CPU Servidor Crítica",
  "tipo":        "metrica",
  "categoria":   "servidor",
  "objetivo":    "host_local",
  "metrica":     "usage_user",
  "operador":    ">",
  "umbral":      90,
  "responsable": "admin@ejemplo.com"
}
```

#### Métricas disponibles por input de Telegraf

**CPU** (`[[inputs.cpu]]`)

| `metrica`      | Descripción               | Rango   |
|----------------|---------------------------|---------|
| `usage_user`   | Uso en espacio de usuario | 0–100 % |
| `usage_system` | Uso en espacio del kernel | 0–100 % |
| `usage_idle`   | Porcentaje de CPU libre   | 0–100 % |

**Memoria** (`[[inputs.mem]]`)

| `metrica`      | Descripción              | Rango   |
|----------------|--------------------------|---------|
| `used_percent` | Porcentaje de RAM en uso | 0–100 % |
| `used`         | RAM usada en bytes       | bytes   |
| `available`    | RAM disponible en bytes  | bytes   |

**Disco** (`[[inputs.disk]]`)

| `metrica`      | Descripción                | Rango   |
|----------------|----------------------------|---------|
| `used_percent` | Porcentaje de disco en uso | 0–100 % |

---

### 4. `categoria: "docker"` con `tipo: "metrica"` — Métrica de un contenedor

Monitoriza recursos de un contenedor específico recogidos por Telegraf. El campo `objetivo` debe ser el nombre real del contenedor (no el alias).

```json
{
  "nombre":      "RAM Contenedor API",
  "tipo":        "metrica",
  "categoria":   "docker",
  "objetivo":    "api_backend",
  "metrica":     "usage_percent",
  "operador":    ">",
  "umbral":      80,
  "responsable": "admin@ejemplo.com"
}
```

#### Métricas disponibles (`[[inputs.docker]]`)

| `metrica`       | Descripción                     | Rango   |
|-----------------|---------------------------------|---------|
| `usage_percent` | CPU o RAM en uso del contenedor | 0–100 % |
| `rx_bytes`      | Bytes de red recibidos          | bytes   |
| `tx_bytes`      | Bytes de red enviados           | bytes   |

---

## Campo `operador` (solo `tipo: "metrica"`)

| Operador | Dispara CRITICAL cuando...                      |
|----------|-------------------------------------------------|
| `>`      | El valor **supera** el umbral                   |
| `<`      | El valor **cae por debajo** del umbral          |
| `>=`     | El valor **iguala o supera** el umbral          |
| `<=`     | El valor **iguala o cae por debajo** del umbral |

---

## Referencia rápida de campos

| Campo        | Obligatorio                        | Aplica a                     | Descripción |
|--------------|------------------------------------|------------------------------|-------------|
| `nombre`     | Sí                                 | Todas                        | Nombre descriptivo. Se usa como identificador interno y en el asunto del correo. |
| `tipo`       | Sí                                 | Todas                        | `estado` o `metrica`. |
| `categoria`  | Sí                                 | Todas                        | `servicio`, `docker` o `servidor`. |
| `objetivo`   | Sí                                 | Todas                        | Alias del recurso (servicios/docker estado) o nombre del contenedor/host (métricas). |
| `url`        | Sí si `categoria: servicio`        | `servicio`                   | URL a la que se realiza el chequeo HTTP GET. |
| `responsable`| Sí                                 | Todas                        | Dirección de correo que recibirá las notificaciones. |
| `metrica`    | Sí si `tipo: metrica`              | `docker`, `servidor`         | Nombre del field en InfluxDB. |
| `operador`   | Sí si `tipo: metrica`              | `docker`, `servidor`         | `>`, `<`, `>=` o `<=`. |
| `umbral`     | Sí si `tipo: metrica`              | `docker`, `servidor`         | Valor numérico de referencia. |
