# Input: MinIO/S3 endpoint/credentials + bytes
# Output: 对象存储 key 与 bytes 读写能力
# Pos: S3 客户端封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import boto3
from botocore.config import Config


@dataclass(frozen=True)
class S3Client:
    endpoint_url: str
    access_key: str
    secret_key: str
    region: str
    bucket: str

    def _client(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=Config(s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        c = self._client()
        try:
            c.head_bucket(Bucket=self.bucket)
        except Exception:
            c.create_bucket(Bucket=self.bucket)

    def put_bytes(self, *, key_prefix: str, url: str, content: bytes, content_type: str | None) -> str:
        self.ensure_bucket()
        sha = hashlib.sha256(content).hexdigest()
        safe = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        key = f"{key_prefix}/{safe}/{sha}.bin"
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client().put_object(Bucket=self.bucket, Key=key, Body=content, **extra)
        return key

    def get_bytes(self, key: str) -> bytes:
        res = self._client().get_object(Bucket=self.bucket, Key=key)
        return res["Body"].read()

    def delete_key(self, key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=key)
