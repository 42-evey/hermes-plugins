"""Evey Wallet Plugin — monitor crypto wallet balances across chains.

Checks BTC, ETH, SOL, XRP, and DOGE wallet balances using free public APIs.
Multiple fallback APIs per chain. No API keys needed.

Tools:
  wallet_check — Check all wallet balances
"""

import json
import os
import urllib.request
import urllib.error

# Wallet addresses (from donate page — these are public)
WALLETS = {
    "BTC": os.environ.get("BTC_ADDRESS", "bc1qneyd4ccsuunkz554vfyudx08hrmgsnlk4nrpds"),
    "ETH": os.environ.get("ETH_ADDRESS", "0xE8b40d85382d0f0b7Fe81B46078e6C2406B07748"),
    "SOL": os.environ.get("SOL_ADDRESS", "C1FW5VN8CQ3zCS7vvgQ7zAjRQF4WYtDRTpZWtvhiYdtU"),
    "XRP": os.environ.get("XRP_ADDRESS", "r3qixRk9o56T3PkDesjXoRrqunSxzdfuGA"),
    "DOGE": os.environ.get("DOGE_ADDRESS", "DLzeCP5cZC2Dt5y64GmWbQLjAiYA3iDn9X"),
}

_last_balances = {}
_CACHE_FILE = os.path.join(
    os.environ.get("HERMES_DATA", os.path.expanduser("~/data")),
    "wallet-balances.json"
)

SCHEMA = {
    "name": "wallet_check",
    "description": (
        "Check crypto wallet balances (BTC, ETH, SOL, XRP, DOGE). Returns current "
        "balance for each chain and flags any new deposits since last check. "
        "Uses free public APIs — no keys needed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "description": "Optional: check only one chain (btc, eth, sol, xrp, doge). Default: all.",
            },
        },
    },
}


def _fetch(url, timeout=10, data=None, headers=None):
    """Fetch URL with timeout, return parsed JSON or None."""
    try:
        h = {"User-Agent": "evey-wallet/1.0"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h, data=data)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def _try_apis(apis):
    """Try multiple API calls, return first success."""
    for fn in apis:
        try:
            result = fn()
            if result is not None:
                return result
        except Exception:
            continue
    return None


def _check_btc():
    addr = WALLETS["BTC"]

    def api1():
        data = _fetch(f"https://blockchain.info/balance?active={addr}")
        if data and addr in data:
            return data[addr].get("final_balance", 0) / 1e8
        return None

    def api2():
        data = _fetch(f"https://blockstream.info/api/address/{addr}")
        if data and "chain_stats" in data:
            funded = data["chain_stats"].get("funded_txo_sum", 0)
            spent = data["chain_stats"].get("spent_txo_sum", 0)
            return (funded - spent) / 1e8
        return None

    def api3():
        data = _fetch(f"https://api.blockcypher.com/v1/btc/main/addrs/{addr}/balance")
        if data and "balance" in data:
            return data["balance"] / 1e8
        return None

    return _try_apis([api1, api2, api3])


def _check_eth():
    addr = WALLETS["ETH"]

    def api1():
        data = _fetch(f"https://eth.blockscout.com/api/v2/addresses/{addr}")
        if data and data.get("coin_balance") is not None:
            return int(data["coin_balance"]) / 1e18
        elif data and "coin_balance" in data:
            return 0.0
        return None

    def api2():
        data = _fetch(f"https://api.ethplorer.io/getAddressInfo/{addr}?apiKey=freekey")
        if data and "ETH" in data:
            return data["ETH"].get("balance", 0)
        return None

    def api3():
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [addr, "latest"]}).encode()
        data = _fetch("https://rpc.ankr.com/eth", data=payload, headers={"Content-Type": "application/json"})
        if data and "result" in data:
            return int(data["result"], 16) / 1e18
        return None

    return _try_apis([api1, api2, api3])


