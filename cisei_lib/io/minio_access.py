from pathlib import Path
import os
from minio import Minio
from contextlib import contextmanager
from datetime import timedelta


class MinioAccess:
    """
    Minimal MinIO access layer with automatic local/remote mapping.
    Remote keys and local paths are always relative to home folders.
    """

    def __init__(self):

     

        try:
            raw_endpoint = os.environ["MINIO_ENDPOINT"].strip().rstrip("/")
            is_tls = raw_endpoint.startswith("https://") # Boolean for TLS

            endpoint = (
                raw_endpoint.replace("http://", "")
                            .replace("https://", "")
                            .replace("9001", "9000")
                            .strip("/")
            )

            self.endpoint = endpoint
            self.s3_endpoint = f"{'https' if is_tls else 'http'}://{endpoint}"

            self.access_key = os.environ["MINIO_ACCESS_KEY"]
            self.secret_key = os.environ["MINIO_SECRET_KEY"]
        
            self.bucket = os.environ["MINIO_BUCKET"]
            self.remote_home = os.environ["MINIO_HOME_FOLDER"].strip().rstrip("/")
            self.local_home = Path(os.environ["LOCAL_HOME_FOLDER"])
            self.local_home.mkdir(parents=True, exist_ok=True)

            self.client = Minio(
                endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=is_tls,
            )
        except Exception as e:
            print('MINIO ENV VARIABLE:', os.environ)
            print(e)
            exit(1)


    def _map_local(self, relative_key: str) -> Path:
        return self.local_home / relative_key

    def _map_remote(self, relative_key: str) -> str:
        return f"{self.remote_home}/{relative_key}"

    def download(self, relative_key: str, mode: str = "cache") -> Path:
        """
        Download object from MinIO using home-based mapping.

        Args:
            relative_key: Path relative to MINIO_HOME_FOLDER.
            mode:
                - "cache"   → use local file if it exists, otherwise download
                - "refresh" → always download and overwrite local file

        Returns:
            Path to the local file.
        """
        local_path = self._map_local(relative_key)

        if mode == "cache" and local_path.exists():
            # print(f'from cache {local_path}')
            return local_path

        remote_key = self._map_remote(relative_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        self.client.fget_object(self.bucket, remote_key, str(local_path))
        return local_path


    def upload(self, relative_key: str, remove_local: bool = True) -> None:
        """Upload local file using home-based mapping.
        By default, deletes the local file after a successful upload.
        """
        local_path = self._map_local(relative_key)
        remote_key = self._map_remote(relative_key)

        if not local_path.exists():
            raise FileNotFoundError(local_path)

        print(f"⬆️  Uploading {local_path} → {remote_key}")
        self.client.fput_object(self.bucket, remote_key, str(local_path))

        if remove_local:
            try:
                local_path.unlink()
                print(f"🧹  Removed local file {local_path}")
            except Exception as e:
                print(f"[Warning] Could not remove {local_path}: {e}")

    def remote_list(self, relative_prefix: str = "") -> list[str]:
        """List remote objects under the user's home folder."""
        prefix = f"{self.remote_home}/{relative_prefix}".rstrip("/")
        objs = self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
        return [obj.object_name for obj in objs]

    def remote_delete(self, relative_key: str) -> None:
        """Delete a remote object under the user's home folder."""
        remote_key = f"{self.remote_home}/{relative_key}"
        print(f"🗑️  Deleting remote {remote_key}")
        self.client.remove_object(self.bucket, remote_key)
    
    def stat_object(self, relative_key: str):
        """Return object metadata under the user's home folder."""
        remote_key = self._map_remote(relative_key)
        return self.client.stat_object(self.bucket, remote_key)

    def get_object_url(self, relative_key, expires=timedelta(minutes=5)):
        remote_key = self._map_remote(relative_key)
        
        url = self.client.presigned_get_object(
            self.bucket,
            remote_key,
            expires=expires,
        )

        return url









