# ARCATV

Aplicación web en Python para llevar el seguimiento de series de TV, películas y mangas: guarda tus series favoritas, marca episodios o películas vistas, registra el capítulo de manga por el que vas y consulta próximos capítulos, reparto, autores y recomendaciones.

## Fuente de datos

ARCATV usa [TMDb](https://developer.themoviedb.org/reference/getting-started) para series y películas, ComicK como fuente principal de búsquedas, fichas, portadas y capítulos de manga, [MangaDex](https://api.mangadex.org/docs/) como respaldo de mangas ya guardados, y AniList como apoyo para recomendaciones de manga. Las búsquedas, fichas, temporadas, episodios, películas, reparto y recomendaciones audiovisuales se solicitan a TMDb con localización `es-ES`, sin servicios de traducción externos. Los capítulos de manga se solicitan priorizando español e inglés. Los resultados se guardan unas horas en caché local para que la experiencia sea rápida y respetuosa con las APIs.

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

La sección de mangas funciona con ComicK y MangaDex sin clave. Si quieres ocultarla temporalmente, puedes definir `COMICK_ENABLED=false` y `MANGADEX_ENABLED=false`.

Otra opción cómoda es crear un archivo `.env` en la raíz con una de estas líneas:

```text
TMDB_API_KEY=tu_clave_tmdb
```

Si Windows o tu red bloquean la validación del certificado de TMDb, la app intenta usar el almacén de certificados del sistema con `truststore`. Como último recurso local puedes añadir `TMDB_VERIFY_SSL=false` al `.env`; funciona como el `verify=False` de `requests`, pero es menos seguro y conviene usarlo solo temporalmente.

## Funciones incluidas

- Buscar series en el catálogo de TMDb en español.
- Buscar películas en el catálogo de TMDb y guardarlas como pendientes o vistas.
- Buscar mangas y guardarlos en tu biblioteca.
- Filtrar búsquedas por género, incluyendo Telenovela cuando la fuente lo marca como `Soap` o la serie encaja por nombre/cadena/sinopsis.
- Ver póster, sinopsis, cadena, géneros y estado de cada serie.
- Ver póster, sinopsis, estudio, géneros, duración y estado de cada película.
- Ver portada, sinopsis, géneros, capítulos disponibles, estado y autores de cada manga.
- Marcar episodios vistos uno a uno, hasta un punto concreto o todos los emitidos.
- Marcar películas como vistas o pendientes desde el listado, la ficha o la búsqueda.
- Marcar hasta el capítulo de manga por el que vas, por ejemplo `1035`, y ocultar capítulos ya leídos.
- Ocultar capítulos vistos por defecto y mostrarlos cuando lo necesites.
- Abrir una ventana emergente con más información al seleccionar un capítulo.
- Consultar recomendaciones basadas en tus series vistas o completadas, filtrables por género, año, varias series de origen y actor, con orden por puntuación de forma predeterminada.
- Consultar recomendaciones de películas basadas en tus películas vistas, actores y películas similares de TMDb.
- Consultar recomendaciones de mangas basadas en tus mangas leídos, autores y sugerencias de AniList.
- Rechazar recomendaciones para ocultarlas y volver a incluirlas o restaurarlas cuando lo necesites.
- Ver recomendaciones agrupadas tipo "Para ti", "Porque viste...", por género y por actores que ya aparecen en tus series.
- Consultar el reparto de una serie o película, abrir la ficha de cada actor y añadir otras series o películas en las que haya trabajado.
- Consultar autores de un manga, abrir la ficha de cada autor y añadir otros mangas relacionados.
- Añadir directamente una serie desde cada recomendación.
- Añadir directamente una película desde cada recomendación de cine.
- Añadir directamente un manga desde cada recomendación de manga.
- Ver progreso por serie y el siguiente episodio pendiente.
- Consultar una vista ordenada de próximos capítulos de tus series guardadas.
- Ordenar el panel para priorizar series con pendientes según tu último episodio marcado como visto.
- Mostrar numeración absoluta en series con temporadas anuales, útil para casos como anime largo.
- Ocultar por defecto las series que ya tienes completamente vistas y actualizar datos manualmente solo para series en emisión o para toda la biblioteca.
- Guardar todo el progreso en una base SQLite local dentro de `instance/`.
