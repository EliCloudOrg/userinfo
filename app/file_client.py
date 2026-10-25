"""调用 file 服务的薄客户端。

所有请求都转发**调用者自己的** Bearer 令牌；本服务不持有服务间密钥。
上传强制 ``visibility=public`` + ``ttl_seconds=0``，并校验 file 真的按永久 public
处理（缺 ``permanent`` 的旧版本视为上游违约，fail closed）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from .constants import FILE_ID_RE


@dataclass(frozen=True)
class UploadedFile:
    file_id: str
    content_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FetchedFile:
    content: bytes
    content_type: str
    etag: str


class FileClientError(Exception):
    def __init__(self, status_code: int, error: str, description: str) -> None:
        super().__init__(f"{status_code} {error}: {description}")
        self.status_code = status_code
        self.error = error
        self.description = description


class FileServiceUnavailable(FileClientError):
    def __init__(self, description: str = "文件服务暂时不可用") -> None:
        super().__init__(503, "file_service_unavailable", description)


class FileServiceContractError(FileClientError):
    def __init__(self, description: str) -> None:
        super().__init__(502, "file_service_error", description)


class FileNotFound(FileClientError):
    def __init__(self) -> None:
        super().__init__(404, "image_not_found", "图片不存在")


class FileClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ 上传
    def upload_image(
        self,
        token: str,
        *,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> UploadedFile:
        try:
            response = self._client.post(
                "/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (filename, content, content_type)},
                data={"visibility": "public", "ttl_seconds": "0"},
            )
        except httpx.HTTPError as exc:
            raise FileServiceUnavailable(f"连接文件服务失败：{type(exc).__name__}") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise FileServiceContractError("文件服务返回了无法解析的响应") from exc

        if not isinstance(body, dict):
            raise FileServiceContractError("文件服务返回了非对象响应")
        if body.get("visibility") != "public" or body.get("permanent") is not True:
            raise FileServiceContractError("文件服务没有按 public + 永久保存图片，请先升级 file 服务")
        if body.get("content_type") != content_type:
            raise FileServiceContractError("文件服务保存的 Content-Type 与上传不一致")
        file_id = str(body.get("id") or "")
        if not FILE_ID_RE.match(file_id):
            raise FileServiceContractError("文件服务返回的 id 格式不合法")
        sha256 = str(body.get("sha256") or "")
        if len(sha256) != 64:
            raise FileServiceContractError("文件服务返回的 sha256 不合法")
        try:
            size = int(body.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise FileServiceContractError("文件服务返回的 size_bytes 不合法") from exc
        return UploadedFile(file_id=file_id, content_type=content_type, size_bytes=size, sha256=sha256)

    # ------------------------------------------------------------------ 读取
    def fetch_file(self, token: str, file_id: str) -> FetchedFile:
        try:
            response = self._client.get(
                f"/v1/files/{file_id}/content",
                params={"direct": "0"},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise FileServiceUnavailable(f"连接文件服务失败：{type(exc).__name__}") from exc

        if response.status_code in (404, 410):
            raise FileNotFound()
        if response.status_code < 200 or response.status_code >= 300:
            raise self._error_from(response)

        content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
        etag = response.headers.get("etag", "").strip().strip('"')
        if not etag:
            etag = hashlib.sha256(response.content).hexdigest()
        return FetchedFile(content=response.content, content_type=content_type, etag=etag)

    # ------------------------------------------------------------------ 删除
    def delete_file(self, token: str, file_id: str) -> None:
        try:
            response = self._client.delete(
                f"/v1/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise FileServiceUnavailable(f"连接文件服务失败：{type(exc).__name__}") from exc
        if response.status_code in (200, 202, 204, 404):
            return
        raise self._error_from(response)

    # ------------------------------------------------------------------ 健康
    def health(self) -> bool:
        try:
            response = self._client.get("/healthz", timeout=min(self.timeout, 3.0))
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _error_from(response: httpx.Response) -> FileClientError:
        error = "file_service_error"
        description = f"文件服务返回 {response.status_code}"
        try:
            body: Any = response.json()
            if isinstance(body, dict):
                error = str(body.get("error") or error)
                description = str(body.get("error_description") or description)
        except ValueError:
            pass
        return FileClientError(response.status_code, error, description)
