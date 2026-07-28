const episodeDialog = document.querySelector("#episode-dialog");
const episodeDialogImage = document.querySelector("#episode-dialog-image");
const episodeDialogStatus = document.querySelector("#episode-dialog-status");
const episodeDialogCode = document.querySelector("#episode-dialog-code");
const episodeDialogTitle = document.querySelector("#episode-dialog-title");
const episodeDialogMeta = document.querySelector("#episode-dialog-meta");
const episodeDialogSummary = document.querySelector("#episode-dialog-summary");

document.querySelectorAll(".episode-open").forEach((button) => {
    button.addEventListener("click", () => {
        if (!episodeDialog) {
            return;
        }

        const episode = JSON.parse(button.dataset.episode || "{}");
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

        episodeDialog.showModal();
    });
});

episodeDialog?.addEventListener("click", (event) => {
    if (event.target === episodeDialog) {
        episodeDialog.close();
    }
});
