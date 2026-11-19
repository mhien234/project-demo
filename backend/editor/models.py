"""Project data model: a project holds tracks, a track holds clips.

Times are in seconds on the timeline. A clip shows media from `in_point`
to `out_point`, starting at `start` on the timeline.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

TRACK_KINDS = ("video", "audio", "text")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class Clip:
    id: str
    start: float
    in_point: float = 0.0
    out_point: float = 0.0
    media_id: str | None = None
    text: str | None = None          # text clips (captions, titles)
    volume: float = 1.0
    look: str | None = None          # film look name, see app/features/looks.py

    @property
    def duration(self) -> float:
        return max(0.0, self.out_point - self.in_point)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Track:
    id: str
    kind: str
    name: str = ""
    muted: bool = False
    clips: list[Clip] = field(default_factory=list)


@dataclass
class Project:
    id: str
    name: str
    width: int = 1080
    height: int = 1920
    fps: int = 30
    tracks: list[Track] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [c.end for t in self.tracks for c in t.clips]
        return max(ends) if ends else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Project":
        tracks = [
            Track(id=t["id"], kind=t["kind"], name=t.get("name", ""), muted=t.get("muted", False),
                  clips=[Clip(**c) for c in t.get("clips", [])])
            for t in d.get("tracks", [])
        ]
        return Project(id=d["id"], name=d["name"], width=d.get("width", 1080), height=d.get("height", 1920),
                       fps=d.get("fps", 30), tracks=tracks)

    def find_track(self, track_id: str) -> Track:
        for t in self.tracks:
            if t.id == track_id:
                return t
        raise KeyError(f"track not found: {track_id}")

    def find_clip(self, clip_id: str) -> tuple[Track, Clip]:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return t, c
        raise KeyError(f"clip not found: {clip_id}")


@dataclass
class Media:
    id: str
    path: str
    original_name: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    variable_fps: bool = False
