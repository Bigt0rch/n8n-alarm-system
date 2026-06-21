# Guía de definición de alarmas (`alarms.json`)

## Estructura general

Las alarmas se definen como objetos JSON.

Los campos obligatorios comunes son:

```json
{
  "nombre":      "Nombre descriptivo de la alarma",
  "tipo":        "estado | metrica",
  "categoria":   "servicio | docker | servidor",
  "objetivo":    "identificador del recurso a monitorizar",
  "responsable": "correo@ejemplo.com"
}
```

El resto de campos depende de la combinación de `tipo` y `categoria`.

---

## Campo `tipo`

| Valor     | Descripción                                                                                                                          |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `estado`  | Comprueba si un servicio o contenedor está vivo. La condición se evalúa usando un `status_code`.                                     |
| `metrica` | Evalúa un porcentaje de uso contra un umbral. Solo se permiten métricas de recursos: CPU, RAM y disco raíz cuando estén disponibles. |

---

## Campo `categoria`

| Valor      | Descripción                                                     |
| ---------- | --------------------------------------------------------------- |
| `servicio` | Servicio HTTP comprobado directamente por el script de alarmas. |
| `docker`   | Contenedor Docker. Puede ser alarma de estado o de métrica.     |
| `servidor` | Máquina o host monitorizado por Telegraf.                       |

---

# 1. Alarmas de servicio HTTP

## `categoria: "servicio"` con `tipo: "estado"`

Monitoriza si un servicio HTTP responde correctamente mediante una petición `GET` directa realizada por el propio script de alarmas.

Ya no se usa `servicios.json`.

El campo `url` es obligatorio.

El campo `objetivo` es el nombre lógico del servicio. Este nombre se usa para guardar el resultado del chequeo en InfluxDB y para identificarlo en Grafana.

## Regla de estado

|                                 Código HTTP | Estado                            |
| ------------------------------------------: | --------------------------------- |
|                                       `200` | `OK`                              |
|                                       `403` | `OK`                              |
|                       Cualquier otro código | `CRITICAL`                        |
| Error de conexión, timeout o excepción HTTP | `CRITICAL`, registrado como `500` |

## Escritura auxiliar en InfluxDB

Cada vez que se evalúa una alarma de servicio, el script guarda el resultado en la medición:

```text
servicio_gen
```

Con:

| Tipo  | Nombre        | Valor                |
| ----- | ------------- | -------------------- |
| tag   | `nombre`      | valor de `objetivo`  |
| field | `status_code` | código HTTP obtenido |

## Campos obligatorios

| Campo         | Descripción                          |
| ------------- | ------------------------------------ |
| `nombre`      | Nombre descriptivo de la alarma.     |
| `tipo`        | Debe ser `estado`.                   |
| `categoria`   | Debe ser `servicio`.                 |
| `objetivo`    | Nombre lógico del servicio.          |
| `url`         | URL que se comprobará con HTTP GET.  |
| `responsable` | Correo que recibirá la notificación. |

## Campos opcionales para diagnóstico

| Campo             | Descripción                                                                                                                                        |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `container_name`  | Alias del contenedor definido en dockers.json. Se usa para consultar el estado Docker asociado al servicio cuando la alarma pasa a CRITICAL.                         |
| `maquina_virtual` | Host o máquina virtual donde se ejecuta el servicio. Se usa solo para consultar contexto de CPU, RAM y disco cuando el servicio pasa a `CRITICAL`. |

## Ejemplo

```json
{
  "nombre": "Web Principal Down",
  "tipo": "estado",
  "categoria": "servicio",
  "objetivo": "web_corporativa",
  "url": "https://web-corporativa.ejemplo.com",
  "responsable": "admin@ejemplo.com",
  "container_name": "web_corp_nginx",
  "maquina_virtual": "servidor-prod"
}
```

---

# 2. Alarmas de estado de Docker

## `categoria: "docker"` con `tipo: "estado"`

