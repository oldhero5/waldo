import io
from pathlib import Path

from minio import Minio

from lib.config import settings

_client: Minio | None = None
_inference_lifecycle_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def ensure_bucket() -> None:
    client = get_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def upload_file(object_name: str, file_path: str | Path) -> str:
    client = get_client()
    file_path = Path(file_path)
    client.fput_object(settings.minio_bucket, object_name, str(file_path))
    return object_name


def upload_bytes(object_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    client = get_client()
    client.put_object(
        settings.minio_bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_name


def download_file(object_name: str, file_path: str | Path) -> Path:
    client = get_client()
    file_path = Path(file_path)
    client.fget_object(settings.minio_bucket, object_name, str(file_path))
    return file_path


def delete_object(object_name: str) -> None:
    get_client().remove_object(settings.minio_bucket, object_name)


def ensure_inference_lifecycle() -> None:
    """Expire orphaned inputs without changing retention of source/training media."""
    from minio.commonconfig import ENABLED, Filter
    from minio.error import S3Error
    from minio.lifecycleconfig import (
        AbortIncompleteMultipartUpload,
        Expiration,
        LifecycleConfig,
        NoncurrentVersionExpiration,
        Rule,
    )

    global _inference_lifecycle_client
    client = get_client()
    if _inference_lifecycle_client is client:
        return
    try:
        policy = client.get_bucket_lifecycle(settings.minio_bucket)
        rules = policy.rules if policy is not None else []
    except S3Error as error:
        if error.code != "NoSuchLifecycleConfiguration":
            raise
        rules = []
    desired = Rule(
        ENABLED,
        rule_filter=Filter(prefix="inference/"),
        rule_id="waldo-inference-inputs",
        expiration=Expiration(days=2),
        noncurrent_version_expiration=NoncurrentVersionExpiration(noncurrent_days=2),
        abort_incomplete_multipart_upload=AbortIncompleteMultipartUpload(days_after_initiation=2),
    )
    if desired not in rules:
        rules = [rule for rule in rules if rule.rule_id != desired.rule_id]
        client.set_bucket_lifecycle(settings.minio_bucket, LifecycleConfig([*rules, desired]))
    _inference_lifecycle_client = client


def get_download_url(object_name: str) -> str:
    """Return a download URL routed through the API (avoids MinIO hostname leaking to browsers)."""
    from datetime import UTC, datetime, timedelta
    from urllib.parse import quote

    from jose import jwt

    token = jwt.encode(
        {"type": "download", "object": object_name, "exp": datetime.now(UTC) + timedelta(minutes=15)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return f"/api/v1/download/{quote(object_name, safe='/')}?token={token}"


def get_presigned_url(object_name: str, expires_hours: int = 1) -> str:
    """Generate a presigned MinIO URL (for internal/direct access)."""
    from datetime import timedelta

    client = get_client()
    return client.presigned_get_object(
        settings.minio_bucket,
        object_name,
        expires=timedelta(hours=expires_hours),
    )


def list_objects(prefix: str = "") -> list[str]:
    client = get_client()
    return [obj.object_name for obj in client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)]
