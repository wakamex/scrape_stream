# %%
import requests
import datetime
import time
import dotenv
import os
import shutil
import threading
from pathlib import Path
from tqdm import tqdm

# %%
denv = dotenv.dotenv_values(".env")

MP3_DIR: str = str(denv.get("MP3_DIR", "/mnt/raid5/mp3s"))
CHANNEL: str = str(denv.get("CHANNEL", "hardstyle"))
DI_USERNAME: str = str(denv.get("DI_USERNAME", ""))
DI_PASSWORD: str = str(denv.get("DI_PASSWORD", ""))


def load_channels() -> list[str]:
    """Load channels from favorites.txt, falling back to CHANNEL env var."""
    fav_path = Path(__file__).parent / "favorites.txt"
    if fav_path.exists():
        channels = [l.strip() for l in fav_path.read_text().splitlines() if l.strip()]
        if channels:
            return channels
    return [CHANNEL]


def login() -> str:
    """Login to DI.FM and return API key."""
    if not DI_USERNAME or not DI_PASSWORD:
        raise Exception("DI_USERNAME and DI_PASSWORD required in .env")

    response = requests.post(
        "https://api.audioaddict.com/v1/di/members/authenticate",
        data={"username": DI_USERNAME, "password": DI_PASSWORD},
    )
    if response.status_code != 200:
        raise Exception(f"Login failed: {response.status_code} {response.text}")

    data = response.json()
    api_key = data.get("api_key")
    if not api_key:
        raise Exception(f"No api_key in response: {data}")

    return api_key


def get_channel_id(channel_key: str) -> int:
    """Get channel ID from channel key."""
    url = "https://api.audioaddict.com/v1/di/channels"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to get channels: {response.status_code}")

    for ch in response.json():
        if ch["key"] == channel_key:
            return ch["id"]
    raise Exception(f"Channel not found: {channel_key}")


def get_routine(channel_id: int, api_key: str):
    """Get current routine (playlist) for a channel."""
    url = f"https://api.audioaddict.com/v1/di/routines/channel/{channel_id}?tune_in=true"
    response = requests.get(url, headers={"X-Api-Key": api_key})
    if response.status_code != 200:
        raise Exception(f"Failed to get routine: {response.status_code}")
    return response.json()


def get_currently_playing(channel_id: int):
    """Get currently playing track info."""
    url = "https://api.audioaddict.com/v1/di/currently_playing"
    response = requests.get(url)
    if response.status_code != 200:
        return None

    for cp in response.json():
        if cp["channel_id"] == channel_id:
            return cp
    return None


def download_track(url: str, output_path: str, desc: str = "Downloading"):
    """Download a track with progress bar."""
    # Ensure URL has https
    if url.startswith("//"):
        url = "https:" + url

    response = requests.get(url, stream=True)
    if response.status_code != 200:
        print(f"Failed to download: {response.status_code}")
        return False

    total_size = int(response.headers.get('content-length', 0))

    with open(output_path, 'wb') as f:
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=desc) as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

    return True


def sanitize_filename(name: str) -> str:
    """Remove invalid filename characters."""
    return "".join(c for c in name if c not in r'<>:"/\|?*')


def scrape_channel(channel_key: str, api_key: str):
    """Scrape a single channel continuously."""
    channel_dir = os.path.join(MP3_DIR, channel_key)
    os.makedirs(channel_dir, exist_ok=True)

    tag = f"[{channel_key}]"
    print(f"{tag} Getting channel ID...")
    channel_id = get_channel_id(channel_key)
    print(f"{tag} Channel ID: {channel_id}")

    downloaded_ids = set()

    while True:
        try:
            routine = get_routine(channel_id, api_key)

            currently_playing = get_currently_playing(channel_id)
            if not currently_playing:
                time.sleep(10)
                continue

            current_track_id = currently_playing["track"]["id"]

            current_track = None
            for track in routine.get("tracks", []):
                if track["id"] == current_track_id:
                    current_track = track
                    break

            if not current_track:
                time.sleep(10)
                continue

            # Skip if already downloaded — just wait silently
            if current_track_id in downloaded_ids:
                start_time_str = currently_playing["track"]["start_time"]
                duration = currently_playing["track"]["duration"]
                start_time = datetime.datetime.fromisoformat(start_time_str)
                elapsed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
                time_left = max(0, duration - elapsed)
                time.sleep(min(time_left + 2, 30))
                continue

            content = current_track.get("content", {})
            assets = content.get("assets", [])
            if not assets:
                downloaded_ids.add(current_track_id)
                time.sleep(5)
                continue

            track_url = assets[0]["url"]
            track_name = f"{current_track['display_artist']} - {current_track['display_title']}"
            safe_name = sanitize_filename(track_name)
            output_path = f"{channel_dir}/{safe_name}.mp3"

            if os.path.exists(output_path):
                downloaded_ids.add(current_track_id)
                continue

            start_time_str = currently_playing["track"]["start_time"]
            duration = currently_playing["track"]["duration"]
            start_time = datetime.datetime.fromisoformat(start_time_str)
            end_time = start_time + datetime.timedelta(seconds=duration)

            print(f"\n{tag} Downloading: {track_name}")

            temp_path = f"{channel_dir}/temp.mp3"
            if download_track(track_url, temp_path, desc=f"{channel_key}: {safe_name[:40]}"):
                shutil.move(temp_path, output_path)
                print(f"{tag} Saved: {safe_name}.mp3")
                downloaded_ids.add(current_track_id)
            else:
                print(f"{tag} Failed: {track_name}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            now = datetime.datetime.now(datetime.timezone.utc)
            time_left = (end_time - now).total_seconds()
            if time_left > 0:
                print(f"{tag} Waiting {time_left:.0f}s for track to end...")
                time.sleep(time_left + 1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"{tag} Error: {e}")
            time.sleep(10)


def main():
    channels = load_channels()

    print("Logging in to DI.FM...")
    api_key = login()
    print(f"Got API key: {api_key[:8]}...")
    print(f"Scraping {len(channels)} channels: {', '.join(channels)}")

    if len(channels) == 1:
        scrape_channel(channels[0], api_key)
    else:
        threads = []
        for ch in channels:
            t = threading.Thread(target=scrape_channel, args=(ch, api_key), daemon=True)
            t.start()
            threads.append(t)
            time.sleep(0.5)  # Stagger API calls

        try:
            while any(t.is_alive() for t in threads):
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")


if __name__ == "__main__":
    main()