Monitoriza si un contenedor Docker está en estado `running`.

El estado no lo obtiene directamente el script de alarmas. Lo obtiene el script `monitor_docker.py`, ejecutado por Telegraf, que escribe los resultados en InfluxDB.

El script `monitor_docker.py` lee `dockers.json`, busca el contenedor usando `container_name`, pero publica el resultado usando el `alias`.

Por tanto, en alarmas Docker de estado:

```text
objetivo = alias definido en dockers.json
```

No debe usarse el nombre real del contenedor en este tipo de alarma.

## Origen de datos en InfluxDB

La alarma lee el último valor de:

```text
measurement = docker_gen
tag nombre  = objetivo
field       = status_code
```

## Regla de estado

|                Código | Situación                                                       | Estado     |
| --------------------: | --------------------------------------------------------------- | ---------- |
|                 `200` | Contenedor en estado `running`                                  | `OK`       |
|                 `500` | Contenedor existe pero no está en `running` o hubo error Docker | `CRITICAL` |
|                 `404` | Contenedor no encontrado                                        | `CRITICAL` |
| Cualquier otro código | Estado desconocido                                              | `CRITICAL` |

A diferencia de los servicios HTTP, en Docker solo se considera `OK` el código `200`.

## Campos obligatorios

| Campo         | Descripción                                      |
| ------------- | ------------------------------------------------ |
| `nombre`      | Nombre descriptivo de la alarma.                 |
| `tipo`        | Debe ser `estado`.                               |
| `categoria`   | Debe ser `docker`.                               |
| `objetivo`    | Alias del contenedor definido en `dockers.json`. |
| `responsable` | Correo que recibirá la notificación.             |

## Ejemplo

```json
{
  "nombre": "Contenedor Base de Datos Down",
  "tipo": "estado",
  "categoria": "docker",
  "objetivo": "mongodb_container",
  "responsable": "admin@ejemplo.com"
}
```

---

# 3. Alarmas de recursos de servidor

## `categoria: "servidor"` con `tipo: "metrica"`

Monitoriza porcentajes de uso de recursos del host recogidos por Telegraf.

Solo se permiten estas alarmas:

| Recurso | Qué monitoriza                           | Origen Telegraf   |
| ------- | ---------------------------------------- | ----------------- |
| `cpu`   | Porcentaje total de CPU usada            | `[[inputs.cpu]]`  |
| `ram`   | Porcentaje de memoria RAM usada          | `[[inputs.mem]]`  |
| `disco` | Porcentaje de disco usado en la raíz `/` | `[[inputs.disk]]` |

No se permiten alarmas de bytes, red, memoria disponible en bytes, disco en bytes ni otras métricas que no sean porcentajes de uso.

## Campo `objetivo`

En alarmas de servidor:

```text
objetivo = host de Telegraf
```

Debe coincidir con el tag `host` almacenado por Telegraf en InfluxDB.

## Campo `recurso`

El campo `recurso` es obligatorio y sustituye a la necesidad de indicar manualmente el field de InfluxDB.

Valores permitidos:

```text
cpu
ram
disco
```

## Métricas internas usadas

| `recurso` | Measurement | Filtro adicional                                     | Field usado    | Valor evaluado     |
| --------- | ----------- | ---------------------------------------------------- | -------------- | ------------------ |
| `cpu`     | `cpu`       | `host = objetivo`, preferiblemente `cpu = cpu-total` | `usage_idle`   | `100 - usage_idle` |
| `ram`     | `mem`       | `host = objetivo`                                    | `used_percent` | `used_percent`     |
| `disco`   | `disk`      | `host = objetivo`, `path = "/"`                      | `used_percent` | `used_percent`     |

Para CPU se evalúa el uso total calculado como:

```text
CPU usada = 100 - usage_idle
```

## Campos obligatorios

