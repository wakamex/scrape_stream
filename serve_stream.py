import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import dotenv
from mutagen.id3 import ID3, TXXX
from mutagen.mp3 import MP3

denv = dotenv.dotenv_values(".env")
MP3_DIR = Path(denv.get("MP3_DIR", "/mnt/raid5/mp3s"))
HOST = denv.get("SERVE_HOST", "0.0.0.0")
PORT = int(denv.get("SERVE_PORT", "8000"))

# Global library cache: {channel: [track, ...]}
library: dict[str, list[dict]] = {}


def load_favorites() -> list[str]:
    fav_path = Path(__file__).parent / "favorites.txt"
    if fav_path.exists():
        return [l.strip() for l in fav_path.read_text().splitlines() if l.strip()]
    return []


def get_rating(tags: ID3) -> int:
    txxx = tags.getall("TXXX:RATING")
    if txxx and txxx[0].text:
        try:
            return int(txxx[0].text[0])
        except (ValueError, IndexError):
            pass
    return 0


def scan_library() -> dict[str, list[dict]]:
    favorites = load_favorites()
    result: dict[str, list[dict]] = {}

    if not MP3_DIR.is_dir():
        return result

    # Discover all channel directories
    channels = []
    for entry in sorted(MP3_DIR.iterdir()):
        if entry.is_dir():
            channels.append(entry.name)

    # Order: favorites first, then the rest
    ordered = [c for c in favorites if c in channels]
    ordered += [c for c in channels if c not in ordered]

    for channel in ordered:
        channel_dir = MP3_DIR / channel
        tracks = []
        for mp3_file in sorted(channel_dir.glob("*.mp3")):
            if mp3_file.name == "temp.mp3":
                continue
            artist = ""
            title = mp3_file.stem
            rating = 0
            try:
                tags = ID3(mp3_file)
                if "TPE1" in tags:
                    artist = str(tags["TPE1"])
                if "TIT2" in tags:
                    title = str(tags["TIT2"])
                rating = get_rating(tags)
            except Exception:
                # Fall back to filename parsing
                parts = mp3_file.stem.split(" - ", 1)
                if len(parts) == 2:
                    artist, title = parts
            tracks.append({
                "artist": artist,
                "title": title,
                "rating": rating,
                "path": f"{channel}/{mp3_file.name}",
            })
        if tracks:
            result[channel] = tracks

    return result


def set_rating(mp3_path: Path, rating: int):
    try:
        tags = ID3(mp3_path)
    except Exception:
        tags = ID3()
    tags.delall("TXXX:RATING")
    if rating > 0:
        tags.add(TXXX(encoding=3, desc="RATING", text=[str(rating)]))
    tags.save(mp3_path)


