import os
from minio import Minio
from minio.error import S3Error
import pytest
from cisei_lib.io.minio_access import MinioAccess

@pytest.mark.minio
def test_minio_auth_basic():
    endpoint = os.environ["MINIO_ENDPOINT"].replace("http://", "").replace("https://", "").replace("9001","9000")
    client = Minio(
        endpoint,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.environ["MINIO_ENDPOINT"].startswith("https://")
    )

    bucket = os.environ["MINIO_BUCKET"]
    print(f"Testing MinIO authentication for bucket: {bucket}")

    try:
        exists = client.bucket_exists(bucket)
        if exists:
            print("✅ Connection and authentication successful.")
            print(f"Bucket '{bucket}' is accessible.")
        else:
            print("⚠️  Authentication succeeded, but bucket does not exist or cannot be accessed.")
    except S3Error as e:
        print("❌ MinIO error:", e)
    except Exception as e:
        print("❌ Connection failed:", e)


def minio_acess(monkeypatch):
    """Prepare environment and client."""
    # monkeypatch.setenv("MINIO_ENDPOINT", "http://10.32.13.11:9000")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://100.108.148.23:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "admin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "Inicio@123")
    monkeypatch.setenv("MINIO_BUCKET", "planapp")
    monkeypatch.setenv("MINIO_HOME_FOLDER", "home")
    monkeypatch.setenv("LOCAL_HOME_FOLDER", "/workspaces/planning_service/home")
    client = MinioAccess()
    return client


def test_upload_and_auto_remove(tmp_path, monkeypatch):
    minio_client = minio_acess(monkeypatch)
    """Upload a file and confirm local removal when remove_local=True."""
    test_file = tmp_path / "root/test_upload.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("MinIO test file")

    rel_key = "root/test_upload.txt"
    minio_client.local_home = tmp_path  # redirect local home to tmpdir

    minio_client.upload(rel_key)  # remove_local=True by default
    assert not test_file.exists(), "File should be deleted after upload"

    meta = minio_client.stat_object(rel_key)
    assert meta.object_name.endswith("test_upload.txt")

    # cleanup
    minio_client.remote_delete(rel_key)



def test_upload_without_removal(tmp_path, monkeypatch):
    minio_client = minio_acess(monkeypatch)
    """Upload and keep the file when remove_local=False."""
    test_file = tmp_path / "root/test_keep.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("Keep me")

    rel_key = "root/test_keep.txt"
    minio_client.local_home = tmp_path

    minio_client.upload(rel_key, remove_local=False)
    assert test_file.exists(), "File should remain when remove_local=False"

    # cleanup
    minio_client.remote_delete(rel_key)


def test_stat_and_delete(tmp_path, monkeypatch):
    minio_client = minio_acess(monkeypatch)

    # create local file
    f = tmp_path / "root/test_upload.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("Keep me")

    rel_key = "root/test_upload.txt"
    minio_client.local_home = tmp_path

    # upload first
    minio_client.upload(rel_key, remove_local=False)

    # now stat
    meta = minio_client.stat_object(rel_key)
    assert meta and meta.object_name.endswith("test_upload.txt")

    # delete
    minio_client.remote_delete(rel_key)
    keys = minio_client.remote_list("root")
    assert "test_upload.txt" not in "".join(keys)


def test_download(tmp_path, monkeypatch):
    minio_client = minio_acess(monkeypatch)

    # create local file
    f = tmp_path / "root/test_upload.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("Keep me")

    rel_key = "root/test_upload.txt"
    minio_client.local_home = tmp_path

    # upload before downloading
    minio_client.upload(rel_key, remove_local=False)

    # now download
    local_path = minio_client.download(rel_key)
    assert local_path.exists()
    assert "Keep me" in local_path.read_text()

def test_remote_list(tmp_path, monkeypatch):
    minio_client = minio_acess(monkeypatch)
    """Verify that remote_list() correctly lists objects under home prefix."""
    # Create and upload two test files
    files = ["root/test_list_1.txt", "root/test_list_2.txt"]
    minio_client.local_home = tmp_path
    for name in files:
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"Content for {name}")
        minio_client.upload(name, remove_local=True)

    # List remotely
    keys = minio_client.remote_list("root")
    for name in files:
        assert any(name in k for k in keys), f"{name} missing in remote list"

    # Cleanup
    for name in files:
        minio_client.remote_delete(name)










