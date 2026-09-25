import io
from pathlib import Path

import boto3
from botocore.client import Config

from app.core.config import settings


class StorageService:
    """Thin wrapper around an S3-compatible object store (MinIO in dev, S3 in prod).

    Only this class ever holds storage credentials; callers get presigned URLs,
    never raw keys/secrets.
    """

    def __init__(self) -> None:
        common_kwargs = dict(
            aws_access_key_id=settings.STORAGE_ACCESS_KEY,
            aws_secret_access_key=settings.STORAGE_SECRET_KEY,
            region_name=settings.STORAGE_REGION,
            config=Config(signature_version="s3v4"),
            use_ssl=settings.STORAGE_USE_SSL,
        )
        # Internal client: used for uploads/downloads/deletes from inside the docker network.
        self._client = boto3.client("s3", endpoint_url=settings.STORAGE_ENDPOINT_URL, **common_kwargs)
        # Presigning client: bound to the PUBLIC endpoint so the signature (which covers the
        # Host header) is valid when a browser on the host machine follows the URL. Swapping
        # the host in an already-signed URL would break SigV4 validation, so a second client
        # with the public endpoint baked in from the start is used instead.
        self._presign_client = boto3.client(
            "s3", endpoint_url=settings.STORAGE_PUBLIC_ENDPOINT_URL, **common_kwargs
        )
        self._bucket = settings.STORAGE_BUCKET

    def ensure_bucket(self) -> None:
        existing = self._client.list_buckets().get("Buckets", [])
        if not any(b["Name"] == self._bucket for b in existing):
            self._client.create_bucket(Bucket=self._bucket)

    def upload_fileobj(self, fileobj: io.IOBase, key: str, content_type: str | None = None) -> str:
        extra_args = {"ContentType": content_type} if content_type else {}
        self._client.upload_fileobj(fileobj, self._bucket, key, ExtraArgs=extra_args)
        return key

    def upload_file(self, local_path: str | Path, key: str, content_type: str | None = None) -> str:
        extra_args = {"ContentType": content_type} if content_type else {}
        self._client.upload_file(str(local_path), self._bucket, key, ExtraArgs=extra_args)
        return key

    def download_to_file(self, key: str, local_path: str | Path) -> None:
        self._client.download_file(self._bucket, key, str(local_path))

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def generate_presigned_get_url(self, key: str, expires_in: int | None = None) -> str:
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in or settings.SIGNED_URL_EXPIRE_SECONDS,
        )


storage_service = StorageService()
