"""Raw document storage (MinIO / S3-compatible), content-addressed by SHA-256.

The same bytes are stored once across runs and cities; the database keeps the object key.
"""

import io
import threading
from typing import Protocol
from .config import ArtifactSettings

_EXTENSIONS = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt"}

class ArtifactStore(Protocol):
    def put(self, data: bytes, sha256: str, content_type: str) -> str: ...
    def check(self) -> None: ...

def object_key(sha256: str, content_type: str) -> str:
    ext = _EXTENSIONS.get(content_type.split(";")[0].strip().lower(), ".bin")
    return f"sha256/{sha256[:2]}/{sha256}{ext}"

class MinioArtifactStore:
    def __init__(self, settings: ArtifactSettings, client=None) -> None:
        self.settings = settings
        self.bucket = settings.bucket
        if client is None:
            import urllib3
            from minio import Minio
            http = urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=3, read=60),
                retries=urllib3.Retry(total=2, backoff_factor=0.2,
                                      status_forcelist=[500, 502, 503, 504]))
            client = Minio(settings.endpoint, access_key=settings.access_key,
                           secret_key=settings.secret_key, secure=settings.secure,
                           http_client=http)
        self.client = client
        self._ready = False
        self._lock = threading.Lock()

    def _ensure_bucket(self) -> None:
        with self._lock:
            if not self._ready:
                if not self.client.bucket_exists(self.bucket):
                    self.client.make_bucket(self.bucket)
                self._ready = True

    def check(self) -> None:
        """Raise if the store is unreachable or the bucket can't be created."""
        self._ensure_bucket()

    def put(self, data: bytes, sha256: str, content_type: str) -> str:
        from minio.error import S3Error
        self._ensure_bucket()
        key = object_key(sha256, content_type)
        try:
            self.client.stat_object(self.bucket, key)
            return f"{self.bucket}/{key}"  # already stored (content-addressed)
        except S3Error as exc:
            if exc.code not in ("NoSuchKey", "NoSuchObject", "ResourceNotFound"):
                raise
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data),
                               content_type=content_type.split(";")[0].strip()
                               or "application/octet-stream")
        return f"{self.bucket}/{key}"

def build_artifact_store(settings) -> ArtifactStore | None:
    if not settings.artifacts.enabled:
        return None
    return MinioArtifactStore(settings.artifacts)
