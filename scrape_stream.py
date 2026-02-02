# %%
from typing import Union

import requests
import subprocess
import datetime
import time
import dotenv
import dateutil.parser
import pandas as pd
import os
import shutil
from tqdm import tqdm

# %%
denv = dotenv.dotenv_values(".env")

STREAM_URL: str = str(denv["STREAM_URL"])
MP3_DIR: str = str(denv.get("MP3_DIR", "/mnt/raid5/mp3s"))


# %%
def record_track(stream_url, output_filename, duration=None):
    """Record from stream to file. If duration is set, ffmpeg stops automatically."""
    command = ["ffmpeg", "-y", "-i", stream_url, "-vn", "-acodec", "copy"]
    if duration and duration > 0:
        command.extend(["-t", str(int(duration) + 1)])  # +1s buffer for timing drift
    command.append(output_filename)
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)


# %%
def get_currently_playing(channel: Union[str, int], channels):
    if isinstance(channel, str):
        channel = int(channels[channels["key"] == channel].iloc[0]["id"])
    channel_id: int = channel

    url = "https://api.audioaddict.com/v1/di/currently_playing"
    response = requests.get(url)

    if response.status_code != 200:
        print(f"Unable to fetch currently playing track info: {response.status_code}")
        return None

    currently_playing_stations = response.json()

    return next(
        (cp for cp in currently_playing_stations if cp["channel_id"] == channel_id),
        None,
    )


# %%
def get_channels():
    url = "https://api.audioaddict.com/v1/di/channels"
    response = requests.get(url)

    if response.status_code != 200:
        print(f"Unable to fetch channels: {response.status_code}")
        return None

    records = [{"id": item["id"], "key": item["key"], "name": item["name"]} for item in response.json()]
    return pd.DataFrame.from_records(records)


def get_track_info(channel: str, channels):
    """Get current track info. Returns (time_left, name) or None if failed."""
    current_track = get_currently_playing(channel, channels)
    if not current_track:
        return None

    start_time = dateutil.parser.parse(current_track["track"]["start_time"])
    time_passed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
    duration = current_track["track"]["duration"]
    time_left = duration - time_passed
    name = f"{current_track['track']['display_artist']} - {current_track['track']['display_title']}"
    return time_left, name


def wait_for_process(p, timeout=None):
    """Wait for process to complete, with optional timeout."""
    try:
        p.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def save_recording(name):
    """Move temp recording to final filename."""
    temp_file = f"{MP3_DIR}/temp.mp3"
    if not os.path.exists(temp_file):
        print(f"Warning: temp file not found: {temp_file}")
        return False

    # Sanitize filename
    safe_name = "".join(c for c in name if c not in r'<>:"/\|?*')
    output_filename = f"{MP3_DIR}/{safe_name}.mp3"

    print(f"Saving: {safe_name}.mp3")
    shutil.move(temp_file, output_filename)
    return True


# %%
def main(channel: str):
    channels = get_channels()
    if channels is None:
        print("Failed to get channel list")
        return

    print(f"Starting stream ripper for {channel}...")
    os.makedirs(MP3_DIR, exist_ok=True)

    while True:
        # Get current track info
        track_info = get_track_info(channel, channels)
        if not track_info:
            print("Failed to get track info, retrying in 10s...")
            time.sleep(10)
            continue

        time_left, name = track_info

        # Skip if track is almost over (< 5s left)
        if time_left < 5:
            print(f"Track '{name}' almost over ({time_left:.0f}s left), waiting for next...")
            time.sleep(time_left + 2)
            continue

        print(f"\nRecording: {name} ({time_left:.0f}s remaining)")

        # Start recording with duration limit
        temp_file = f"{MP3_DIR}/temp.mp3"
        p = record_track(STREAM_URL, temp_file, duration=time_left)

        # Show progress bar while recording
        with tqdm(total=time_left, bar_format="{l_bar}{bar}| {n:.0f}/{total:.0f}s") as pbar:
            start = time.time()
            while p.poll() is None:  # While ffmpeg is running
                time.sleep(0.5)
                elapsed = time.time() - start
                pbar.n = min(elapsed, time_left)
                pbar.refresh()

        # ffmpeg finished (duration reached), save the file
        save_recording(name)


if __name__ == "__main__":
    main(channel="hardstyle")
