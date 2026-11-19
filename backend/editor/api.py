"""API của trình dựng: /api/editor/...

Mọi yêu cầu thay đổi dữ liệu cần header X-CML: 1 (giống /api/jobs), nên trang web lạ
không gửi được. Mở /docs trên máy chủ để bấm thử từng API.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from ..media import MediaError
from .core import CommandError, Locked, VersionConflict
from .looks import LOOKS
from .service import EditorService, load_templates


def require_cml(x_cml: str | None = Header(default=None)) -> None:
    if x_cml != "1":
        raise HTTPException(403, "Thiếu header X-CML: 1.")


class NewProject(BaseModel):
    name: str
    width: int = 1080
    height: int = 1920
    fps: int = 30


class CommandIn(BaseModel):
    command: dict[str, Any]
    actor: str = "user"
    expected_version: int | None = None


class Owner(BaseModel):
    owner: str


class PathIn(BaseModel):
    path: str


class ToolIn(BaseModel):
    kind: str
    options: dict[str, Any] = {}


class CaptionsIn(BaseModel):
    job_id: str
    lang: str | None = None
    offset: float = 0.0
    actor: str = "user"


class BeatCutIn(BaseModel):
    music_id: str
    source_ids: list[str]
    beats_per_cut: int = 2
    min_clip: float = 0.3
    look: str | None = None
    max_seconds: float = 60
    sensitivity: int = 60
    actor: str = "user"


class TemplateIn(BaseModel):
    template: str
    source_ids: list[str]
    music_id: str | None = None
    actor: str = "user"


def make_router(svc: EditorService) -> APIRouter:
    r = APIRouter(prefix="/api/editor", tags=["editor"])
    write = [Depends(require_cml)]

    def fail(e: Exception):
        if isinstance(e, VersionConflict):
            raise HTTPException(409, {"error": str(e), "current_version": e.current})
        if isinstance(e, Locked):
            raise HTTPException(423, {"error": str(e), "owner": e.owner})
        if isinstance(e, (CommandError, KeyError, ValueError, MediaError)):
            raise HTTPException(400, {"error": str(e).strip("'\"")})
        raise e

    # ---------- media ----------
    @r.get("/media")
    def list_media():
        return [m.__dict__ for m in svc.store.list_media()]

    @r.post("/media/upload", dependencies=write)
    def upload(file: UploadFile = File(...)):
        suffix = Path(file.filename or "file").suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=svc.media_dir) as tmp:
            shutil.copyfileobj(file.file, tmp)
        try:
            return svc.import_file(Path(tmp.name), file.filename, move=True).__dict__
        except Exception as e:  # noqa: BLE001
            Path(tmp.name).unlink(missing_ok=True)
            fail(e)

    @r.post("/media/import-path", dependencies=write)
    def import_path(body: PathIn):
        """Cho agent: nhập một file đang có trên máy này theo đường dẫn đầy đủ."""
        try:
            return svc.import_file(Path(body.path)).__dict__
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/media/{media_id}/tool", dependencies=write)
    def run_tool(media_id: str, body: ToolIn):
        """Chạy worker có sẵn trên file: denoise, subtitles (mode=transcribe), voiceover. Trả về mã việc."""
        try:
            return {"job_id": svc.submit_tool(body.kind, media_id, body.options)}
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/media/from-job/{job_id}", dependencies=write)
    def from_job(job_id: str):
        try:
            return svc.import_job_result(job_id).__dict__
        except Exception as e:  # noqa: BLE001
            fail(e)

    # ---------- project ----------
    @r.get("/projects")
    def projects():
        return svc.store.list_projects()

    @r.post("/projects", dependencies=write)
    def create(body: NewProject):
        p = svc.core.create_project(body.name, body.width, body.height, body.fps)
        return {"project": p.to_dict(), "version": 1}

    @r.get("/projects/{pid}")
    def get_project(pid: str, since_version: int | None = None):
        """Giao diện hỏi mỗi 1 giây; có since_version thì trả {changed:false} rất nhẹ."""
        try:
            head, latest = svc.store.head(pid)
            if since_version is not None and since_version == head:
                return {"changed": False, "version": head}
            p, v = svc.core.get(pid)
            return {"changed": True, "project": p.to_dict(), "version": v, "can_undo": v > 1,
                    "can_redo": v < latest, "duration": round(p.duration, 3),
                    "locked_by": svc.store.lock_owner(pid)}
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.get("/projects/{pid}/history")
    def history(pid: str):
        return svc.store.history(pid)

    @r.post("/projects/{pid}/commands", dependencies=write)
    def command(pid: str, body: CommandIn):
        try:
            return svc.core.apply(pid, body.command, body.actor, body.expected_version)
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/undo", dependencies=write)
    def undo(pid: str):
        try:
            return {"version": svc.core.undo(pid)}
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/redo", dependencies=write)
    def redo(pid: str):
        try:
            return {"version": svc.core.redo(pid)}
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/lock", dependencies=write)
    def lock(pid: str, body: Owner):
        if not svc.core.lock(pid, body.owner):
            raise HTTPException(423, {"error": "Project đang được agent khác sửa.", "owner": svc.store.lock_owner(pid)})
        return {"ok": True, "ttl_seconds": svc.core.lock_ttl}

    @r.post("/projects/{pid}/unlock", dependencies=write)
    def unlock(pid: str, body: Owner):
        svc.core.unlock(pid, body.owner)
        return {"ok": True}

    # ---------- AI trên timeline ----------
    @r.post("/projects/{pid}/beat-cut", dependencies=write)
    def beat_cut(pid: str, body: BeatCutIn):
        try:
            return svc.beat_cut(pid, **body.model_dump())
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/template", dependencies=write)
    def template(pid: str, body: TemplateIn):
        try:
            return svc.apply_template(pid, **body.model_dump())
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/captions-from-job", dependencies=write)
    def captions(pid: str, body: CaptionsIn):
        try:
            return svc.captions_from_job(pid, **body.model_dump())
        except Exception as e:  # noqa: BLE001
            fail(e)

    @r.post("/projects/{pid}/export", dependencies=write)
    def export(pid: str):
        """Xuất MP4 trong hàng đợi việc chung. Theo dõi bằng GET /api/jobs/{job_id}."""
        try:
            svc.core.get(pid)
        except Exception as e:  # noqa: BLE001
            fail(e)
        job_id, _ = svc.jobs.new_job_dir()
        svc.jobs.submit(job_id, "render", {"project_id": pid})
        return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}

    @r.get("/templates")
    def templates():
        return load_templates()

    @r.get("/looks")
    def looks():
        return sorted(LOOKS)

    return r
