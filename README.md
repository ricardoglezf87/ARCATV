# ARCATV

Aplicación web en Python para llevar el seguimiento de series de TV: guarda tus series favoritas, marca episodios vistos y consulta los próximos capítulos conocidos.

## Fuente de datos

ARCATV usa la API pública de [TVmaze](https://www.tvmaze.com/api), que ofrece búsquedas, fichas de series, alias por país, puntuaciones, géneros y listados de episodios en JSON sin necesidad de clave. Los datos se guardan en caché local durante unas horas para que la experiencia sea rápida y respetuosa con el servicio.

Cuando TVmaze tiene un alias para España o países hispanohablantes, ARCATV usa ese nombre en la interfaz. Si no existe alias en español, mantiene el título original. Las descripciones de series y episodios se intentan traducir al español con MyMemory y se cachean localmente; si la traducción no está disponible, se muestra el texto original.

## Puesta en marcha

En Windows puedes usar doble clic sobre `iniciar_arcatv.bat` desde la raíz del proyecto. El script crea el entorno local si falta, instala dependencias y abre la web.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app arcatv run --debug
```

Después abre `http://127.0.0.1:5000`.

## Funciones incluidas

- Buscar series en TVmaze y añadirlas a tu lista.
- Filtrar búsquedas por género, incluyendo Telenovela cuando TVmaze lo marca como `Soap` o la serie encaja por nombre/cadena/sinopsis.
- Ver póster, sinopsis, cadena, géneros y estado de cada serie.
- Marcar episodios vistos uno a uno, hasta un punto concreto o todos los emitidos.
- Ocultar capítulos vistos por defecto y mostrarlos cuando lo necesites.
- Abrir una ventana emergente con más información al seleccionar un capítulo.
- Consultar recomendaciones basadas en tus series vistas o completadas, filtrables por género y año, con orden reciente por defecto u orden por puntuación.
- Ver progreso por serie y el siguiente episodio pendiente.
- Consultar una vista ordenada de próximos capítulos de tus series guardadas.
- Ordenar el panel para priorizar series con pendientes según tu último episodio marcado como visto.
- Mostrar numeración absoluta en series con temporadas anuales, útil para casos como anime largo.
- Ocultar series finalizadas por defecto en el inicio y actualizar datos manualmente solo para series en emisión o para toda la biblioteca.
- Guardar todo el progreso en una base SQLite local dentro de `instance/`.