def generate_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Music Library</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 900px;
            margin: 40px auto;
            padding: 20px;
            padding-bottom: 120px;
            line-height: 1.6;
            color: #333;
        }
        h1 { margin-bottom: 8px; }
        .tagline { color: #666; margin-top: 0; }
        nav { border-bottom: 1px solid #eee; margin-bottom: 16px; display: flex; flex-wrap: wrap; gap: 0; }
        nav a {
            color: #555; text-decoration: none; padding: 8px 14px;
            border-bottom: 2px solid transparent; font-size: 0.9em;
        }
        nav a:hover { color: #333; }
        nav a.active { color: #0066cc; border-bottom-color: #0066cc; }
        table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 0.9em; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f5f5f5; }
        tr:hover { background: #f9f9f9; cursor: pointer; }
        tr.playing { background: #f0f7ff; }
        .stars { white-space: nowrap; cursor: pointer; font-size: 1.1em; }
        .stars span { color: #ccc; }
        .stars span.on { color: #e6a817; }
        .stars span:hover, .stars span.hover { color: #e6a817; }
        #player {
            position: fixed; bottom: 0; left: 0; right: 0;
            background: #f5f5f5; border-top: 1px solid #eee;
            padding: 12px 20px; display: none;
            align-items: center; gap: 16px;
        }
        #player .info { flex: 0 0 auto; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.9em; }
        #player .info .artist { color: #666; font-size: 0.85em; }
        #player audio { flex: 1; min-width: 200px; }
        #player .stars { flex: 0 0 auto; }
        .num { color: #888; width: 40px; }
    </style>
</head>
<body>
    <h1>Music Library</h1>
    <p class="tagline" id="subtitle">Loading...</p>
    <nav id="channels"></nav>
    <table id="tracklist"><tbody></tbody></table>

    <div id="player">
        <div class="info">
            <div class="title" id="p-title"></div>
            <div class="artist" id="p-artist"></div>
        </div>
        <audio id="audio" controls preload="none"></audio>
        <div class="stars" id="p-stars"></div>
    </div>

    <script>
    let channels = {};
    let currentChannel = null;
    let currentIdx = -1;
    let tracks = [];

    async function init() {
        const resp = await fetch('/api/tracks');
        channels = await resp.json();
        const names = Object.keys(channels);
        if (!names.length) {
            document.getElementById('subtitle').textContent = 'No tracks found.';
            return;
        }
        const total = names.reduce((s, k) => s + channels[k].length, 0);
        document.getElementById('subtitle').textContent = total + ' tracks in ' + names.length + ' channels';

        const nav = document.getElementById('channels');
        names.forEach(name => {
            const a = document.createElement('a');
            a.href = '#';
            a.textContent = name + ' (' + channels[name].length + ')';
            a.onclick = e => { e.preventDefault(); showChannel(name); };
            nav.appendChild(a);
        });

        showChannel(names[0]);
    }

    function showChannel(name) {
        currentChannel = name;
        tracks = channels[name] || [];
        document.querySelectorAll('nav a').forEach(a => {
            a.classList.toggle('active', a.textContent.startsWith(name + ' '));
        });
        const tbody = document.querySelector('#tracklist tbody');
        tbody.innerHTML = '';
        const head = document.createElement('tr');
        head.innerHTML = '<th class="num">#</th><th>Artist</th><th>Title</th><th>Rating</th>';
        tbody.appendChild(head);
        tracks.forEach((t, i) => {
            const tr = document.createElement('tr');
            tr.dataset.idx = i;
            tr.onclick = () => play(i);
            tr.innerHTML = '<td class="num">' + (i + 1) + '</td>' +
                '<td>' + esc(t.artist) + '</td>' +
                '<td>' + esc(t.title) + '</td>' +
                '<td>' + starsHtml(t.rating, i) + '</td>';
            tbody.appendChild(tr);
        });
    }

    function starsHtml(rating, idx) {
        let h = '<span class="stars" data-idx="' + idx + '">';
        for (let i = 1; i <= 5; i++) {
            h += '<span data-v="' + i + '" class="' + (i <= rating ? 'on' : '') + '"' +
                ' onclick="event.stopPropagation(); rate(' + idx + ',' + i + ')"' +
                ' onmouseenter="previewStars(this)" onmouseleave="clearPreview(this)"' +
                '>&#9733;</span>';
        }
        h += '</span>';
        return h;
    }

    function previewStars(el) {
        const v = +el.dataset.v;
        el.parentElement.querySelectorAll('span').forEach(s => {
            s.classList.toggle('hover', +s.dataset.v <= v);
        });
    }
    function clearPreview(el) {
        el.parentElement.querySelectorAll('span').forEach(s => s.classList.remove('hover'));
    }

    async function rate(idx, rating) {
        const t = tracks[idx];
        const prev = t.rating;
        // Toggle off if clicking same rating
        const newRating = (prev === rating) ? 0 : rating;
        const resp = await fetch('/api/rate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: t.path, rating: newRating})
        });
        if (resp.ok) {
            t.rating = newRating;
            // Update table stars
            const row = document.querySelector('tr[data-idx="' + idx + '"] .stars');
            if (row) row.outerHTML = starsHtml(newRating, idx);
            // Update player stars if this is the current track
            if (idx === currentIdx) renderPlayerStars(newRating);
        }
    }

    function play(idx) {
        const t = tracks[idx];
        currentIdx = idx;
        const audio = document.getElementById('audio');
        audio.src = '/mp3/' + encodeURIComponent(t.path);
        audio.play();
        document.getElementById('p-title').textContent = t.title;
        document.getElementById('p-artist').textContent = t.artist;
        document.getElementById('player').style.display = 'flex';
        renderPlayerStars(t.rating);
        document.querySelectorAll('tr.playing').forEach(r => r.classList.remove('playing'));
        const row = document.querySelector('tr[data-idx="' + idx + '"]');
        if (row) row.classList.add('playing');
    }

    function renderPlayerStars(rating) {
        const el = document.getElementById('p-stars');
        el.innerHTML = starsHtml(rating, currentIdx);
    }

    document.getElementById('audio').addEventListener('ended', () => {
        if (currentIdx < tracks.length - 1) play(currentIdx + 1);
    });

    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    init();
    </script>
</body>
</html>"""


class MusicHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress request logging noise
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            html = generate_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif path == "/api/tracks":
            self.send_json(library)

        elif path.startswith("/mp3/"):
            rel = urllib.parse.unquote(path[5:])
            self.serve_mp3(rel)

        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/rate":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                rel_path = body["path"]
                rating = int(body["rating"])

                if not 0 <= rating <= 5:
                    self.send_json({"error": "rating must be 0-5"}, 400)
                    return

                full_path = (MP3_DIR / rel_path).resolve()
                if not str(full_path).startswith(str(MP3_DIR.resolve())) or not full_path.is_file():
                    self.send_json({"error": "invalid path"}, 400)
                    return

                set_rating(full_path, rating)

                # Update cache
                for channel_tracks in library.values():
                    for t in channel_tracks:
                        if t["path"] == rel_path:
                            t["rating"] = rating
                            break

                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_error(404)

    def serve_mp3(self, rel_path: str):
        full_path = (MP3_DIR / rel_path).resolve()
        if not str(full_path).startswith(str(MP3_DIR.resolve())) or not full_path.is_file():
            self.send_error(404)
            return

        file_size = full_path.stat().st_size
        range_header = self.headers.get("Range")

        if range_header:
            # Parse "bytes=START-END"
            range_spec = range_header.replace("bytes=", "")
            parts = range_spec.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
        else:
            start = 0
            length = file_size
            self.send_response(200)
            self.send_header("Content-Length", str(file_size))

        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        with open(full_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break
                remaining -= len(chunk)


def main():
    global library

    print(f"Scanning library at {MP3_DIR}...")
    library = scan_library()
    total = sum(len(tracks) for tracks in library.values())
    print(f"Found {total} tracks in {len(library)} channels")

    server = ThreadingHTTPServer((HOST, PORT), MusicHandler)
    print(f"Serving at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
