import json
import os
import subprocess
import mimetypes
from glob import glob

import crawleruseragents
from pickledb import PickleDB
from flask import Flask, url_for, render_template_string, request, redirect
from PIL import Image

# Ensure AVIF mimetype is known
mimetypes.add_type("image/avif", ".avif")

app = Flask(__name__)

# Ensure output folder exists
OUTPUT_FOLDER = os.path.join(os.getcwd(), "static")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

COOKIES_FILE = os.path.join("data", "cookies.txt")

# Debug flag (set APP_DEBUG=1 to enable verbose logging)
DEBUG = str(os.environ.get("APP_DEBUG", "")).lower() in ("1", "true", "yes", "on")

def dlog(msg: str):
    if DEBUG:
        print(msg)

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
VIDEO_EXTS = {"mp4", "mov", "webm", "m4v"}


db = PickleDB("data/cache.db")


def is_video(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return True
    # fallback to mimetypes
    mime, _ = mimetypes.guess_type(filename)
    return bool(mime and mime.startswith("video/"))


def run_gallery_dl(url: str, post_id: str) -> None:
    """Download media for the given Instagram URL into OUTPUT_FOLDER using gallery-dl.
    Non-fatal return codes: 0 (success) and 4 (nothing downloaded/skipped)."""
    cmd = [
        "gallery-dl",
        "-o", f"base-directory={OUTPUT_FOLDER}",
        "-o", "path-restrict=auto",
        "-o", "skip=true",
        "-o", "mtime-from-timestamp=true",
        "-o", "directory=.",
        # Use shortcode so our collected files match the URL id
        "-o", f"filename={post_id}-{{num}}.{{extension}}",
    ]
    if os.path.exists(COOKIES_FILE):
        cmd.extend(["--cookies", COOKIES_FILE])
        dlog(f"Using cookies file: {COOKIES_FILE}")
    cmd.append(url)
    dlog(f"Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    dlog(f"gallery-dl return code: {proc.returncode}")
    if DEBUG:
        if proc.stdout:
            dlog(f"gallery-dl stdout:\n{proc.stdout}")
        if proc.stderr:
            dlog(f"gallery-dl stderr:\n{proc.stderr}")
    # Treat 0 ok, 4 (skipped/nothing) ok; others raise
    if proc.returncode not in (0, 4):
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)


def collect_files(post_id: str) -> list:
    # Look for files like id-*.ext (preferred) or id.* (fallback)
    patterns = [
        os.path.join(OUTPUT_FOLDER, f"{post_id}-*.*"),
        os.path.join(OUTPUT_FOLDER, f"{post_id}.*"),
    ]
    files = []
    for p in patterns:
        files.extend(glob(p))
    # De-duplicate and sort naturally
    files = sorted(set(os.path.basename(f) for f in files))
    return files


def convert_images_to_avif(post_id: str, quality: int = 60, speed: int = 6) -> None:
    """Convert downloaded images for this post to AVIF to conserve space.
    Keeps original file only if conversion fails or AVIF is not smaller.
    """
    # Work on a snapshot of current files
    files = collect_files(post_id)
    for basename in files:
        src_path = os.path.join(OUTPUT_FOLDER, basename)
        name, ext = os.path.splitext(basename)
        ext_no_dot = ext.lower().lstrip(".")
        if is_video(basename):
            continue
        if ext_no_dot not in IMAGE_EXTS or ext_no_dot == "avif":
            continue
        dest_basename = f"{name}.avif"
        dest_path = os.path.join(OUTPUT_FOLDER, dest_basename)
        if os.path.exists(dest_path):
            dlog(f"AVIF already exists for {basename}; skipping")
            # Optionally remove original if avif exists and smaller
            try:
                if os.path.getsize(dest_path) < os.path.getsize(src_path):
                    os.remove(src_path)
                    dlog(f"Removed original {basename} (AVIF smaller)")
            except Exception as e:
                dlog(f"Size check/remove error for {basename}: {e}")
            continue
        try:
            dlog(f"Converting {basename} -> {dest_basename} (q={quality}, speed={speed})")
            with Image.open(src_path) as im:
                save_params = {"quality": quality, "speed": speed}
                im.save(dest_path, format="AVIF", **save_params)
            # Compare sizes and remove original if smaller
            try:
                avif_size = os.path.getsize(dest_path)
                orig_size = os.path.getsize(src_path)
                dlog(f"Sizes for {basename}: orig={orig_size} avif={avif_size}")
                if avif_size < orig_size:
                    os.remove(src_path)
                    dlog(f"Removed original {basename} (saved {orig_size - avif_size} bytes)")
                else:
                    dlog(f"Keeping original {basename} (AVIF not smaller)")
            except Exception as e:
                dlog(f"Post-conversion size compare failed for {basename}: {e}")
        except Exception as e:
            dlog(f"Failed to convert {basename} to AVIF: {e}")


def extract_metadata(url: str) -> dict:
    """Try to get metadata via gallery-dl -j. Returns dict with title, description, thumbnail."""
    meta = {"title": "Instagram Media", "description": "", "thumbnail": ""}
    try:
        dlog(f"Extracting metadata for: {url}")
        proc = subprocess.run(["gallery-dl", "-j", url], check=False, capture_output=True, text=True)
        if DEBUG and proc.stderr:
            dlog(f"gallery-dl -j stderr:\n{proc.stderr}")
        if proc.returncode not in (0, 4):
            dlog(f"gallery-dl -j returned code {proc.returncode}")
            return meta
        lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
        if DEBUG:
            dlog(f"gallery-dl -j produced {len(lines)} JSON lines")
        if not lines:
            return meta
        first = lines[0]
        # Common fields
        meta["title"] = first.get("title") or first.get("owner") or meta["title"]
        meta["description"] = first.get("description") or first.get("caption") or ""
        thumb = first.get("thumbnail") or first.get("preview") or first.get("url")
        if thumb:
            meta["thumbnail"] = thumb
    except Exception as e:
        dlog(f"Metadata extraction error: {e}")
    return meta


@app.route("/p/<id>")
@app.route("/p/<id>/")
@app.route("/reel/<id>")
@app.route("/reel/<id>/")
def index(id):
    use_cache = not app.debug
    if use_cache:
        cache = db.get(id)
        if cache:
            dlog("Cache hit!")
            return cache
    else:
        dlog("Flask debug=True; skipping cache")

    # Build Instagram URL depending on route
    if request.path.startswith("/reel/"):
        url = f"https://www.instagram.com/reel/{id}/"
    else:
        url = f"https://www.instagram.com/p/{id}/"
    dlog(f"Resolved Instagram URL: {url}")

    user_agent = request.headers.get("User-Agent")
    dlog(f"User-Agent: {user_agent}")
    if not crawleruseragents.is_crawler(user_agent):
        dlog("Non-crawler detected; redirecting to Instagram")
        return redirect(url, code=302)

    # Fetch metadata (best-effort) and download media (supports images and carousels)
    meta = extract_metadata(url)
    try:
        run_gallery_dl(url, id)
    except subprocess.CalledProcessError as e:
        # If download fails, still try to render something with original URL
        dlog(f"gallery-dl failed: {e}")

    # Convert images to AVIF to conserve space/bandwidth
    try:
        convert_images_to_avif(id)
    except Exception as e:
        dlog(f"AVIF conversion step error: {e}")

    files = collect_files(id)
    dlog(f"Collected files: {files}")

    # Prepare media URLs
    media_urls = [url_for("static", filename=f, _external=True) for f in files]

    # Choose primary media
    primary_url = media_urls[0] if media_urls else url  # fallback to original
    primary_is_video = False
    if media_urls:
        primary_is_video = is_video(files[0])
    dlog(f"Primary URL: {primary_url} (video={primary_is_video})")

    # Determine OG/Twitter types
    og_type = "video.other" if primary_is_video else "article"
    twitter_card = "player" if primary_is_video else "summary_large_image"

    # Thumbnail: prefer metadata, else first image file, else primary
    thumbnail = meta.get("thumbnail") or next((u for f, u in zip(files, media_urls) if not is_video(f)), primary_url)
    dlog(f"Thumbnail selected: {thumbnail}")

    # Render with Open Graph meta tags only (no inline media display)
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>{{ title }}</title>
        <meta name="description" content="{{ description }}">

        <!-- Open Graph -->
        <meta property="og:title" content="{{ title }}">
        <meta property="og:description" content="{{ description }}">
        <meta property="og:type" content="{{ og_type }}">
        <meta property="og:url" content="{{ primary_url }}">
        {% for img in images %}
        <meta property="og:image" content="{{ img }}">
        {% endfor %}
        {% if primary_is_video %}
        <meta property="og:video" content="{{ primary_url }}">
        <meta property="og:video:type" content="video/mp4">
        {% endif %}

        <!-- Twitter Card -->
        <meta name="twitter:card" content="{{ twitter_card }}">
        <meta name="twitter:title" content="{{ title }}">
        <meta name="twitter:description" content="{{ description }}">
        {% for img in images %}
        <meta name="twitter:image" content="{{ img }}">
        {% endfor %}
        {% if primary_is_video %}
        <meta name="twitter:player" content="{{ primary_url }}">
        {% endif %}
    </head>
    <body>
    </body>
    </html>
    """

    finished_html = render_template_string(
        html_template,
        title=meta.get("title") or "Instagram Media",
        description=meta.get("description") or "",
        og_type=og_type,
        twitter_card=twitter_card,
        primary_url=primary_url,
        primary_is_video=primary_is_video,
        thumbnail=thumbnail,
        images=[u for f, u in zip(files, media_urls) if not is_video(f)],
    )

    if use_cache:
        db.set(id, finished_html)
        db.save()
    else:
        dlog("Skipping cache save due to Flask debug")

    return finished_html

