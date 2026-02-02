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
import signal
import shutil
from tqdm import tqdm

# %%
denv = dotenv.dotenv_values(".env")

STREAM_URL: str = str(denv["STREAM_URL"])


# %%
def record_track(stream_url, output_filename):
    command = ["ffmpeg", "-i", stream_url, "-vn", "-acodec", "copy", output_filename]
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
    # display(channels.style.hide(axis=0))
    # id	key         name
    # 1	    trance	    Trance
    # 2	    vocaltrance	Vocal Trance

def update_track_info(channel: str, channels):
    assert (current_track:=get_currently_playing(channel, channels)), "Unable to fetch currently playing track info"
    start_time = dateutil.parser.parse(current_track["track"]["start_time"]) # start of track
    time_passed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds() # since start of track
    duration = current_track["track"]["duration"] # duration of track
    time_left = duration - time_passed # time left in track
    end_time = start_time + datetime.timedelta(seconds=duration) # end of track
    name = f"{current_track['track']['display_artist']} - {current_track['track']['display_title']}"
    return time_passed, time_left, duration, end_time, name

def kill_process_and_children(p, name):
    print("Stopping current recording...", end="")
    os.killpg(os.getpgid(p.pid), signal.SIGTERM)

    # wait for process to terminate cleanly
    for _ in range(50):
        if p.poll() is not None:
            break
        print(".", end="")
        time.sleep(0.1)

    # if the process is still running, forcefully kill the process group
    if p.poll() is None:
        print("\nForcefully killing process group...", end="")
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)

    # wait for file to be written
    None if os.path.exists("/home/mihai/mp3s/temp.mp3") else print("Waiting for file to be written...", end="")
    while not os.path.exists("/home/mihai/mp3s/temp.mp3"):
        print(".", end="")
        time.sleep(0.1)
    # copy file to new location, trusting everything is ready
    output_filename = f"/home/mihai/mp3s/{name}.mp3"
    print(f"\nMoving file from /home/mihai/mp3s/temp.mp3 to {output_filename}...")
    shutil.move("/home/mihai/mp3s/temp.mp3", output_filename)
    print(f"{os.path.exists('/home/mihai/mp3s/temp.mp3')=}")
    print(f"{os.path.exists(output_filename)=}")

# %%
def main(channel: str):
    channels = get_channels()
    print("Starting stream ripper...")
    p = None
    SECONDS_INTO_TRACK = 10
    name = "new track"

    time_passed, time_left, duration, end_time, name = update_track_info(channel, channels)
    while True:
        if p is not None:
            kill_process_and_children(p, name)

        print("Starting new recording...")
        p = record_track(STREAM_URL, "/home/mihai/mp3s/temp.mp3")
        time_since_track_end = (datetime.datetime.now(datetime.timezone.utc) - end_time).total_seconds()

        # wait until 10s into the track to get the new track info
        time_since_track_switch = time_passed if time_since_track_end < 0 else time_since_track_end
        if new_track:=time_since_track_end < SECONDS_INTO_TRACK:
            sleep_duration = max(0, SECONDS_INTO_TRACK - time_since_track_end) # sleep until 10s into track
        else:
            time_passed, time_left, duration, end_time, name = update_track_info(channel, channels)
            sleep_duration = max(0, time_left) # sleep until end of track
        print(f"Debug - sleep_duration: {sleep_duration}")
        start_bar = datetime.datetime.now(datetime.timezone.utc) # time when we start the bar
        print(f"Debug - start_bar: {start_bar}")
        desc = f"{start_bar:%Y-%m-%d %H:%M:%S}: {name}"
        bar_format = "{l_bar}{bar}| {n:4.1f}/{total:.1f}"
        print(f"Debug - bar_format: {bar_format}")
        with tqdm(total=sleep_duration, bar_format="{l_bar}{bar}| {n:4.1f}/{total:.1f}", desc=desc) as pbar:
            while pbar.n < pbar.total:
                time.sleep(0.1)
                pbar.update(min(0.1, pbar.total - pbar.n))
                if new_track:
                    time_since_track_switch += 0.1
                    if time_since_track_switch >= SECONDS_INTO_TRACK:
                        new_track = False
                        time_passed, time_left, duration, end_time, name = update_track_info(channel, channels)
                        pbar.desc = f"{start_bar:%Y-%m-%d %H:%M:%S}: {name}"
                        pbar.total = max(0, duration) # get time to sleep
                        pbar.n = time_passed # update progress to be the time since the start of the track
                pbar.refresh()


if __name__ == "__main__":
    main(channel="hardstyle")

# %%

# %%
channels = get_channels()
current = get_currently_playing(60, channels)
print(f"{current=}")
current = get_currently_playing("hardstyle", channels)
print(f"{current=}")

schema = {
    "channel_id": 60,
    "channel_key": "hardstyle",
    "track": {
        "id": 3046583,
        "display_artist": "DJ Inzane",
        "display_title": "Hardstyle Classics #1",
        "start_time": "2023-05-13T22:38:10-04:00",
        "duration": 6569.0,
    },
}
