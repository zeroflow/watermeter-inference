"""
Server detection module for training scripts.
Syncs training data from server if running on a client machine.
"""
import re
import subprocess

SERVER_IP = "192.168.4.35"
SERVER_USER = "thomas"
SERVER_BASE_PATH = "/var/ml/openvino-notebooks/watermeter"
LOCAL_BASE_PATH = "/home/thomas/watermeter-inference"

RSYNC_EXCLUDES = [
    ".cache",
    ".git",
    "dataset",
    "dataset_100",
    "venv",
    "ov_model",
]


def get_all_ips():
    """Get all IP addresses for this machine (handles multiple adapters/docker)."""
    result = subprocess.run(["ip", "addr"], capture_output=True, text=True)
    ips = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
    return [ip for ip in ips if ip != '127.0.0.1']


def is_server():
    """Check if current machine is the server."""
    return SERVER_IP in get_all_ips()


def sync_folder(folder_name: str):
    """Sync a folder from server to local machine."""
    remote_path = f"{SERVER_USER}@{SERVER_IP}:{SERVER_BASE_PATH}/{folder_name}"
    local_path = f"{LOCAL_BASE_PATH}/{folder_name}"

    cmd = ["rsync", "-avz", "--progress"]
    for exclude in RSYNC_EXCLUDES:
        cmd.extend(["--exclude", exclude])
    cmd.extend([remote_path, local_path])

    print(f"Syncing {folder_name} from server...")
    print(f"  {' '.join(cmd)}")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Warning: rsync for {folder_name} exited with code {result.returncode}")
    else:
        print(f"Successfully synced {folder_name}")


def handle():
    """
    Check if running on server or client.
    If client, sync training data before continuing.
    """
    all_ips = get_all_ips()
    print(f"Local IPs: {all_ips}")
    print(f"Server IP: {SERVER_IP}")

    if is_server():
        print("Running on server - no sync needed")
        return

    print("Running on client - syncing training data from server...")
    print("=" * 60)

    # Sync both arrows and digits folders
    sync_folder("arrows")
    sync_folder("digits")

    print("=" * 60)
    print("Sync complete, continuing with training...")
    print()


if __name__ == "__main__":
    all_ips = get_all_ips()
    print(f"All local IPs: {all_ips}")
    print(f"Server IP: {SERVER_IP}")
    print(f"Is server: {is_server()}")
