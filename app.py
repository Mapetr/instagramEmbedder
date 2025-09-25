import json
import os

import crawleruseragents
from pickledb import PickleDB
from flask import Flask, url_for, render_template_string, request, redirect
from yt_dlp import YoutubeDL

app = Flask(__name__)

# Ensure output folder exists
OUTPUT_FOLDER = os.path.join(os.getcwd(), "static")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

db = PickleDB("data/cache.db")


@app.route("/p/<id>")
@app.route("/p/<id>/")
@app.route("/reel/<id>")
@app.route("/reel/<id>/")
def index(id):
    cache = db.get(id)
    if cache:
        print("Cache hit!")
        return cache

    url = f"https://www.instagram.com/p/{id}/"

    user_agent = request.headers.get("User-Agent")
    if not crawleruseragents.is_crawler(user_agent):
        return redirect(url, code=302)

    ydl_opts = {
        "cookiefile": "data/cookies.txt",
        "format": "best",
        "outtmpl": os.path.join(OUTPUT_FOLDER, "%(id)s.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegCopyStream"},
        ],
        "postprocessor_args": {
            "copystream": [
                "-c:v", "libx264", "-c:a", "ac3"
            ],
        },
        "quiet": False,
        "verbose": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        info_object = ydl.sanitize_info(info)

        title = info_object.get("title", "")
        description = info_object.get("description", "")
        thumbnail = info_object.get("thumbnail", "")
        ext = info_object.get("ext", "mp4")
        filename = f"{info_object.get('id')}.{ext}"

        print(json.dumps(info_object, indent=4, sort_keys=True, ensure_ascii=False))

        # Download media
        ydl.download([url])

    # Build the public URL to the file
    file_url = url_for("static", filename=filename, _external=True)

    # Render with Open Graph meta tags
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
        <meta property="og:type" content="video.other">
        <meta property="og:url" content="{{ url }}">
        <meta property="og:image" content="{{ thumbnail }}">
        <meta property="og:video" content="{{ url }}">
        <meta property="og:video:type" content="video/mp4">

        <!-- Twitter Card -->
        <meta name="twitter:card" content="player">
        <meta name="twitter:title" content="{{ title }}">
        <meta name="twitter:description" content="{{ description }}">
        <meta name="twitter:image" content="{{ thumbnail }}">
        <meta name="twitter:player" content="{{ url }}">
    </head>
    <body>
    </body>
    </html>
    """

    finished_html = render_template_string(
        html_template,
        title=title,
        description=description,
        url=file_url,
        thumbnail=thumbnail,
    )

    db.set(id, finished_html)
    db.save()

    return finished_html

