const episodeDialog = document.querySelector("#episode-dialog");
const episodeDialogImage = document.querySelector("#episode-dialog-image");
const episodeDialogStatus = document.querySelector("#episode-dialog-status");
const episodeDialogCode = document.querySelector("#episode-dialog-code");
const episodeDialogTitle = document.querySelector("#episode-dialog-title");
const episodeDialogMeta = document.querySelector("#episode-dialog-meta");
const episodeDialogSummary = document.querySelector("#episode-dialog-summary");

document.querySelectorAll(".episode-open").forEach((button) => {
    button.addEventListener("click", async () => {
        if (!episodeDialog) {
            return;
        }

        const fallbackEpisode = JSON.parse(button.dataset.episode || "{}");
        fillEpisodeDialog({ ...fallbackEpisode, summary: "Cargando descripción..." });
        episodeDialog.showModal();

        if (!button.dataset.detailUrl) {
            fillEpisodeDialog(fallbackEpisode);
            return;
        }

        try {
            const response = await fetch(button.dataset.detailUrl, {
                headers: { Accept: "application/json" },
            });
            if (!response.ok) {
                throw new Error("No se pudo cargar el episodio.");
            }
            fillEpisodeDialog(await response.json());
        } catch {
            fillEpisodeDialog(fallbackEpisode);
        }
    });
});

function fillEpisodeDialog(episode) {
    episodeDialogStatus.textContent = episode.status || "";
    episodeDialogCode.textContent = episode.code || "";
    episodeDialogTitle.textContent = episode.title || "Sin título";
    episodeDialogMeta.textContent = [episode.show, episode.air, episode.runtime]
            .filter(Boolean)
            .join(" · ");
    episodeDialogSummary.textContent = episode.summary || "Sin sinopsis disponible.";

    if (episode.image) {
        episodeDialogImage.src = episode.image;
        episodeDialogImage.alt = `Imagen de ${episode.title || "episodio"}`;
        episodeDialogImage.hidden = false;
    } else {
        episodeDialogImage.removeAttribute("src");
        episodeDialogImage.alt = "";
        episodeDialogImage.hidden = true;
    }
}

episodeDialog?.addEventListener("click", (event) => {
    if (event.target === episodeDialog) {
        episodeDialog.close();
    }
});
