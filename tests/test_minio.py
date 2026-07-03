import os
from pathlib import Path
from minio import Minio
from minio.error import S3Error
from cisei_lib.io.minio_access import MinioAccess

def test_minio_auth_basic():
    print(os.environ["MINIO_ENDPOINT"])

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

def test_upload_and_auto_remove(tmp_path):
    minio_client = MinioAccess()
    """Upload a file and confirm local removal when remove_local=True."""
    test_file = tmp_path / "root/test_upload.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("MinIO test file")

    rel_key = "root/test_upload.txt"
    minio_client.local_home = tmp_path  # redirect local home to tmpdir

    minio_client.upload(rel_key, remove_local=False)
    assert test_file.exists(), "File should remain when remove_local=False"

    # now stat
    meta = minio_client.stat_object(rel_key)
    assert meta and meta.object_name.endswith("test_upload.txt")

    # delete
    minio_client.remote_delete(rel_key)
    keys = minio_client.remote_list("root")
    assert "test_upload.txt" not in "".join(keys)

    minio_client.upload(rel_key)  # remove_local=True by default
    assert not test_file.exists(), "File should be deleted after upload"

    # cleanup
    minio_client.remote_delete(rel_key)

def test_remote_list(tmp_path):
    minio_client = MinioAccess()
    """Verify that remote_list() correctly lists objects under home prefix."""
    # Create and upload two test files
    files = ["temp/test_list_1.txt", "temp/test_list_2.txt"]
    minio_client.local_home = tmp_path
    for name in files:
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"Content for {name}")
        minio_client.upload(name, remove_local=True)

    # List remotely
    keys = minio_client.remote_list("temp")
    for name in files:
        assert any(name in k for k in keys), f"{name} missing in remote list"

    # Cleanup
    for name in files:
        minio_client.remote_delete(name)