def _check_sol():
    addr = WALLETS["SOL"]

    def _sol_rpc(url):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [addr]}).encode()
        data = _fetch(url, data=payload, headers={"Content-Type": "application/json"})
        if data and "result" in data:
            return data["result"].get("value", 0) / 1e9
        return None

    def api1():
        return _sol_rpc("https://api.mainnet-beta.solana.com")

    def api2():
        return _sol_rpc("https://rpc.ankr.com/solana")

    return _try_apis([api1, api2])


def _check_xrp():
    addr = WALLETS["XRP"]

    def api1():
        data = _fetch(f"https://api.xrpscan.com/api/v1/account/{addr}")
        if data and "xrpBalance" in data:
            return float(data["xrpBalance"])
        return None

    def api2():
        payload = json.dumps({"method": "account_info", "params": [{"account": addr, "ledger_index": "validated"}]}).encode()
        data = _fetch("https://s1.ripple.com:51234/", data=payload, headers={"Content-Type": "application/json"})
        if data and "result" in data:
            acct = data["result"].get("account_data", {})
            if "Balance" in acct:
                return int(acct["Balance"]) / 1e6
        return None

    def api3():
        data = _fetch(f"https://data.ripple.com/v2/accounts/{addr}/balances")
        if data and "balances" in data:
            for b in data["balances"]:
                if b.get("currency") == "XRP":
                    return float(b["value"])
        return None

    result = _try_apis([api1, api2, api3])
    # XRP accounts have a 10 XRP reserve — if balance is exactly 0 or account not found, it's unfunded
    if result is None:
        return 0.0
    return result


def _check_doge():
    addr = WALLETS["DOGE"]

    def api1():
        data = _fetch(f"https://dogechain.info/api/v1/address/balance/{addr}")
        if data and "balance" in data:
            return float(data["balance"])
        return None

    def api2():
        data = _fetch(f"https://api.blockcypher.com/v1/doge/main/addrs/{addr}/balance")
        if data and "balance" in data:
            return data["balance"] / 1e8
        return None

    def api3():
        data = _fetch(f"https://chain.so/api/v2/get_address_balance/DOGE/{addr}")
        if data and "data" in data:
            return float(data["data"].get("confirmed_balance", 0))
        return None

    return _try_apis([api1, api2, api3])


def _load_cache():
    global _last_balances
    try:
        with open(_CACHE_FILE) as f:
            _last_balances = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _last_balances = {}


def _save_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(_last_balances, f)
    except Exception:
        pass


def handler(args, **kwargs):
    _load_cache()
    chain_filter = None
    if isinstance(args, dict):
        chain_filter = (args.get("chain") or "").upper()
    elif isinstance(args, str) and args.strip():
        chain_filter = args.strip().upper()

    checkers = {
        "BTC": _check_btc,
        "ETH": _check_eth,
        "SOL": _check_sol,
        "XRP": _check_xrp,
        "DOGE": _check_doge,
    }

    if chain_filter and chain_filter in checkers:
        checkers = {chain_filter: checkers[chain_filter]}

    results = []
    deposits_detected = []

    for chain, check_fn in checkers.items():
        balance = check_fn()
        if balance is None:
            results.append(f"{chain}: Unable to check (all APIs failed)")
            continue

        prev = _last_balances.get(chain, 0)
        diff = balance - prev

        status = f"{chain}: {balance:.8f}"
        if diff > 0 and prev > 0:
            status += f" (+{diff:.8f} NEW DEPOSIT!)"
            deposits_detected.append(f"{chain}: +{diff:.8f}")
        elif balance > 0:
            status += " (has balance)"
        else:
            status += " (empty)"

        results.append(status)
        _last_balances[chain] = balance

    _save_cache()

    output = "Wallet Balances:\n" + "\n".join(results)
    if deposits_detected:
        output += "\n\nNEW DEPOSITS DETECTED:\n" + "\n".join(deposits_detected)
        output += "\nAlert V immediately!"

    return output


def register(ctx):
    ctx.register_tool(
        name=SCHEMA["name"],
        toolset="hermes-cli",
        schema=SCHEMA,
        handler=handler,
    )
