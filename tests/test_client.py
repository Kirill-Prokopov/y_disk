"""Smoke test against the real Yandex.Disk API.

Run with: python tests/test_client.py
Uses the token named Y_DISK_FINAM_MARKET_DATA_LOADER from ~/Code/.secrets.

This never deletes anything remotely (the package has no delete method);
it only creates a uniquely-named scratch folder to upload/download through.
"""
import shutil
import tempfile
import time
from pathlib import Path

from y_disk import YandexDiskClient, load_token

TOKEN_NAME = "Y_DISK_FINAM_MARKET_DATA_LOADER"


def main() -> None:
    token = load_token(TOKEN_NAME)
    disk = YandexDiskClient(token)

    try:
        info = disk.get_disk_info()
        print(f"disk ok: {info['used_space']}/{info['total_space']} bytes used")
    except Exception as e:
        print(f"get_disk_info unavailable for this token's scope ({e}); continuing")

    run_folder = f"app:/y_disk_pkg_smoke_test/{int(time.time())}"

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # --- single file upload/download + unique naming -----------------
        local_file = tmp_dir / "hello.txt"
        local_file.write_text("hello from y_disk\n")

        remote_path_1 = disk.upload_file(str(local_file), f"{run_folder}/hello.txt")
        assert remote_path_1 == f"{run_folder}/hello.txt", remote_path_1

        remote_path_2 = disk.upload_file(str(local_file), f"{run_folder}/hello.txt")
        assert remote_path_2 != remote_path_1, "second upload should get a unique name"
        assert disk.exists(remote_path_1) and disk.exists(remote_path_2)
        print(f"unique naming ok: {remote_path_1!r} vs {remote_path_2!r}")

        downloaded = tmp_dir / "hello_downloaded.txt"
        disk.download_file(remote_path_1, str(downloaded))
        assert downloaded.read_text() == local_file.read_text()
        print("single file upload/download roundtrip ok")

        # --- folder upload/download ---------------------------------------
        local_folder = tmp_dir / "folder_src"
        (local_folder / "sub").mkdir(parents=True)
        (local_folder / "a.txt").write_text("file a\n")
        (local_folder / "sub" / "b.txt").write_text("file b\n")

        remote_folder = disk.upload_folder(str(local_folder), f"{run_folder}/folder")
        listing = {item.name for item in disk.list_dir(remote_folder)}
        assert listing == {"a.txt", "sub"}, listing

        downloaded_folder = tmp_dir / "folder_dst"
        disk.download_folder(remote_folder, str(downloaded_folder))
        assert (downloaded_folder / "a.txt").read_text() == "file a\n"
        assert (downloaded_folder / "sub" / "b.txt").read_text() == "file b\n"
        print("folder upload/download roundtrip ok")

        print("\nremote tree left behind for inspection (nothing was deleted):")
        disk.print_tree(run_folder)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