| Campo         | Descripción                          |
| ------------- | ------------------------------------ |
| `nombre`      | Nombre descriptivo de la alarma.     |
| `tipo`        | Debe ser `metrica`.                  |
| `categoria`   | Debe ser `servidor`.                 |
| `objetivo`    | Host de Telegraf.                    |
| `recurso`     | `cpu`, `ram` o `disco`.              |
| `operador`    | `>`, `<`, `>=` o `<=`.               |
| `umbral`      | Valor numérico de referencia.        |
| `responsable` | Correo que recibirá la notificación. |

## Ejemplo: CPU de servidor

```json
{
  "nombre": "CPU Servidor Crítica",
  "tipo": "metrica",
  "categoria": "servidor",
  "objetivo": "servidor-prod",
  "recurso": "cpu",
  "operador": ">",
  "umbral": 90,
  "responsable": "admin@ejemplo.com"
}
```

## Ejemplo: RAM de servidor

```json
{
  "nombre": "RAM Servidor Crítica",
  "tipo": "metrica",
  "categoria": "servidor",
  "objetivo": "servidor-prod",
  "recurso": "ram",
  "operador": ">=",
  "umbral": 95,
  "responsable": "admin@ejemplo.com"
}
```

## Ejemplo: disco raíz de servidor

```json
{
  "nombre": "Disco Raíz Servidor Crítico",
  "tipo": "metrica",
  "categoria": "servidor",
  "objetivo": "servidor-prod",
  "recurso": "disco",
  "operador": ">",
  "umbral": 85,
  "responsable": "admin@ejemplo.com"
}
```

---

# 4. Alarmas de recursos de Docker

## `categoria: "docker"` con `tipo: "metrica"`

Monitoriza porcentajes de uso de recursos de un contenedor Docker recogidos por Telegraf.

Estas alarmas no usan `monitor_docker.py`. El script `monitor_docker.py` solo se usa para estado del contenedor.

## Campo `objetivo`

En alarmas Docker de métrica:

```text
objetivo = nombre real del contenedor
```

Debe ser el nombre que aparece al ejecutar:

```bash
docker ps -a --format "{{.Names}}"
```

No debe usarse el alias de `dockers.json`.

## Recursos permitidos

Solo se permiten recursos en porcentaje.

| Recurso    | Permitido | Motivo                                                                                                 |
| ---------- | --------: | ------------------------------------------------------------------------------------------------------ |
| `cpu`      |        Sí | Telegraf puede recoger porcentaje de CPU del contenedor.                                               |
| `ram`      |        Sí | Telegraf puede recoger porcentaje de memoria del contenedor.                                           |
| `disco`    |        No | No se permite porque no está garantizado que Telegraf guarde porcentaje de disco usado por contenedor. |
| `rx_bytes` |        No | Se eliminan alarmas de bytes de red.                                                                   |
| `tx_bytes` |        No | Se eliminan alarmas de bytes de red.                                                                   |

Si Telegraf no almacena una métrica de porcentaje para un recurso concreto, no se puede crear una alarma de ese recurso.

## Métricas internas esperadas

| `recurso` | Measurement esperado   | Tag de contenedor esperado  | Field usado     |
| --------- | ---------------------- | --------------------------- | --------------- |
| `cpu`     | `docker_container_cpu` | `container_name = objetivo` | `usage_percent` |
| `ram`     | `docker_container_mem` | `container_name = objetivo` | `usage_percent` |


## Campos obligatorios

| Campo         | Descripción                           |
| ------------- | ------------------------------------- |
| `nombre`      | Nombre descriptivo de la alarma.      |
| `tipo`        | Debe ser `metrica`.                   |
| `categoria`   | Debe ser `docker`.                    |
| `objetivo`    | Nombre real del contenedor, no alias. |
| `recurso`     | `cpu` o `ram`.                        |
| `operador`    | `>`, `<`, `>=` o `<=`.                |
| `umbral`      | Valor numérico de referencia.         |
| `responsable` | Correo que recibirá la notificación.  |

