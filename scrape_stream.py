# %%
import requests
import datetime
import time
import dotenv
import os
import shutil
from tqdm import tqdm

# %%
denv = dotenv.dotenv_values(".env")

MP3_DIR: str = str(denv.get("MP3_DIR", "/mnt/raid5/mp3s"))
CHANNEL: str = str(denv.get("CHANNEL", "hardstyle"))
DI_USERNAME: str = str(denv.get("DI_USERNAME", ""))
DI_PASSWORD: str = str(denv.get("DI_PASSWORD", ""))


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


def main():
    channel_dir = os.path.join(MP3_DIR, CHANNEL)
    os.makedirs(channel_dir, exist_ok=True)

    print("Logging in to DI.FM...")
    api_key = login()
    print(f"Got API key: {api_key[:8]}...")

    print(f"Getting channel ID for {CHANNEL}...")
    channel_id = get_channel_id(CHANNEL)
    print(f"Channel ID: {channel_id}")

    downloaded_ids = set()  # Track what we've already downloaded

    while True:
        try:
            # Get current routine (includes track content URLs)
            routine = get_routine(channel_id, api_key)

            # Get currently playing to know which track is active
            currently_playing = get_currently_playing(channel_id)
            if not currently_playing:
                print("Failed to get currently playing, retrying in 10s...")
                time.sleep(10)
                continue

            current_track_id = currently_playing["track"]["id"]

            # Find the current track in the routine
            current_track = None
            for track in routine.get("tracks", []):
                if track["id"] == current_track_id:
                    current_track = track
                    break

            if not current_track:
                print(f"Current track {current_track_id} not in routine, retrying in 10s...")
                time.sleep(10)
                continue

            # Skip if already downloaded
            if current_track_id in downloaded_ids:
                # Wait until track ends
                start_time_str = currently_playing["track"]["start_time"]
                duration = currently_playing["track"]["duration"]
                # Parse start time and calculate time left
                start_time = datetime.datetime.fromisoformat(start_time_str)
                elapsed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
                time_left = max(0, duration - elapsed)
                print(f"Already have track {current_track_id}, waiting {time_left:.0f}s for next...")
                time.sleep(min(time_left + 2, 30))  # Wait but check again in at most 30s
                continue

            # Get content URL
            content = current_track.get("content", {})
            assets = content.get("assets", [])
            if not assets:
                print(f"No assets for track {current_track_id}, skipping...")
                downloaded_ids.add(current_track_id)  # Don't retry
                time.sleep(5)
                continue

            track_url = assets[0]["url"]
            track_name = f"{current_track['display_artist']} - {current_track['display_title']}"
            safe_name = sanitize_filename(track_name)
            output_path = f"{channel_dir}/{safe_name}.mp3"

            # Skip if file already exists
            if os.path.exists(output_path):
                print(f"File exists: {safe_name}.mp3")
                downloaded_ids.add(current_track_id)
                continue

            # Calculate when track ends
            start_time_str = currently_playing["track"]["start_time"]
            duration = currently_playing["track"]["duration"]
            start_time = datetime.datetime.fromisoformat(start_time_str)
            end_time = start_time + datetime.timedelta(seconds=duration)

            print(f"\nDownloading: {track_name}")

            # Download to temp file first
            temp_path = f"{channel_dir}/temp.mp3"
            if download_track(track_url, temp_path, desc=safe_name[:50]):
                shutil.move(temp_path, output_path)
                print(f"Saved: {safe_name}.mp3")
                downloaded_ids.add(current_track_id)
            else:
                print(f"Failed to download: {track_name}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            # Wait until track ends
            now = datetime.datetime.now(datetime.timezone.utc)
            time_left = (end_time - now).total_seconds()
            if time_left > 0:
                print(f"Waiting {time_left:.0f}s for track to end...")
                time.sleep(time_left + 1)  # +1s buffer

        except KeyboardInterrupt:
            print("\nStopping...")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
