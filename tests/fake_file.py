"""内存版 file 服务，供路由测试使用。"""

from __future__ import annotations

import hashlib
import secrets

from app.file_client import FetchedFile, FileClientError, FileNotFound, UploadedFile


class FakeFileService:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.fail_upload = False
        self.fail_fetch = False
        self.health_ok = True

    def clear(self) -> None:
        self.objects.clear()
        self.deleted.clear()
        self.fail_upload = False
        self.fail_fetch = False
        self.health_ok = True

    def upload_image(self, token: str, *, content: bytes, content_type: str, filename: str) -> UploadedFile:
        if self.fail_upload:
            raise FileClientError(503, "storage_unavailable", "fake storage down")
        file_id = "f_" + secrets.token_urlsafe(16)
        sha = hashlib.sha256(content).hexdigest()
        self.objects[file_id] = {
            "content": content,
            "content_type": content_type,
            "sha256": sha,
            "filename": filename,
        }
        return UploadedFile(file_id=file_id, content_type=content_type, size_bytes=len(content), sha256=sha)

    def fetch_file(self, token: str, file_id: str) -> FetchedFile:
        if self.fail_fetch:
            raise FileClientError(503, "storage_unavailable", "fake storage down")
        record = self.objects.get(file_id)
        if record is None:
            raise FileNotFound()
        return FetchedFile(
            content=record["content"],
            content_type=record["content_type"],
            etag=record["sha256"],
        )

    def delete_file(self, token: str, file_id: str) -> None:
        self.deleted.append(file_id)
        self.objects.pop(file_id, None)

    def health(self) -> bool:
        return self.health_ok

    def close(self) -> None:
        return
