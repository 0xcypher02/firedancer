#!/usr/bin/env python3.11
import os
import tempfile
import sys
import time
import shutil
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from plumbum import local, TF, BG

script_directory = os.path.dirname(os.path.abspath(__file__))
entrypoint = "147.28.184.21"
entrypoint_port = 8000
# entrypoint = "entrypoint2.testnet.solana.com"

def line_in_log(pattern, log_path):
    return local["grep"]["-q", "-P", pattern, log_path] & TF


def current_slot():
    solana_cmd = local["solana"]["-u", "m", "epoch-info", "--output", "json"]
    jq_cmd = local["jq"][".absoluteSlot"]
    return int((solana_cmd | jq_cmd)())


def check_fddev_path(script_directory = script_directory):
    fddev_path = os.path.join(script_directory, "build", "native", "gcc", "bin")
    os.environ["PATH"] = f"{fddev_path}:{os.environ['PATH']}"


def download_snapshot(url, output_dir, num_tries=10):
    # we try multiple times because the endpoint can be flaky
    for _ in range(num_tries):
        response = requests.get(url, allow_redirects=False)
        if response.status_code != 303:
            raise RuntimeError(f"unexpected status code: {response.status_code}")
        filename = response.headers['location'][1:] # chop off the leading '/'

        response = requests.get(url, allow_redirects=True)
        if response.status_code == 200:
            filename = os.path.join(output_dir, filename)
            with open(filename, 'wb') as f:
                f.write(response.content)
            return filename
        time.sleep(1)

    raise RuntimeError(f"failed after {num_tries} tries to download {url}")


def write_toml(directory, entrypoint, entrypoint_port, snapshot, incremental):
    with open(os.path.join(directory, "fddev.toml"), "w") as f:
        f.write(
            f"""
[gossip]
    port = 8730
[tiles]
    [tiles.gossip]
        entrypoints = ["{entrypoint}"]
        peer_ports = [{entrypoint_port}]
        gossip_listen_port = 8730
    [tiles.repair]
        repair_intake_listen_port = 8731
        repair_serve_listen_port = 8732
    [tiles.replay]
        snapshot = "{snapshot}"
        incremental = "{incremental}"
        tpool_thread_count = 10
        funk_sz_gb = 500
        funk_txn_max = 1024
        funk_rec_max = 600000000
[log]
  path = "fddev.log"
  level_stderr = "NOTICE"
[development]
    topology = "firedancer"
""")


def start_fddev(work_directory, fddev_path=os.path.join(script_directory, "build", "native", "gcc", "bin", "fddev"), use_gdb=True):
    log_path = os.path.join(work_directory, "fddev.log")
    config_path = os.path.join(work_directory, "fddev.toml")
    gdb_path = os.path.join(work_directory, "gdb.log")
    fddev_args = [ "--log-path",
                   os.path.realpath(log_path),
                   "--config", os.path.realpath(config_path),
                   "--no-sandbox",
                   "--no-clone",
                   "--no-solana-labs" ]
    gdb_args = [ "-ex", "set confirm off",
                 "-ex", "set pagination off",
                 "-ex", "run",
                 "-ex", "bt",
                 "-ex", "quit" ]
    if use_gdb:
        tee = local["tee"][os.path.realpath(gdb_path)]
        cmd = local["sudo"][ "gdb", *gdb_args, "--args", fddev_path, *fddev_args ]
        return ( cmd | tee ) & BG(stdout=sys.stdout, stderr=sys.stderr)
    else:
        return local["fddev"][ *fddev_args ] & BG(stdout=sys.stdout, stderr=sys.stderr)


def get_time(tz = ZoneInfo('America/Chicago')):
    return datetime.now(tz).strftime("%Y-%m-%d-%H:%M:%S")


def copy_data_to_upload_dir(data_dir, new_dir, upload_dir='~/upload-to-gcs'):
    new_dir = os.path.join(data_dir, new_dir)
    # Create the new directory
    os.makedirs(new_dir, exist_ok=False)

    # Copy all contents except the new directory itself to the new directory
    print(f'listdir {os.listdir(data_dir)}')
    for item in os.listdir(data_dir):
        print(item)
        if os.path.join(data_dir, item) != new_dir:
            if os.path.isdir(item):
                print(f'copying directory {item}')
                shutil.copytree(os.path.join(data_dir, item), os.path.join(new_dir, item))
            else:
                print(f'copying file {item}')
                shutil.copy2(os.path.join(data_dir, item), new_dir)

    # Move the new directory to the desired location
    destination = os.path.expanduser(upload_dir)
    shutil.move(new_dir, destination)


def loop(fddev, tmp_path):
    caught_up = False
    t0 = time.time()
    while True:
        if fddev.poll():
            fddev.proc.kill()
            print("*** FDDEV CRASH ***")
            copy_data_to_upload_dir(tmp_path, f"{current_slot()}-crash-{get_time()}")
            return

        if line_in_log("ERR.*incremental accounts_hash [^ ]+ != [^ ]+$", os.path.join(tmp_path, "fddev.log")):
            print("*** INCREMENTAL ACCOUNTS_HASH MISMATCH ")
            copy_data_to_upload_dir(tmp_path, f"{current_slot()}-incremental-accounts-hash-mismatch-{get_time()}")
            return

        if line_in_log("Bank hash mismatch", os.path.join(tmp_path, "fddev.log")):
            # TODO: change fddev to make locating the bad slot easier
            bad_slot = (local['grep']["Bank hash mismatch", os.path.join(tmp_path, "fddev.log")] |
                        local['awk']['NR==1 {for (i=1; i<=NF; i++) if ($i == "slot:") {gsub(/[^0-9]/, "", $(i+1)); print $(i+1); exit}}'])()
            print("*** BANK HASH MISMATCH {bad_slot} ***")
            copy_data_to_upload_dir(tmp_path, f"{bad_slot}-bank-hash-mismatch-{get_time()}")
            return

        if line_in_log("caught up: 1", os.path.join(tmp_path, "fddev.log")):
            caught_up = True
            return

        if not caught_up and time.time()-t0>3600:
            print("fddev failed to catch up")
            return
        
        time.sleep(1)

def main():
    # check_fddev_path(script_directory)
    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as tmp:
        print(tmp)
        print('downloading snapshot')
        snapshot = download_snapshot(f"http://{entrypoint}:8899/snapshot.tar.bz2", tmp)
        print('downloading incremental snapshot')
        incremental = download_snapshot(f"http://{entrypoint}:8899/incremental-snapshot.tar.bz2", tmp)
        # snapshot = ""
        # incremental = ""
        print('starting fddev')
        write_toml(tmp, entrypoint, entrypoint_port, snapshot, incremental)
        fddev = start_fddev(tmp)
        loop(fddev, tmp)

if __name__ == "__main__":
    main()