## Ejemplo: CPU de contenedor

```json
{
  "nombre": "CPU Contenedor API Crítica",
  "tipo": "metrica",
  "categoria": "docker",
  "objetivo": "api_backend",
  "recurso": "cpu",
  "operador": ">",
  "umbral": 85,
  "responsable": "admin@ejemplo.com"
}
```

## Ejemplo: RAM de contenedor

```json
{
  "nombre": "RAM Contenedor API Crítica",
  "tipo": "metrica",
  "categoria": "docker",
  "objetivo": "api_backend",
  "recurso": "ram",
  "operador": ">",
  "umbral": 80,
  "responsable": "admin@ejemplo.com"
}
```

---

# 5. Campo `operador`

El campo `operador` solo aplica a alarmas con:

```json
"tipo": "metrica"
```

Operadores permitidos:

| Operador | Dispara `CRITICAL` cuando...                 |
| -------- | -------------------------------------------- |
| `>`      | El valor supera el umbral.                   |
| `<`      | El valor cae por debajo del umbral.          |
| `>=`     | El valor iguala o supera el umbral.          |
| `<=`     | El valor iguala o cae por debajo del umbral. |

Ejemplo:

```json
{
  "operador": ">",
  "umbral": 90
}
```

Significa:

```text
CRITICAL si valor > 90
OK si valor <= 90
```

---

# 6. Comportamiento cuando no hay datos

Si una alarma no encuentra datos en InfluxDB para el recurso configurado, el estado resultante será:

```text
CRITICAL
```

Esto aplica a:

* estado Docker sin `status_code` disponible;
* métricas de servidor sin datos;
* métricas de Docker sin datos.

Por eso es importante comprobar primero en InfluxDB/Grafana que el recurso existe y que Telegraf está guardando la métrica esperada.

---

# 7. Identificadores correctos según el tipo de alarma

| Categoría  | Tipo      | Campo `objetivo` debe ser                                               |
| ---------- | --------- | ----------------------------------------------------------------------- |
| `servicio` | `estado`  | Nombre lógico del servicio.                                             |
| `docker`   | `estado`  | Alias definido en `dockers.json`.                                       |
| `docker`   | `metrica` | Nombre real del contenedor, el de `docker ps -a --format "{{.Names}}"`. |
| `servidor` | `metrica` | Host de Telegraf.                                                       |

---

# 8. Campos de contexto para servicios

Las alarmas de servicio pueden incluir campos opcionales que no cambian la evaluación de la alarma, pero ayudan a generar contexto cuando el servicio pasa a `CRITICAL`.

```json
{
  "container_name": "alias_docker",
  "maquina_virtual": "host_telegraf"
}
```

## `container_name`

Debe ser el alias registrado en dockers.json, no el nombre real del contenedor.

Se usa para buscar el alias correspondiente en `dockers.json` y consultar el último estado Docker.

## `maquina_virtual`

Debe coincidir con el tag `host` usado por Telegraf.

Se usa para consultar:

* CPU total usada;
* RAM usada;
* disco raíz usado.

---

# 9. Referencia rápida de campos

| Campo             | Obligatorio | Aplica a                                     | Descripción                                                                                |
| ----------------- | ----------: | -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `nombre`          |          Sí | Todas                                        | Nombre descriptivo de la alarma. Se usa para construir el identificador interno de alarma. |
| `tipo`            |          Sí | Todas                                        | `estado` o `metrica`.                                                                      |
| `categoria`       |          Sí | Todas                                        | `servicio`, `docker` o `servidor`.                                                         |
| `objetivo`        |          Sí | Todas                                        | Identificador del recurso. Su significado depende de `categoria` y `tipo`.                 |
| `responsable`     |          Sí | Todas                                        | Correo que recibirá las notificaciones.                                                    |
| `url`             |          Sí | `servicio` + `estado`                        | URL comprobada por HTTP GET.                                                               |
| `recurso`         |          Sí | `servidor` + `metrica`, `docker` + `metrica` | Recurso de porcentaje a comprobar.                                                         |
| `operador`        |          Sí | Todas las métricas                           | `>`, `<`, `>=` o `<=`.                                                                     |
| `umbral`          |          Sí | Todas las métricas                           | Valor numérico de referencia.                                                              |
| `container_name`  |          No | `servicio` + `estado`                        | Alias del contenedor asociado, usado solo para contexto.                             |
| `maquina_virtual` |          No | `servicio` + `estado`                        | Host asociado, usado solo para contexto.                                                   |

