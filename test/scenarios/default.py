### Tests

assert_running("setup-secrets")
# Unused secrets should be inaccessible
succeed('[[ $(stat -c "%U:%G %a" /secrets/dummy) = "root:root 440" ]]')

assert_running("bitcoind")
machine.wait_until_succeeds("bitcoin-cli getnetworkinfo")
assert_matches("su operator -c 'bitcoin-cli getnetworkinfo' | jq", '"version"')
# Test RPC Whitelist
machine.wait_until_succeeds("su operator -c 'bitcoin-cli help'")
# Restating rpcuser & rpcpassword overrides privileged credentials
machine.fail(
    "bitcoin-cli -rpcuser=publicrpc -rpcpassword=$(cat /secrets/bitcoin-rpcpassword-public) help"
)
machine.wait_until_succeeds(
    log_has_string("bitcoind", "RPC User publicrpc not allowed to call method help")
)

assert_running("electrs")
machine.wait_for_open_port(4224)  # prometeus metrics provider
# Check RPC connection to bitcoind
machine.wait_until_succeeds(log_has_string("electrs", "NetworkInfo"))
assert_running("nginx")
# Stop electrs from spamming the test log with 'wait for bitcoind sync' messages
succeed("systemctl stop electrs")

assert_running("liquidd")
machine.wait_until_succeeds("elements-cli getnetworkinfo")
assert_matches("su operator -c 'elements-cli getnetworkinfo' | jq", '"version"')
succeed("su operator -c 'liquidswap-cli --help'")

assert_running("clightning")
assert_matches("su operator -c 'lightning-cli getinfo' | jq", '"id"')

assert_running("spark-wallet")
spark_auth = re.search("login=(.*)", succeed("cat /secrets/spark-wallet-login"))[1]
machine.wait_for_open_port(9737)
assert_matches(f"curl -s {spark_auth}@localhost:9737", "Spark")

assert_running("lightning-charge")
charge_auth = re.search("API_TOKEN=(.*)", succeed("cat /secrets/lightning-charge-env"))[1]
machine.wait_for_open_port(9112)
assert_matches(f"curl -s api-token:{charge_auth}@localhost:9112/info | jq", '"id"')

assert_running("nanopos")
machine.wait_for_open_port(9116)
assert_matches("curl localhost:9116", "tshirt")

assert_running("onion-chef")

# FIXME: use 'wait_for_unit' because 'create-web-index' always fails during startup due
# to incomplete unit dependencies.
# 'create-web-index' implicitly tests 'nodeinfo'.
machine.wait_for_unit("create-web-index")
machine.wait_for_open_port(80)
assert_matches("curl localhost", "nix-bitcoin")
assert_matches("curl -L localhost/store", "tshirt")

machine.wait_until_succeeds(log_has_string("bitcoind-import-banlist", "Importing node banlist"))
assert_no_failure("bitcoind-import-banlist")

# test that `systemctl status` can't leak credentials
assert_matches(
    "sudo -u electrs systemctl status clightning 2>&1 >/dev/null",
    "Failed to dump process list for 'clightning.service', ignoring: Access denied",
)
machine.succeed("grep -Fq hidepid=2 /proc/mounts")

### Additional tests

# Current time in µs
pre_restart = succeed("date +%s.%6N").rstrip()

# Sanity-check system by restarting all services
succeed("systemctl restart bitcoind clightning spark-wallet lightning-charge nanopos liquidd")

# Now that the bitcoind restart triggered a banlist import restart, check that
# re-importing already banned addresses works
machine.wait_until_succeeds(
    log_has_string(f"bitcoind-import-banlist --since=@{pre_restart}", "Importing node banlist")
)
assert_no_failure("bitcoind-import-banlist")

### Test lnd

succeed("systemctl stop nanopos lightning-charge spark-wallet clightning")
succeed("systemctl start lnd")
assert_matches("su operator -c 'lncli getinfo' | jq", '"version"')
assert_no_failure("lnd")

### Test loopd

succeed("systemctl start lightning-loop")
assert_matches("su operator -c 'loop --version'", "version")
# Check that lightning-loop fails with the right error, making sure
# lightning-loop can connect to lnd
machine.wait_until_succeeds(
    log_has_string("lightning-loop", "chain notifier RPC isstill in the process of starting")
)
