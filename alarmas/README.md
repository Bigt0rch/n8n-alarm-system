# Configurar los workflows de alarmas
A continuación veremos como configurar el sistema de alarmas en n8n, si tiene alguna duda sobre alguno de los ficheros de configuración aquí mencionados, por favor lea [Ficheros necesarios para que todos los sistemas funcionen](../README.md#Ficheros-necesarios-para-que-todos-los-sistemas-funcionen).

## Paso previo, desactivar el registro de estados de servicios mediante Telegraf
En el proyecto [monitorizacion-grafana-influx-telegraf](https://github.com/luisGarciiaa/monitorizacion-grafana-influx-telegraf) de [Luis Garcia Capilla](https://github.com/luisGarciiaa) Telegraf recoge el código HTTP que devuelven las alarmas y lo almacena en influx. Sin embargo, tiene un problema con los timeout que provoca que nunca se lleguen a recoger todos los codigos de todos los servicios registrados. Es por esto que esa funcionalidad ha sido migrada a este proyecto.

Para evitar solapamientos entre la información de este proyecto y el de [Luis Garcia Capilla](https://github.com/luisGarciiaa), debemos desactivar esta función mencionada del proyecto [monitorizacion-grafana-influx-telegraf](https://github.com/luisGarciiaa/monitorizacion-grafana-influx-telegraf) de [Luis Garcia Capilla](https://github.com/luisGarciiaa). Para ello, iremos al fichero [telegraf.conf](https://github.com/luisGarciiaa/monitorizacion-grafana-influx-telegraf/blob/main/telegraf.conf) y buscaremos el siguiente fragmento:

```
[[inputs.exec]]
  commands = ["bash -c \"source /home/.../myenv/bin/activate && python3 /home/.../monitor_servicio.py\""]  
  interval = "30s"
  timeout = "10s"
  data_format = "influx"
  name_override = "servicio_gen"
  [inputs.exec.tags]
    url = "servicio_general"
```
**Debemos comentar este fragmento y dejalo de la siguiente manera**:
```
# [[inputs.exec]]
#   commands = ["bash -c \"source /home/.../myenv/bin/activate && python3 /home/.../monitor_servicio.py\""]  
#   interval = "30s"
#   timeout = "10s"
#   data_format = "influx"
#   name_override = "servicio_gen"
#   [inputs.exec.tags]
#     url = "servicio_general"
```

Después debemos relanzar telegraf en nuestro sistema para que estos cambios sean efectivos.


## Archivo de configuración configAlarms.json
A continuación veremos los cambios que se deben realizar al archivo configAlarms.json que se le debe haber proporcionado. Más información en [README.md](../README.md#Ficheros-necesarios-para-que-todos-los-sistemas-funcionen).
  - **`INFLUX_CONFIG`**: este objeto contiene varios campos necesarios pare que el workflow pueda relizar su conexion con InfluxDB. Asegurese de rellenar los campos correctamente o el sistema fallará por completo.
  - **`MAIL_CONFIG`**: este objeto contiene varios campos necesarios pare que el workflow pueda enviar los correos necesarios de forma correcta. Asegurese de rellenar los campos correctamente o el sistema fallará por completo.
  - **`ALARMS_FILE`**: Modifique esta variable por el path de tu archivo alarms.json siempre dentro de la carpeta `n8n-files`.
  - **`DOCKERS_FILE`**: Modifique esta variable por el path de tu archivo dockers.json siempre dentro de la carpeta `n8n-files`.
  - **`BATCH_SIZE`**: dado que el workflow está preparado para soportar grandes cantidades de alarmas y procesarlas en paralelo, con esta variable determinas el número de alarmas que deseas que se procesen en paralelo. Por ejemplo, si el valor es 150, cada "worker" procesará 150 alarmas o menos.
  - **`MONITOR_SCRIPT`**: Modifique esta variable por el path de tu archivo [alarmMonitorDBn8n.py](alarmMonitorDBn8n.py), siempre dentro de la carpeta `n8n-files`.
  - **`NOTIFIER_SCRIPT`**: Modifique esta variable por el path de tu archivo [alarmNotifiern8n.py](alarmNotifiern8n.py), siempre dentro de la carpeta `n8n-files`.
  - **`LOG_DIRECTORY`**: Modifique esta variable por el path de la carpeta donde quieres que se depositen los logs de este workflow.

En caso de que requiera modificar alguno de estos valores, no es necesario que reinicie n8n, con guardar los cambios será suficiente para que las póximas ejecuciones programadas usen los nuevos valores.

## Workflow principal alarmas
A continuación veremos como importar y configurar el workflow principal, que se encarga de cargar todas las alarmas, dividirlas por lotes y enviarselos a workflows secundarios.
 - Importe el workflow [Workflow principal alarmas.json](Workflow%20principal%20alarmas.json).
 - Recuerde colocar el fichero configAlarms.json en el directorio `n8n-files` o algún subdirectorio.
 - En el nodo `Abrir config.json` del workflow modifique el path del archivo que se abre por donde este ubicado su fichero configAlarms.json.
 - Sustituya el trigger del workflow por otro un trigger `On a schedule` y cionfigurelo para que este workflow se ejecute con la frecuencia que usted desee. Recomendamos ejecutarlo con una frecuencia de  entre 10-15 mins.


## Workflow secundario
Para configurar el workflow secundario que se encarga de procesar los lotes de alarmas que le envia el workflow principal a través de un webhook, sigua los siguientes pasos:
 - Importe el workflow [Workflow secundario (webhook).json](Workflow%20secundario%20(webhook).json).
 - Recuerde colocar el fichero configAlarms.json en el directorio `n8n-files` o algún subdirectorio.
 - En el nodo `Abrir config.json` del workflow modifique el path del archivo que se abre por donde este ubicado tu fichero a configAlarms.json.
 - Finalmente, encontrará un botón que dice **Publish** en la parte superior de tu pantalla, esto es necesario para que el endopint del workflow quede expuesto. Presionalo para publicarlo.
 - Asegurese de que **la URL del nodo `HTTP Request` del [Workflow principal alarmas.json](Workflow%20principal%20alarmas.json) conincide con la URL de producción del nodo `Webhook` del [Workflow secundario (webhook).json](Workflow%20secundario%20(webhook).json)**.


## Requirements
Para poder ejecutar los scripts de los workflows, es necesario instalar **fuera de cualquier entorno virtual** mediante el siguiente comando las librerias del [requirements.txt](requirements.txt)

```
pip install -r requirements.txt
```