---

# 10. Combinaciones permitidas

| Categoría  | Tipo      | Permitido | Comentario                                            |
| ---------- | --------- | --------: | ----------------------------------------------------- |
| `servicio` | `estado`  |        Sí | Servicio HTTP comprobado directamente por el script.  |
| `servicio` | `metrica` |        No | No se permiten métricas de servicio.                  |
| `docker`   | `estado`  |        Sí | Estado leído desde `docker_gen`.                   |
| `docker`   | `metrica` |        Sí | Solo porcentajes de CPU y RAM si Telegraf los guarda. |
| `servidor` | `estado`  |        No | No se contempla estado binario de servidor.           |
| `servidor` | `metrica` |        Sí | Solo porcentajes de CPU, RAM y disco raíz.            |

---

# 11. Recursos permitidos por categoría

## Servidor

| `recurso` | Permitido |
| --------- | --------: |
| `cpu`     |        Sí |
| `ram`     |        Sí |
| `disco`   |        Sí |

## Docker

| `recurso` | Permitido |
| --------- | --------: |
| `cpu`     |        Sí |
| `ram`     |        Sí |
| `disco`   |        No |

---

# 12. Ejemplo completo de lista de alarmas

```json
[
  {
    "nombre": "Web Principal Down",
    "tipo": "estado",
    "categoria": "servicio",
    "objetivo": "web_corporativa",
    "url": "https://web-corporativa.ejemplo.com",
    "responsable": "admin@ejemplo.com",
    "container_name": "web_corp_nginx",
    "maquina_virtual": "servidor-prod"
  },
  {
    "nombre": "Contenedor MongoDB Down",
    "tipo": "estado",
    "categoria": "docker",
    "objetivo": "mongodb_container",
    "responsable": "admin@ejemplo.com"
  },
  {
    "nombre": "CPU Servidor Crítica",
    "tipo": "metrica",
    "categoria": "servidor",
    "objetivo": "servidor-prod",
    "recurso": "cpu",
    "operador": ">",
    "umbral": 90,
    "responsable": "admin@ejemplo.com"
  },
  {
    "nombre": "RAM Servidor Crítica",
    "tipo": "metrica",
    "categoria": "servidor",
    "objetivo": "servidor-prod",
    "recurso": "ram",
    "operador": ">=",
    "umbral": 95,
    "responsable": "admin@ejemplo.com"
  },
  {
    "nombre": "Disco Raíz Servidor Crítico",
    "tipo": "metrica",
    "categoria": "servidor",
    "objetivo": "servidor-prod",
    "recurso": "disco",
    "operador": ">",
    "umbral": 85,
    "responsable": "admin@ejemplo.com"
  },
  {
    "nombre": "CPU Contenedor API Crítica",
    "tipo": "metrica",
    "categoria": "docker",
    "objetivo": "api_backend",
    "recurso": "cpu",
    "operador": ">",
    "umbral": 85,
    "responsable": "admin@ejemplo.com"
  },
  {
    "nombre": "RAM Contenedor API Crítica",
    "tipo": "metrica",
    "categoria": "docker",
    "objetivo": "api_backend",
    "recurso": "ram",
    "operador": ">",
    "umbral": 80,
    "responsable": "admin@ejemplo.com"
  }
]
```

---

# 13. Casos no permitidos

