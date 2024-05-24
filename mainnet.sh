#!/bin/bash
set -euxo pipefail
shopt -s extglob
IFS=$'\n\t'

# to get coredumps for coredumpctl
sudo ulimit -c unlimited

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# create temporary files in the user's home directory because it's likely to be on a large disk
TMPDIR=$(mktemp --directory --tmpdir="$HOME" tmp-test-tvu-testnet.XXXXXX)
cd $TMPDIR

cleanup() {
  sudo killall fddev || true
  fddev configure fini all >/dev/null 2>&1 || true
  rm -rf "$TMPDIR"
}

trap cleanup EXIT SIGINT SIGTERM
sudo killall fddev || true

# if fddev is not on path then use the one in the home directory
if ! command -v fddev > /dev/null; then
  PATH="$SCRIPT_DIR/build/native/gcc/bin":$PATH
fi

# Use our RPC node as the entrypoint because it's faster than the public entrypoints
ENTRYPOINT=emfr-ccn-solana-mainnet-api28.jumpisolated.com
#ENTRYPOINT=entrypoint3.mainnet-beta.solana.com

#snapshot=$(curl -I "http://$ENTRYPOINT:8899/snapshot.tar.bz2" | grep location | cut -d/ -f2 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
#if [ ! -f ~/snapshots/$snapshot ]; then
#    echo "File ~/snapshots/$snapshot does not exist. Downloading..."
#    mkdir -p ~/snapshots
#    # we download then move the snapshot so only successfully completed snapshots are in ~/snapshots
#    wget -q --trust-server-names -P ~/ "http://$ENTRYPOINT:8899/snapshot.tar.bz2"
#    mv ~/$snapshot ~/snapshots
#else
#    echo "File $snapshot already exists. No need to download."
#fi

download_snapshot() {
  local url=$1
  local num_tries=${2:-10}
  local s
  for _ in $(seq 1 $num_tries); do
    s=$(curl -s --max-redirs 0 $url)
    if ! wget -q --trust-server-names $url; then
      sleep 1
    else
      echo "${s:1}"
      return 0
    fi
  done

  echo "failed after $num_tries tries to wget $url"
  return 1
}

snapshot=$(download_snapshot http://$ENTRYPOINT:8899/snapshot.tar.bz2)
incremental=$(download_snapshot http://$ENTRYPOINT:8899/incremental-snapshot.tar.bz2)

ENTRYPOINT=$(dig +short emfr-ccn-solana-mainnet-api28.jumpisolated.com)
ENTRYPOINT_PORT=8000

echo "
[gossip]
    port = 8730
[tiles]
    [tiles.gossip]
        entrypoints = [\"$ENTRYPOINT\"]
        peer_ports = [$ENTRYPOINT_PORT]
        gossip_listen_port = 8730
    [tiles.repair]
        repair_intake_listen_port = 8731
        repair_serve_listen_port = 8732
    [tiles.replay]
        snapshot = \"$snapshot\"
        incremental = \"$incremental\"
        tpool_thread_count = 10
        funk_sz_gb = 500
        funk_txn_max = 1024
        funk_rec_max = 600000000
[log]
  path = \"fddev.log\"
  level_stderr = \"NOTICE\"
[development]
    topology = \"firedancer\"
" > fddev.toml

fddev --log-path $(readlink -f fddev.log) --config $(readlink -f fddev.toml) --no-sandbox --no-clone --no-solana-labs &
FDDEV_PID=$!
disown $FDDEV_PID

CAUGHT_UP=0
mkdir -p ~/upload-to-gcs
set +x
# Run for 2 hours
for i in $(seq 1 7200); do
  if ! kill -0 $FDDEV_PID 2>/dev/null; then
    echo "*** FDDEV CRASH ***"
    break
  fi
	   
  CURRENT_SLOT=$(solana -u m epoch-info --output json | jq .absoluteSlot)
  # if we have an incremental accounts hash mismatch, then fail
  if grep -P "ERR.*incremental accounts_hash [^ ]+ != [^ ]+$" $(readlink -f fddev.log); then
    echo "*** INCREMENTAL ACCOUNTS_HASH MISMATCH ***"
    NEW_DIR=$CURRENT_SLOT-incremental-accounts-hash-mismatch-$(TZ='America/Chicago' date "+%Y-%m-%d-%H:%M:%S")
    mkdir -p $NEW_DIR
    cp -r !($NEW_DIR) $NEW_DIR
    mv $NEW_DIR ~/upload-to-gcs
    break
  fi

  # if we have a bank hash mismatch, then fail
  if grep -q "Bank hash mismatch" $(readlink -f fddev.log); then
    BAD_SLOT=$( grep "Bank hash mismatch" fddev.log | awk 'NR==1 {for (i=1; i<=NF; i++) if ($i == "slot:") {gsub(/[^0-9]/, "", $(i+1)); print $(i+1); exit}}' )
    echo "*** BANK HASH MISMATCH $BAD_SLOT ***"
    NEW_DIR=$BAD_SLOT-bank-hash-mismatch-$(TZ='America/Chicago' date "+%Y-%m-%d-%H:%M:%S")
    mkdir -p $NEW_DIR
    cp -r !($NEW_DIR) $NEW_DIR
    mv $NEW_DIR ~/upload-to-gcs
    break
  fi

  if grep -q "caught up: 1"; then
    CAUGHT_UP=1
  fi

  if grep -q "^ERR" $(readlink -f fddev.log); then
    echo "*** ERROR ENCOUNTERED ***"
    NEW_DIR=$CURRENT_SLOT-error-$(TZ='America/Chicago' date "+%Y-%m-%d-%H:%M:%S")
    mkdir -p $NEW_DIR
    cp -r !($NEW_DIR) $NEW_DIR
    mv $NEW_DIR ~/upload-to-gcs
    break
  fi
  
  # if we have not caught up after one hour, then fail
  if [ $CAUGHT_UP -eq 0 ]; then
    if [ $i -eq 3600 ]; then
      echo "fddev failed to catch up"
      exit 1
    fi
  fi

  sleep 1
done
set -x

exit 0
