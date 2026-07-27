# ARCATV

Aplicación web en Python para llevar el seguimiento de series de TV: guarda tus series favoritas, marca episodios vistos y consulta los próximos capítulos conocidos.

## Fuente de datos

ARCATV usa la API pública de [TVmaze](https://www.tvmaze.com/api), que ofrece búsquedas, fichas de series y listados de episodios en JSON sin necesidad de clave. Los datos se guardan en caché local durante unas horas para que la experiencia sea rápida y respetuosa con el servicio.

## Puesta en marcha

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app arcatv run --debug
```

Después abre `http://127.0.0.1:5000`.

## Funciones incluidas

- Buscar series en TVmaze y añadirlas a tu lista.
- Ver póster, sinopsis, cadena, géneros y estado de cada serie.
- Marcar episodios vistos uno a uno, hasta un punto concreto o todos los emitidos.
- Ver progreso por serie y el siguiente episodio pendiente.
- Consultar una vista ordenada de próximos capítulos de tus series guardadas.
- Guardar todo el progreso en una base SQLite local dentro de `instance/`.
