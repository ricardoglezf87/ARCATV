# ARCATV

Aplicación web en Python para llevar el seguimiento de series de TV: guarda tus series favoritas, marca episodios vistos y consulta los próximos capítulos conocidos.

## Fuente de datos

ARCATV usa [TMDb](https://developer.themoviedb.org/reference/getting-started) como fuente principal cuando `TMDB_API_KEY` o `TMDB_BEARER_TOKEN` están configurados. TMDb devuelve fichas, búsquedas, temporadas, episodios, tendencias y recomendaciones con localización `es-ES`, así que esos textos no pasan por traducción externa.

[TVmaze](https://www.tvmaze.com/api) queda como repositorio de respaldo: funciona sin clave y se usa cuando TMDb no está configurado o no aporta candidatos suficientes. Los datos se guardan en caché local durante unas horas para que la experiencia sea rápida y respetuosa con los servicios.

Cuando TVmaze tiene un alias para España o países hispanohablantes, ARCATV usa ese nombre en la interfaz. Si no existe alias en español, mantiene el título original. Las descripciones que vengan de TVmaze se intentan traducir al español con MyMemory y se cachean localmente; si la traducción no está disponible, se muestra el texto original.

## Puesta en marcha

En Windows puedes usar doble clic sobre `iniciar_arcatv.bat` desde la raíz del proyecto. El script crea el entorno local si falta, instala dependencias y abre la web.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app arcatv run --debug
```

Después abre `http://127.0.0.1:5000`.

Para activar TMDb, crea una clave gratuita en TMDb y define una de estas variables antes de abrir la app:

```powershell
$env:TMDB_API_KEY="tu_clave_tmdb"
.\iniciar_arcatv.bat
```

También puedes usar `TMDB_BEARER_TOKEN` si prefieres el token de lectura de API.

Otra opción cómoda es crear un archivo `.env` en la raíz con una de estas líneas:

```text
TMDB_API_KEY=tu_clave_tmdb
```

Si Windows o tu red bloquean la validación del certificado de TMDb, la app intenta usar el almacén de certificados del sistema con `truststore`. Como último recurso local puedes añadir `TMDB_VERIFY_SSL=false` al `.env`; funciona como el `verify=False` de `requests`, pero es menos seguro y conviene usarlo solo temporalmente.

## Funciones incluidas

- Buscar series primero en TMDb y usar TVmaze como respaldo.
- Filtrar búsquedas por género, incluyendo Telenovela cuando la fuente lo marca como `Soap` o la serie encaja por nombre/cadena/sinopsis.
- Ver póster, sinopsis, cadena, géneros y estado de cada serie.
- Marcar episodios vistos uno a uno, hasta un punto concreto o todos los emitidos.
- Ocultar capítulos vistos por defecto y mostrarlos cuando lo necesites.
- Abrir una ventana emergente con más información al seleccionar un capítulo.
- Consultar recomendaciones basadas en tus series vistas o completadas, filtrables por género y año, con orden reciente por defecto u orden por puntuación.
- Ver recomendaciones agrupadas tipo "Para ti", "Tops del momento", "Porque viste..." y tops por género o cadena/plataforma cuando haya datos suficientes.
- Añadir directamente una serie desde cada recomendación.
- Ver progreso por serie y el siguiente episodio pendiente.
- Consultar una vista ordenada de próximos capítulos de tus series guardadas.
- Ordenar el panel para priorizar series con pendientes según tu último episodio marcado como visto.
- Mostrar numeración absoluta en series con temporadas anuales, útil para casos como anime largo.
- Ocultar por defecto las series que ya tienes completamente vistas y actualizar datos manualmente solo para series en emisión o para toda la biblioteca.
- Guardar todo el progreso en una base SQLite local dentro de `instance/`.
