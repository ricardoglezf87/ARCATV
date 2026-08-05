import base64
import io
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests


class MangaDownloadError(RuntimeError):
    pass


class MissingMangaDownloadDependency(MangaDownloadError):
    pass


@dataclass
class MangaDownloadResult:
    panel_count: int
    page_count: int
    strategy: str


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def download_manga_chapter(url, chapter_dir, chrome_version=None):
    chapter_dir = Path(chapter_dir)
    chapter_dir.mkdir(parents=True, exist_ok=True)

    errors = []
    for strategy in (_download_scroll_reader, _download_page_by_page_reader):
        clear_chapter_directory(chapter_dir)
        try:
            result = strategy(url, chapter_dir, chrome_version=chrome_version)
        except MissingMangaDownloadDependency:
            raise
        except MangaDownloadError as exc:
            errors.append(str(exc))
            continue
        if result.panel_count:
            return result
        errors.append(f"{result.strategy} no encontro imagenes.")

    raise MangaDownloadError(" / ".join(error for error in errors if error) or "No se pudo descargar el capitulo.")


def clear_chapter_directory(chapter_dir):
    chapter_dir = Path(chapter_dir)
    if not chapter_dir.exists():
        return
    for item in chapter_dir.iterdir():
        if item.is_dir():
            clear_chapter_directory(item)
            item.rmdir()
        else:
            item.unlink()


def chapter_images(chapter_dir):
    chapter_dir = Path(chapter_dir)
    if not chapter_dir.exists():
        return []
    return sorted(
        image
        for image in chapter_dir.iterdir()
        if image.is_file()
        and image.suffix.casefold() in IMAGE_EXTENSIONS
        and re.fullmatch(r"\d{3,}", image.stem)
    )


def read_vignette_map(chapter_dir):
    config_path = Path(chapter_dir) / "config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_browser_dependencies():
    missing = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        missing.append("beautifulsoup4")

    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")

    try:
        import undetected_chromedriver as uc
    except ImportError as exc:
        missing.append("undetected-chromedriver")
        if "distutils" in str(exc):
            missing.append("setuptools")

    if missing:
        packages = ", ".join(dict.fromkeys(missing))
        raise MissingMangaDownloadDependency(
            f"Faltan dependencias para descargar capitulos: {packages}."
        )

    try:
        import cv2
        import numpy as np
    except ImportError:
        cv2 = None
        np = None

    return BeautifulSoup, Image, uc, cv2, np


def _new_driver(uc, chrome_version=None, performance_logs=False):
    options = uc.ChromeOptions()
    if performance_logs:
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    kwargs = {"options": options}
    resolved_version = _coerce_chrome_major_version(chrome_version) or _detect_local_chrome_major_version()
    if resolved_version:
        kwargs["version_main"] = resolved_version

    try:
        return uc.Chrome(**kwargs)
    except Exception as exc:
        browser_version = _chrome_version_from_error(exc)
        if browser_version and browser_version != resolved_version:
            kwargs["version_main"] = browser_version
            return uc.Chrome(**kwargs)
        raise


def _coerce_chrome_major_version(version):
    if not version:
        return None
    match = re.search(r"\d+", str(version))
    if not match:
        return None
    return int(match.group(0))


