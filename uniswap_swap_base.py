from web3 import Web3
from decimal import Decimal
import time, json

# ── Config ──────────────────────────────────────────────────────────────────
RPC_URL      = "https://base-rpc.publicnode.com"
web3         = Web3(Web3.HTTPProvider(RPC_URL))
chain_id     = web3.eth.chain_id

USDC_ADDR    = web3.to_checksum_address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")
USDT_ADDR    = web3.to_checksum_address("0xfde4c96c8593536e31f229ea8f37b2ada2699bb2")
ROUTER_ADDR  = web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")  # Uniswap V3 SwapRouter02 Base
POOL_FEE     = 100   # 0.01% — pool USDC/USDT di Base pakai fee tier 100

SLIPPAGE_PCT = Decimal("0.005")   # 0.5%
DELAY_SEC    = 3                   # jeda antar swap (detik)

# ── ABI ─────────────────────────────────────────────────────────────────────
ERC20_ABI = json.loads('[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]')

ROUTER_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

# ── Helpers ──────────────────────────────────────────────────────────────────
def get_gas_price():
    base = web3.eth.get_block("latest").get("baseFeePerGas", web3.to_wei(0.001, "gwei"))
    return int(Decimal(base) * Decimal("1.15"))

def get_balance(wallet_addr, token_addr):
    contract = web3.eth.contract(address=token_addr, abi=ERC20_ABI)
    raw      = contract.functions.balanceOf(wallet_addr).call()
    dec      = contract.functions.decimals().call()
    return raw, Decimal(raw) / (Decimal(10) ** dec)

def ensure_approve(wallet_addr, pvkey, token_addr, spender, amount_raw):
    contract   = web3.eth.contract(address=token_addr, abi=ERC20_ABI)
    allowance  = contract.functions.allowance(wallet_addr, spender).call()
    if allowance >= amount_raw:
        print("  [approve] Already approved ✓")
        return
    print("  [approve] Approving unlimited...")
    gas_price = get_gas_price()
    tx = contract.functions.approve(spender, 2**256 - 1).build_transaction({
        "from":                 wallet_addr,
        "nonce":                web3.eth.get_transaction_count(wallet_addr),
        "maxFeePerGas":         gas_price,
        "maxPriorityFeePerGas": gas_price,
    })
    tx["gas"] = int(web3.eth.estimate_gas(tx) * 1.1)
    signed   = web3.eth.account.sign_transaction(tx, pvkey)
    tx_hash  = web3.eth.send_raw_transaction(signed.raw_transaction)
    web3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  [approve] Tx: {web3.to_hex(tx_hash)}")

def swap_exact_input(wallet_addr, pvkey, token_in, token_out, amount_in_raw):
    """exactInputSingle via Uniswap V3 SwapRouter02"""
    router    = web3.eth.contract(address=ROUTER_ADDR, abi=ROUTER_ABI)
    gas_price = get_gas_price()

    token_in_dec  = web3.eth.contract(address=token_in,  abi=ERC20_ABI).functions.decimals().call()
    token_out_dec = web3.eth.contract(address=token_out, abi=ERC20_ABI).functions.decimals().call()

    amount_in_human = Decimal(amount_in_raw) / (Decimal(10) ** token_in_dec)
    amount_out_min  = int(amount_in_human * (1 - SLIPPAGE_PCT) * (Decimal(10) ** token_out_dec))

    params = {
        "tokenIn":            token_in,
        "tokenOut":           token_out,
        "fee":                POOL_FEE,
        "recipient":          wallet_addr,
        "amountIn":           amount_in_raw,
        "amountOutMinimum":   amount_out_min,
        "sqrtPriceLimitX96":  0,
    }

    tx = router.functions.exactInputSingle(params).build_transaction({
        "from":                 wallet_addr,
        "nonce":                web3.eth.get_transaction_count(wallet_addr),
        "maxFeePerGas":         gas_price,
        "maxPriorityFeePerGas": gas_price,
        "value":                0,
    })
    tx["gas"] = int(web3.eth.estimate_gas(tx) * 1.1)
    signed   = web3.eth.account.sign_transaction(tx, pvkey)
    tx_hash  = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt  = web3.eth.wait_for_transaction_receipt(tx_hash)
    return web3.to_hex(tx_hash), receipt.status  # status 1 = success

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    pvkey       = input("Private Key EVM: ").strip()
    total_swap  = int(input("Total Swap (round-trip): ").strip())

    wallet = web3.eth.account.from_key(pvkey)
    addr   = wallet.address
    print(f"\nWallet : {addr}")
    print(f"Chain  : Base (id={chain_id})")
    print(f"Loops  : {total_swap}\n{'─'*50}")

    for i in range(1, total_swap + 1):
        print(f"\n[{i}/{total_swap}] ── Round trip ──────────────────────────")
        try:
            # ── Leg 1: USDC → USDT ──────────────────────────────────────
            raw_usdc, bal_usdc = get_balance(addr, USDC_ADDR)
            if raw_usdc == 0:
                print("  [!] USDC balance 0, skip.")
                continue
            print(f"  USDC balance : {bal_usdc:.6f}")
            ensure_approve(addr, pvkey, USDC_ADDR, ROUTER_ADDR, raw_usdc)
            print(f"  Swapping USDC → USDT ...")
            tx1, s1 = swap_exact_input(addr, pvkey, USDC_ADDR, USDT_ADDR, raw_usdc)
            print(f"  Tx: {tx1}  {'✓' if s1 else '✗'}")
            time.sleep(DELAY_SEC)

            # ── Leg 2: USDT → USDC ──────────────────────────────────────
            raw_usdt, bal_usdt = get_balance(addr, USDT_ADDR)
            if raw_usdt == 0:
                print("  [!] USDT balance 0 setelah swap, skip leg 2.")
                continue
            print(f"  USDT balance : {bal_usdt:.6f}")
            ensure_approve(addr, pvkey, USDT_ADDR, ROUTER_ADDR, raw_usdt)
            print(f"  Swapping USDT → USDC ...")
            tx2, s2 = swap_exact_input(addr, pvkey, USDT_ADDR, USDC_ADDR, raw_usdt)
            print(f"  Tx: {tx2}  {'✓' if s2 else '✗'}")
            time.sleep(DELAY_SEC)

        except Exception as e:
            print(f"  [ERROR] {e}")
            time.sleep(DELAY_SEC)
            continue

    print(f"\n{'─'*50}\nDone — {total_swap} round trip(s) selesai.")

if __name__ == "__main__":
    main()
