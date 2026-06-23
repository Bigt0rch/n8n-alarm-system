# Configurar los workflows de informes
A continuación veremos como configurar el sistema de infomes en n8n, si tiene alguna duda sobre alguno de los ficheros de configuración aquí mencionados, por favor lea [Ficheros necesarios para que todos los sistemas funcionen](../README.md#Ficheros-necesarios-para-que-todos-los-sistemas-funcionen).

## Archivo de configuración configReports.json
A continuación veremos los cambios que se deben realizar al archivo configReports.json que se le debe haber proporcionado. Más información en [README.md](../README.md#Ficheros-necesarios-para-que-todos-los-sistemas-funcionen).
  - **`INFLUX_CONFIG`**: este objeto contiene varios campos necesarios pare que el workflow pueda relizar su conexion con InfluxDB. Asegurese de rellenar los campos correctamente o el sistema fallará por completo.
  - **`MAIL_CONFIG`**: este objeto contiene varios campos necesarios pare que el workflow pueda enviar los correos necesarios de forma correcta. Asegurese de rellenar los campos correctamente o el sistema fallará por completo.
  - **`ALARMS_FILE`**: Modifique esta variable por el path de tu archivo alarms.json siempre dentro de la carpeta `n8n-files`.
  - **`DOCKERS_FILE`**: Modifique esta variable por el path de tu archivo dockers.json siempre dentro de la carpeta `n8n-files`.
  - **`SEND_REPORTS_TO`**: Modifique esta variable por la dirección de correo electronico que deba recibir los informes anuales y mensuales.
  - **`DAILY_REPORTS_SCRIPT`**: Modifique esta variable por el path de tu archivo [reportDaily.py](reportDaily.py), siempre dentro de la carpeta `n8n-files`.
  - **`MONTHLY_REPORTS_SCRIPT`**: Modifique esta variable por el path de tu archivo [reportMonthly.py](reportMonthly.py), siempre dentro de la carpeta `n8n-files`.
  - **`YEARLY_REPORTS_SCRIPT`**: Modifique esta variable por el path de tu archivo [reportAnnual.py](reportAnnual.py), siempre dentro de la carpeta `n8n-files`.
  - **`OUTPUT_DIR`**: Modifique esta variable por el path de la carpeta donde desea que se depositen los PDF generados por este sistema, aunque tambén se le enviaran por correo a las personas pertinentes.
  - **`LOG_DIRECTORY`**: Modifique esta variable por el path de la carpeta donde desea que se depositen los logs de este workflow.
  - **`UMBRAL_COLOR_UPTIME`**: esta variable sirve para definir un umbral entre el 0 y el 100. Por debajo del cual es sistema considerará que uno de sus servicios o dockers se encuentra en estado critico al no llegar al porcentaje de uptiome definido por esta variable-

En caso de que requiera modificar alguno de estos valores, no es necesario que reinicie n8n, con guardar los cambios será suficiente para que las póximas ejecuciones programadas usen los nuevos valores.

## Workflow Informes anuales
A continuación veremos como importar y configurar el workflow que genera los informes anuales.
 - Importe el workflow [Workflow Informes anuales.json](Workflow%20Informes%20anuales.json).
 - Recuerde colocar el fichero configReports.json en el directorio `n8n-files` o algún subdirectorio.
 - En el nodo `Abrir config.json` del workflow modifique el path del archivo que se abre por donde este ubicado su fichero configReports.json.
 - Sustituya el trigger del workflow por otro un trigger `On a schedule` y cionfigurelo para que este workflow se ejecute con la frecuencia que usted desee. Recomendamos ejecutarlo todos los 1 de enero de cada añao a las 6AM.

## Workflow Informes mensuales
A continuación veremos como importar y configurar el workflow que genera los informes mensuales.
 - Importe el workflow [Workflow Informes mensuales.json](Workflow%20Informes%20mensuales.json).
 - Recuerde colocar el fichero configReports.json en el directorio `n8n-files` o algún subdirectorio.
 - En el nodo `Abrir config.json` del workflow modifique el path del archivo que se abre por donde este ubicado su fichero configReports.json.
 - Sustituya el trigger del workflow por otro un trigger `On a schedule` y cionfigurelo para que este workflow se ejecute con la frecuencia que usted desee. Recomendamos ejecutarlo todos los día 1 de cada mes a las 6AM.

## Workflow Informes diarios
A continuación veremos como importar y configurar el workflow que genera los informes mensuales. **Por vavor, tenga en cuenta que este workflow ignora el valor de la variable `SEND_REPORTS_TO` y en su lugar envía información de cada servicio, docker y maquina virtual a sus respectivos responsables directos**
 - Importe el workflow [Workflow Informes diarios.json](Workflow%20Informes%20diarios.json).
 - Recuerde colocar el fichero configReports.json en el directorio `n8n-files` o algún subdirectorio.
 - En el nodo `Abrir config.json` del workflow modifique el path del archivo que se abre por donde este ubicado su fichero configReports.json.
 - Sustituya el trigger del workflow por otro un trigger `On a schedule` y cionfigurelo para que este workflow se ejecute con la frecuencia que usted desee. Recomendamos ejecutarlo todos los días a las 6AM.

## Requirements
 Para poder ejecutar los scripts de los workflows, es necesario instalar **fuera de cualquier entorno virtual** mediante el siguiente comando las librerias del [requirements.txt](requirements.txt)
 
 ```
 pip install -r requirements.txt
 ```