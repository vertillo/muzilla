"""One-off script that tags the committed silent audio fixtures.

Run manually with `python tests/fixtures/audio/tag_fixtures.py` after
regenerating any fixture file with ffmpeg. Not part of the test suite
itself — the tagged output IS the fixture, committed to the repo so CI
never needs ffmpeg or this script.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import (
    COMM,
    TALB,
    TBPM,
    TCOM,
    TCON,
    TDRC,
    TENC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TPUB,
    TRCK,
    TSRC,
    TXXX,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

HERE = Path(__file__).parent

COMMON = {
    "title": "Ágætis byrjun",
    "artist": "Sigur Rós",
    "album": "Ágætis byrjun",
    "album_artist": "Sigur Rós",
    "composer": "Jónsi",
    "track_no": 9,
    "track_total": 10,
    "disc_no": 1,
    "date": "1999-06-12",
    "genre": "Post-Rock",
    "bpm": 72,
    "isrc": "GBUM71029604",
    "label": "PLAY IT AGAIN SAM",
    "comment": "Ripped with LAME 3.98",
    "mb_release_id": "f4a0f0a0-0f0a-0f0a-0f0a-0f0a0f0a0f0a",
}


def tag_vorbis(path: Path, cls: type) -> None:
    audio = cls(path)
    audio["TITLE"] = COMMON["title"]
    audio["ARTIST"] = [COMMON["artist"]]
    audio["ALBUM"] = COMMON["album"]
    audio["ALBUMARTIST"] = COMMON["album_artist"]
    audio["COMPOSER"] = COMMON["composer"]
    audio["TRACKNUMBER"] = str(COMMON["track_no"])
    audio["TRACKTOTAL"] = str(COMMON["track_total"])
    audio["DISCNUMBER"] = str(COMMON["disc_no"])
    audio["DATE"] = COMMON["date"]
    audio["GENRE"] = [COMMON["genre"]]
    audio["BPM"] = str(COMMON["bpm"])
    audio["ISRC"] = COMMON["isrc"]
    audio["LABEL"] = COMMON["label"]
    audio["COMMENT"] = COMMON["comment"]
    audio["MUSICBRAINZ_ALBUMID"] = COMMON["mb_release_id"]
    audio.save()


def tag_mp4(path: Path) -> None:
    audio = MP4(path)
    audio["\xa9nam"] = COMMON["title"]
    audio["\xa9ART"] = COMMON["artist"]
    audio["\xa9alb"] = COMMON["album"]
    audio["aART"] = COMMON["album_artist"]
    audio["\xa9wrt"] = COMMON["composer"]
    audio["trkn"] = [(COMMON["track_no"], COMMON["track_total"])]
    audio["disk"] = [(COMMON["disc_no"], 1)]
    audio["\xa9day"] = COMMON["date"]
    audio["\xa9gen"] = COMMON["genre"]
    audio["tmpo"] = [COMMON["bpm"]]
    audio["\xa9cmt"] = COMMON["comment"]
    audio["----:com.apple.iTunes:ISRC"] = [MP4FreeForm(COMMON["isrc"].encode())]
    audio["----:com.apple.iTunes:LABEL"] = [MP4FreeForm(COMMON["label"].encode())]
    audio["----:com.apple.iTunes:MusicBrainz Album Id"] = [
        MP4FreeForm(COMMON["mb_release_id"].encode())
    ]
    audio.save()


def _add_id3_frames(audio: MP3 | WAVE | AIFF) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.add(TIT2(encoding=3, text=COMMON["title"]))
    tags.add(TPE1(encoding=3, text=COMMON["artist"]))
    tags.add(TALB(encoding=3, text=COMMON["album"]))
    tags.add(TPE2(encoding=3, text=COMMON["album_artist"]))
    tags.add(TCOM(encoding=3, text=COMMON["composer"]))
    tags.add(TRCK(encoding=3, text=f"{COMMON['track_no']}/{COMMON['track_total']}"))
    tags.add(TPOS(encoding=3, text=str(COMMON["disc_no"])))
    tags.add(TDRC(encoding=3, text=COMMON["date"]))
    tags.add(TCON(encoding=3, text=COMMON["genre"]))
    tags.add(TBPM(encoding=3, text=str(COMMON["bpm"])))
    tags.add(TSRC(encoding=3, text=COMMON["isrc"]))
    tags.add(TPUB(encoding=3, text=COMMON["label"]))
    tags.add(TENC(encoding=3, text="LAME3.98"))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=COMMON["comment"]))
    tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=COMMON["mb_release_id"]))
    audio.save(v2_version=4)


def tag_mp3(path: Path) -> None:
    _add_id3_frames(MP3(path))


def tag_wave(path: Path) -> None:
    _add_id3_frames(WAVE(path))


def tag_aiff(path: Path) -> None:
    _add_id3_frames(AIFF(path))


def main() -> None:
    tag_vorbis(HERE / "silence.flac", FLAC)
    tag_vorbis(HERE / "silence.ogg", OggVorbis)
    tag_vorbis(HERE / "silence.opus", OggOpus)
    tag_mp4(HERE / "silence.m4a")
    tag_mp3(HERE / "silence.mp3")
    tag_wave(HERE / "silence.wav")
    tag_aiff(HERE / "silence.aiff")
    print("tagged all fixtures")


if __name__ == "__main__":
    main()