def _chrome_version_from_error(exc):
    match = re.search(r"Current browser version is\s+(\d+)", str(exc), flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _detect_local_chrome_major_version():
    for version in _windows_chrome_versions():
        major = _coerce_chrome_major_version(version)
        if major:
            return major

    commands = _chrome_version_commands()
    seen = set()
    for command in commands:
        key = str(command).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            completed = subprocess.run(
                [str(command), "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        version = completed.stdout or completed.stderr
        major = _coerce_chrome_major_version(version)
        if major:
            return major
    return None


def _windows_chrome_versions():
    try:
        import winreg
    except ImportError:
        return []

    registry_paths = (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
    )
    versions = []
    for hive, key_path in registry_paths:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                version, _ = winreg.QueryValueEx(key, "version")
        except OSError:
            continue
        versions.append(version)
    return versions


def _chrome_version_commands():
    commands = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base_path = os.environ.get(env_name)
        if not base_path:
            continue
        chrome_path = Path(base_path) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if chrome_path.exists():
            commands.append(chrome_path)
    commands.extend(("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))
    return commands


def _download_scroll_reader(url, chapter_dir, chrome_version=None):
    BeautifulSoup, Image, uc, cv2, np = _load_browser_dependencies()
    pages_dir = chapter_dir / "paginas_completas"
    pages_dir.mkdir(parents=True, exist_ok=True)

    try:
        driver = _new_driver(uc, chrome_version=chrome_version, performance_logs=True)
    except Exception as exc:
        raise MangaDownloadError(f"No se pudo abrir Chrome para descargar el capitulo: {exc}") from exc

    try:
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        driver.get(url)
        time.sleep(5)
        for index in range(1, 7):
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {index} / 6);")
            time.sleep(0.8)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        image_urls = _reader_image_urls(soup, url)
        if not image_urls:
            raise MangaDownloadError("El lector de pagina larga no encontro imagenes.")

        network_cache = _browser_image_cache(driver, image_urls)
        vignette_map = {}
        vignette_counter = 1
        page_count = 0
        session = requests.Session()

        for page_index, image_url in enumerate(image_urls, start=1):
            image_data = _image_bytes_from_cache_or_url(network_cache, image_url, url, session)
            if not image_data:
                continue
            full_page = Image.open(io.BytesIO(image_data)).convert("RGB")
            vignette_counter = _save_page_and_panels(
                full_page,
                page_index,
                chapter_dir,
                pages_dir,
                vignette_counter,
                vignette_map,
                cv2,
                np,
            )
            page_count += 1

        _write_config(chapter_dir, vignette_map)
        return MangaDownloadResult(
            panel_count=vignette_counter - 1,
            page_count=page_count,
            strategy="lector de pagina larga",
        )
    except MangaDownloadError:
        raise
    except Exception as exc:
        raise MangaDownloadError(f"Fallo el lector de pagina larga: {exc}") from exc
    finally:
        driver.quit()


def _download_page_by_page_reader(url, chapter_dir, chrome_version=None):
    BeautifulSoup, Image, uc, cv2, np = _load_browser_dependencies()
    pages_dir = chapter_dir / "paginas_completas"
    pages_dir.mkdir(parents=True, exist_ok=True)

    base_url = re.sub(r"/p\d+/?$", "", url.rstrip("/"))
    first_page_url = f"{base_url}/p1"
    try:
        driver = _new_driver(uc, chrome_version=chrome_version)
    except Exception as exc:
        raise MangaDownloadError(f"No se pudo abrir Chrome para descargar el capitulo: {exc}") from exc

    try:
        driver.get(first_page_url)
        time.sleep(4)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        page_select = soup.find("select", id="nums")
        total_pages = len(page_select.find_all("option")) if page_select else 1
        if total_pages < 1:
            raise MangaDownloadError("El lector pagina a pagina no detecto paginas.")

        vignette_map = {}
        vignette_counter = 1
        page_count = 0

        for page_num in range(1, total_pages + 1):
            driver.get(f"{base_url}/p{page_num}")
            time.sleep(1.2)
            image_data = _canvas_image_bytes(driver)
            if not image_data:
                image_data = _reader_current_image_bytes(driver, f"{base_url}/p{page_num}")
            if not image_data:
                continue

            full_page = Image.open(io.BytesIO(image_data)).convert("RGB")
            vignette_counter = _save_page_and_panels(
                full_page,
                page_num,
                chapter_dir,
                pages_dir,
                vignette_counter,
                vignette_map,
                cv2,
                np,
            )
            page_count += 1

        _write_config(chapter_dir, vignette_map)
        return MangaDownloadResult(
            panel_count=vignette_counter - 1,
            page_count=page_count,
            strategy="lector pagina a pagina",
        )
    except MangaDownloadError:
        raise
    except Exception as exc:
        raise MangaDownloadError(f"Fallo el lector pagina a pagina: {exc}") from exc
    finally:
        driver.quit()


def _reader_image_urls(soup, base_url):
    containers = [
        soup.find("div", class_="reading-content"),
        soup.find("main"),
        soup.find("article"),
        soup.body,
    ]
    urls = []
    for container in containers:
        if not container:
            continue
        for image in container.find_all("img"):
            raw_url = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-lazy-src")
                or image.get("data-original")
                or ""
            ).strip()
            if not raw_url or raw_url.startswith("data:image/svg"):
                continue
            absolute_url = raw_url if raw_url.startswith("data:image/") else urljoin(base_url, raw_url)
            if absolute_url not in urls:
                urls.append(absolute_url)
        if urls:
            break
    return urls


def _browser_image_cache(driver, image_urls):
    cache = {}
    wanted = [url for url in image_urls if not url.startswith("data:image/")]
    if not wanted:
        return cache

    try:
        logs = driver.get_log("performance")
    except Exception:
        return cache

    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, ValueError):
            continue
        if message.get("method") != "Network.responseReceived":
            continue
        params = message.get("params") or {}
        response = params.get("response") or {}
        response_url = (response.get("url") or "").strip()
        mime_type = (response.get("mimeType") or "").casefold()
        if "image" not in mime_type and not any(_urls_match(url, response_url) for url in wanted):
            continue
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": params["requestId"]})
        except Exception:
            continue
        cache[response_url] = base64.b64decode(body["body"]) if body.get("base64Encoded") else body.get("body", "").encode("utf-8")
    return cache


def _image_bytes_from_cache_or_url(cache, image_url, referer, session):
    if image_url.startswith("data:image/") and "," in image_url:
        return base64.b64decode(image_url.split(",", 1)[1])

    for cache_url, data in cache.items():
        if _urls_match(image_url, cache_url):
            return data

    try:
        response = session.get(
            image_url,
            headers={
                "Referer": referer,
                "User-Agent": "Mozilla/5.0 ARCATV/0.1",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None


def _urls_match(left, right):
    return bool(left and right and (left == right or left in right or right in left))


def _canvas_image_bytes(driver):
    data_url = driver.execute_script(
        """
        var img = document.getElementById('m_img') || document.querySelector('img');
        if (!img || !img.naturalWidth || !img.naturalHeight) return null;
        var canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        return canvas.toDataURL('image/jpeg');
        """
    )
    if not data_url or "," not in data_url:
        return None
    return base64.b64decode(data_url.split(",", 1)[1])


def _reader_current_image_bytes(driver, referer):
    src = driver.execute_script(
        """
        var img = document.getElementById('m_img') || document.querySelector('img');
        return img ? (img.currentSrc || img.src) : null;
        """
    )
    if not src:
        return None
    return _image_bytes_from_cache_or_url({}, src, referer, requests.Session())


def _save_page_and_panels(full_page, page_index, chapter_dir, pages_dir, vignette_counter, vignette_map, cv2, np):
    page_filename = f"page_{page_index:02d}.jpg"
    full_page.save(pages_dir / page_filename, "JPEG", quality=85)

    panels = _crop_manga_panels(full_page, cv2, np)
    for panel in panels:
        vignette_filename = f"{vignette_counter:03d}.jpg"
        panel.save(chapter_dir / vignette_filename, "JPEG", quality=90)
        vignette_map[str(vignette_counter)] = page_filename
        vignette_counter += 1
    return vignette_counter


def _crop_manga_panels(pil_img, cv2, np):
    if cv2 is None or np is None:
        return [pil_img]

    open_cv_image = np.array(pil_img)
    open_cv_image = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 230, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    height, width, _ = open_cv_image.shape
    min_area = (width * height) * 0.03

    panels_rects = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area > min_area and w < width * 0.98 and h < height * 0.98:
            panels_rects.append((x, y, w, h))

    if not panels_rects:
        return [pil_img]

    panels_rects.sort(key=lambda rect: rect[1])
    rows = []
    current_row = [panels_rects[0]]
    row_y = panels_rects[0][1]

    for rect in panels_rects[1:]:
        if abs(rect[1] - row_y) < 120:
            current_row.append(rect)
        else:
            current_row.sort(key=lambda item: item[0], reverse=True)
            rows.extend(current_row)
            current_row = [rect]
            row_y = rect[1]

    current_row.sort(key=lambda item: item[0], reverse=True)
    rows.extend(current_row)

    cropped_panels = []
    for x, y, w, h in rows:
        crop_bgr = open_cv_image[y:y + h, x:x + w]
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        cropped_panels.append(_image_from_array(crop_rgb))
    return cropped_panels


def _image_from_array(array):
    from PIL import Image

    return Image.fromarray(array)


def _write_config(chapter_dir, vignette_map):
    (chapter_dir / "config.json").write_text(
        json.dumps(vignette_map, indent=2),
        encoding="utf-8",
    )