Los siguientes ejemplos deben rechazarse en la validación.

## Servicio con métrica

```json
{
  "nombre": "CPU Servicio Web",
  "tipo": "metrica",
  "categoria": "servicio",
  "objetivo": "web_corporativa",
  "recurso": "cpu",
  "operador": ">",
  "umbral": 80,
  "responsable": "admin@ejemplo.com"
}
```

Motivo:

```text
Los servicios solo admiten tipo estado.
```

## Docker con disco

```json
{
  "nombre": "Disco Contenedor API",
  "tipo": "metrica",
  "categoria": "docker",
  "objetivo": "api_backend",
  "recurso": "disco",
  "operador": ">",
  "umbral": 80,
  "responsable": "admin@ejemplo.com"
}
```

Motivo:

```text
No se permiten alarmas de disco por contenedor porque no está garantizado que Telegraf guarde ese porcentaje.
```

## Docker con bytes de red

```json
{
  "nombre": "RX Bytes Contenedor API",
  "tipo": "metrica",
  "categoria": "docker",
  "objetivo": "api_backend",
  "recurso": "rx_bytes",
  "operador": ">",
  "umbral": 1000000,
  "responsable": "admin@ejemplo.com"
}
```

Motivo:

```text
Se eliminan las alarmas de bytes de red.
```

## Servidor con métrica manual antigua

```json
{
  "nombre": "RAM Servidor Crítica",
  "tipo": "metrica",
  "categoria": "servidor",
  "objetivo": "servidor-prod",
  "metrica": "used_percent",
  "operador": ">",
  "umbral": 90,
  "responsable": "admin@ejemplo.com"
}
```

Motivo:

```text
Las alarmas de recursos deben usar recurso = cpu | ram | disco.
```

---

# 14. Reglas de validación recomendadas

Una alarma será válida solo si cumple estas reglas:

1. Tiene los campos comunes obligatorios:

   * `nombre`
   * `tipo`
   * `categoria`
   * `objetivo`
   * `responsable`

2. `tipo` solo puede ser:

   * `estado`
   * `metrica`

3. `categoria` solo puede ser:

   * `servicio`
   * `docker`
   * `servidor`

4. Si `categoria = servicio`:

   * `tipo` debe ser `estado`;
   * debe existir `url`.

5. Si `categoria = docker` y `tipo = estado`:

   * `objetivo` debe ser el alias de `dockers.json`;
   * solo `status_code = 200` es `OK`.

6. Si `categoria = docker` y `tipo = metrica`:

   * `objetivo` debe ser el nombre real del contenedor;
   * `recurso` solo puede ser `cpu` o `ram`.

7. Si `categoria = servidor` y `tipo = metrica`:

   * `objetivo` debe ser el host de Telegraf;
   * `recurso` solo puede ser `cpu`, `ram` o `disco`.

8. Si `tipo = metrica`:

   * debe existir `recurso`;
   * debe existir `operador`;
   * debe existir `umbral`;
   * `umbral` debe ser numérico;
   * `operador` debe ser `>`, `<`, `>=` o `<=`.

9. No se permiten:

   * métricas de red;
   * métricas en bytes;
   * métricas Docker de disco;
   * métricas de servicio;
   * estado de servidor.

---

# 15. Resumen final

| Caso             | Origen de datos                                  | Identificador usado en `objetivo` | Recursos permitidos   |
| ---------------- | ------------------------------------------------ | --------------------------------- | --------------------- |
| Servicio estado  | HTTP GET directo del script                      | Nombre lógico del servicio        | No aplica             |
| Docker estado    | `docker_gen` generado por `monitor_docker.py` | Alias de `dockers.json`           | No aplica             |
| Servidor métrica | Telegraf `cpu`, `mem`, `disk`                    | Host de Telegraf                  | `cpu`, `ram`, `disco` |
| Docker métrica   | Telegraf Docker                                  | Nombre real del contenedor        | `cpu`, `ram`          |

