"""
Aster DEX Trading Service

Run:
python3 -m venv .venv
source .venv/bin/activate
pip install flask flask-cors requests psycopg2-binary cryptography eth-account
python src/aster-service.py

Environment:
  ASTER_SERVICE_PORT=5003 (default)
  DATABASE_URL=postgresql://...
  ENCRYPTION_KEY=...
  ASTER_TESTNET=true  (optional — switch to testnet)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
from dotenv import load_dotenv

import logging
import time
import random
import traceback
import requests as http_requests

# EIP-712 signing
from eth_account.messages import encode_typed_data
from eth_account import Account

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Network Configuration ──────────────────────────────────────
IS_TESTNET = os.environ.get('ASTER_TESTNET', 'false').lower() == 'true'

if IS_TESTNET:
    ASTER_BASE_URL = os.environ.get('ASTER_BASE_URL', 'https://fapi.asterdex-testnet.com')
    CHAIN_ID = 714
else:
    ASTER_BASE_URL = os.environ.get('ASTER_BASE_URL', 'https://fapi.asterdex.com')
    CHAIN_ID = 1666

logger.info(f"🌟 Aster DEX Service starting ({'TESTNET' if IS_TESTNET else 'MAINNET'})")
logger.info(f"📡 Base URL: {ASTER_BASE_URL}")
logger.info(f"🔗 Chain ID: {CHAIN_ID}")

# ─── Exchange Info Cache ─────────────────────────────────────────
_exchange_info_cache = None
_exchange_info_cache_time = 0
EXCHANGE_INFO_CACHE_TTL = 300  # 5 minutes


# ════════════════════════════════════════════════════════════════
#  EIP-712 TYPED DATA SIGNING
# ════════════════════════════════════════════════════════════════

# EIP-712 domain template (populated with correct chainId)
EIP712_TYPED_DATA = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"}
        ],
        "Message": [
            {"name": "msg", "type": "string"}
        ]
    },
    "primaryType": "Message",
    "domain": {
        "name": "AsterSignTransaction",
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": "0x0000000000000000000000000000000000000000"
    },
    "message": {
        "msg": ""
    }
}


def eip712_sign(params: dict, private_key: str) -> str:
    """
    Sign params using EIP-712 typed data (Aster v3 auth).
    
    Args:
        params: Dict of all params (including user, signer, nonce)
        private_key: Agent wallet's private key
        
    Returns:
        Hex signature string
    """
    # Build URL-encoded message from params
    msg = '&'.join(f'{k}={v}' for k, v in params.items())
    
    # Clone the typed data and set the message
    import copy
    typed_data = copy.deepcopy(EIP712_TYPED_DATA)
    typed_data['message']['msg'] = msg
    
    # Sign with eth_account
    message = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(message, private_key=private_key)
    
    return signed.signature.hex()


def aster_request(method: str, path: str, params: dict,
                  user_address: str, agent_address: str, 
                  agent_private_key: str, signed: bool = True):
    """
    Make a request to Aster DEX v3 API with EIP-712 signing.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        path: API path (e.g. /fapi/v3/order) — must use v3 endpoints
        params: Business parameters
        user_address: User's main wallet address
        agent_address: Agent wallet address (signer)
        agent_private_key: Agent wallet's private key
        signed: Whether to sign the request (TRADE/USER_DATA endpoints)
    
    Returns:
        Response JSON or raises exception
    """
    if signed:
        # Add auth params
        nonce = int(time.time()) * 1_000_000 + random.randint(0, 999999)
        params['nonce'] = str(nonce)
        params['user'] = user_address
        params['signer'] = agent_address
        
        # Generate EIP-712 signature
        signature = eip712_sign(params, agent_private_key)
        params['signature'] = signature
    
    url = f"{ASTER_BASE_URL}{path}"
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'MaxxitAster/1.0',
    }
    
    try:
        if method == 'GET':
            resp = http_requests.get(url, params=params, headers=headers, timeout=30)
        elif method == 'POST':
            resp = http_requests.post(url, data=params, headers=headers, timeout=30)
        elif method == 'DELETE':
            resp = http_requests.delete(url, params=params, headers=headers, timeout=30)
        elif method == 'PUT':
            resp = http_requests.put(url, data=params, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        # Parse response
        if resp.status_code == 200:
            return resp.json()
        else:
            error_data = None
            try:
                error_data = resp.json()
            except Exception:
                error_data = {"msg": resp.text}
            
            logger.error(f"[Aster API] {method} {path} → {resp.status_code}: {error_data}")
            raise AsterAPIError(
                status_code=resp.status_code,
                code=error_data.get('code', -1),
                msg=error_data.get('msg', str(error_data))
            )
    except http_requests.exceptions.Timeout:
        raise AsterAPIError(status_code=503, code=-1, msg="Request timed out")
    except http_requests.exceptions.ConnectionError:
        raise AsterAPIError(status_code=503, code=-1, msg="Connection error")


class AsterAPIError(Exception):
    """Custom exception for Aster API errors."""
    def __init__(self, status_code: int, code: int, msg: str):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"Aster API Error {code}: {msg}")


# ════════════════════════════════════════════════════════════════
#  CREDENTIAL RETRIEVAL (Reuses Ostium Agent Wallet)
# ════════════════════════════════════════════════════════════════

def get_agent_credentials(user_wallet: str) -> tuple:
    """
    Retrieve the agent wallet credentials from the database.
    Reuses the same agent address + private key as Ostium.
    
    The user must have authorized this agent address on Aster's 
    API wallet page (https://www.asterdex.com/en/api-wallet).
    
    Args:
        user_wallet: User's main wallet address
        
    Returns:
        Tuple of (user_address, agent_address, agent_private_key)
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor
    sys.path.insert(0, os.path.dirname(__file__))
    from encryption_helper import decrypt_private_key
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL not configured")
    
    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute(
            """
            SELECT 
                user_wallet,
                ostium_agent_address,
                ostium_agent_key_encrypted,
                ostium_agent_key_iv,
                ostium_agent_key_tag
            FROM user_agent_addresses 
            WHERE LOWER(user_wallet) = LOWER(%s)
            """,
            (user_wallet,)
        )
        row = cur.fetchone()
        
        if not row or not row['ostium_agent_address']:
            raise ValueError(
                f"No agent wallet found for {user_wallet}. "
                f"Please set up an agent wallet via the Ostium onboarding flow first."
            )
        
        if not row['ostium_agent_key_encrypted']:
            raise ValueError(
                f"Agent wallet exists ({row['ostium_agent_address']}) but private key is missing."
            )
        
        agent_private_key = decrypt_private_key(
            row['ostium_agent_key_encrypted'],
            row['ostium_agent_key_iv'],
            row['ostium_agent_key_tag']
        )
        
        agent_address = row['ostium_agent_address']
        user_address = row['user_wallet']
        
        logger.info(f"✅ Agent credentials loaded: user={user_address[:10]}... agent={agent_address[:10]}...")
        return user_address, agent_address, agent_private_key
    finally:
        cur.close()
        conn.close()


# ════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════

def get_exchange_info():
    """Get and cache exchange info (available symbols, filters, etc.)."""
    global _exchange_info_cache, _exchange_info_cache_time
    
    now = time.time()
    if _exchange_info_cache and (now - _exchange_info_cache_time) < EXCHANGE_INFO_CACHE_TTL:
        return _exchange_info_cache
    
    resp = http_requests.get(f"{ASTER_BASE_URL}/fapi/v3/exchangeInfo", timeout=15)
    if resp.status_code == 200:
        _exchange_info_cache = resp.json()
        _exchange_info_cache_time = now
        return _exchange_info_cache
    else:
        raise AsterAPIError(resp.status_code, -1, "Failed to fetch exchange info")


def resolve_symbol(token: str) -> str:
    """
    Resolve a token name to an Aster symbol.
    E.g. 'BTC' → 'BTCUSDT', 'ETH' → 'ETHUSDT'
    If already a full symbol (e.g. 'BTCUSDT'), return as-is.
    """
    if token.endswith('USDT'):
        return token.upper()
    return f"{token.upper()}USDT"


def get_symbol_info(symbol: str) -> dict:
    """Get trading rules for a specific symbol."""
    info = get_exchange_info()
    for s in info.get('symbols', []):
        if s['symbol'] == symbol:
            return s
    return None


def get_quantity_precision(symbol: str) -> int:
    """Get quantity precision (decimal places) for a symbol."""
    sym_info = get_symbol_info(symbol)
    if sym_info:
        return sym_info.get('quantityPrecision', 3)
    return 3


def get_price_precision(symbol: str) -> int:
    """Get price precision (decimal places) for a symbol."""
    sym_info = get_symbol_info(symbol)
    if sym_info:
        return sym_info.get('pricePrecision', 2)
    return 2


# ════════════════════════════════════════════════════════════════
#  HEALTH & INFO ENDPOINTS
# ════════════════════════════════════════════════════════════════

@app.route('/health', methods=['GET'])
def health():
    """Health check — also pings Aster API."""
    try:
        resp = http_requests.get(f"{ASTER_BASE_URL}/fapi/v3/ping", timeout=5)
        aster_ok = resp.status_code == 200
    except Exception:
        aster_ok = False
    
    return jsonify({
        "status": "ok" if aster_ok else "degraded",
        "service": "aster-dex",
        "chain": "bnb",
        "network": "testnet" if IS_TESTNET else "mainnet",
        "baseUrl": ASTER_BASE_URL,
        "chainId": CHAIN_ID,
        "authMethod": "EIP-712 (v3)",
        "aster_api_reachable": aster_ok
    })


@app.route('/symbols', methods=['GET'])
def get_symbols():
    """List available trading pairs on Aster DEX."""
    try:
        info = get_exchange_info()
        symbols = []
        for s in info.get('symbols', []):
            if s.get('status') == 'TRADING':
                symbols.append({
                    "symbol": s['symbol'],
                    "baseAsset": s.get('baseAsset'),
                    "quoteAsset": s.get('quoteAsset'),
                    "pricePrecision": s.get('pricePrecision'),
                    "quantityPrecision": s.get('quantityPrecision'),
                    "contractType": s.get('contractType'),
                    "status": s.get('status')
                })
        
        return jsonify({
            "success": True,
            "symbols": symbols,
            "count": len(symbols)
        })
    except Exception as e:
        logger.error(f"Error fetching symbols: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/market-data', methods=['GET'])
def get_market_data():
    """Get 24hr ticker data for all or specific symbols."""
    try:
        symbol = request.args.get('symbol')
        params = {}
        if symbol:
            params['symbol'] = resolve_symbol(symbol)
        
        resp = http_requests.get(
            f"{ASTER_BASE_URL}/fapi/v3/ticker/24hr",
            params=params,
            timeout=15
        )
        
        if resp.status_code != 200:
            return jsonify({"success": False, "error": "Failed to fetch market data"}), 500
        
        data = resp.json()
        
        # If single symbol, wrap in list
        if isinstance(data, dict):
            data = [data]
        
        return jsonify({
            "success": True,
            "data": data,
            "count": len(data)
        })
    except Exception as e:
        logger.error(f"Error fetching market data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/price', methods=['GET'])
def get_price():
    """Get current price for a token."""
    try:
        token = request.args.get('token')
        if not token:
            return jsonify({"success": False, "error": "token parameter required"}), 400
        
        symbol = resolve_symbol(token)
        
        resp = http_requests.get(
            f"{ASTER_BASE_URL}/fapi/v3/ticker/price",
            params={"symbol": symbol},
            timeout=10
        )
        
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"Failed to fetch price for {symbol}"}), 500
        
        data = resp.json()
        
        return jsonify({
            "success": True,
            "token": token.upper(),
            "symbol": symbol,
            "price": float(data.get('price', 0)),
            "time": data.get('time')
        })
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
#  ACCOUNT ENDPOINTS (SIGNED — EIP-712)
# ════════════════════════════════════════════════════════════════

@app.route('/balance', methods=['POST'])
def get_balance():
    """Get account USDT balance on Aster."""
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress') or data.get('address')
        
        if not user_wallet:
            return jsonify({"success": False, "error": "userAddress required"}), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        
        result = aster_request('GET', '/fapi/v3/balance', {},
                               user_address, agent_address, agent_key)
        
        # Find USDT balance (Aster uses USDT for margin)
        usdt_balance = None
        for asset in result:
            if asset.get('asset') == 'USDT':
                usdt_balance = asset
                break
        
        return jsonify({
            "success": True,
            "balance": float(usdt_balance['balance']) if usdt_balance else 0,
            "availableBalance": float(usdt_balance['availableBalance']) if usdt_balance else 0,
            "unrealizedProfit": float(usdt_balance['crossUnPnl']) if usdt_balance else 0,
            "allBalances": result
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg}), e.status_code
    except Exception as e:
        logger.error(f"Error getting balance: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/positions', methods=['POST'])
def get_positions():
    """Get open positions on Aster."""
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress') or data.get('address')
        symbol = data.get('symbol')
        
        if not user_wallet:
            return jsonify({"success": False, "error": "userAddress required"}), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        
        params = {}
        if symbol:
            params['symbol'] = resolve_symbol(symbol)
        
        result = aster_request('GET', '/fapi/v3/positionRisk', params,
                               user_address, agent_address, agent_key)
        
        # Filter to only positions with non-zero size
        positions = []
        for pos in result:
            pos_amt = float(pos.get('positionAmt', 0))
            if pos_amt != 0:
                positions.append({
                    "symbol": pos.get('symbol'),
                    "positionAmt": pos_amt,
                    "entryPrice": float(pos.get('entryPrice', 0)),
                    "markPrice": float(pos.get('markPrice', 0)),
                    "unrealizedProfit": float(pos.get('unRealizedProfit', 0)),
                    "liquidationPrice": float(pos.get('liquidationPrice', 0)),
                    "leverage": int(pos.get('leverage', 1)),
                    "marginType": pos.get('marginType'),
                    "positionSide": pos.get('positionSide', 'BOTH'),
                    "side": "long" if pos_amt > 0 else "short",
                    "isolatedMargin": float(pos.get('isolatedMargin', 0)),
                    "notional": float(pos.get('notional', 0)),
                })
        
        return jsonify({
            "success": True,
            "positions": positions,
            "count": len(positions)
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg}), e.status_code
    except Exception as e:
        logger.error(f"Error getting positions: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
#  TRADING ENDPOINTS (SIGNED — EIP-712)
# ════════════════════════════════════════════════════════════════

@app.route('/open-position', methods=['POST'])
def open_position():
    """
    Open a perpetual position on Aster DEX.
    
    Request body:
    {
        "userAddress": "0x...",    // User wallet (for credential lookup)
        "symbol": "BTC",          // Token or full symbol (BTCUSDT)
        "side": "long",           // "long" or "short"
        "quantity": 0.01,         // Position size in BASE asset (e.g. 0.01 BTC) — REQUIRED
        "leverage": 10,           // Leverage (optional, set before order)
        "type": "MARKET",         // Order type (default: MARKET)
        "price": 95000            // Required only for LIMIT orders
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        side = data.get('side')
        quantity = data.get('quantity') or data.get('size')
        leverage = data.get('leverage')
        order_type = data.get('type', 'MARKET').upper()
        price = data.get('price')  # Required for LIMIT orders
        
        if not all([user_wallet, token, side, quantity]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol, side, quantity"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        aster_side = 'BUY' if side.lower() == 'long' else 'SELL'
        
        # Set leverage before placing order if specified
        if leverage:
            try:
                aster_request('POST', '/fapi/v3/leverage', {
                    'symbol': symbol,
                    'leverage': int(leverage),
                }, user_address, agent_address, agent_key)
                logger.info(f"✅ Leverage set to {leverage}x for {symbol}")
            except AsterAPIError as e:
                logger.warning(f"⚠️ Failed to set leverage: {e.msg}")
        
        qty_precision = get_quantity_precision(symbol)
        rounded_qty = round(float(quantity), qty_precision)
        
        order_params = {
            'symbol': symbol,
            'side': aster_side,
            'type': order_type,
            'quantity': str(rounded_qty),
        }
        
        # Add price for LIMIT orders
        if order_type == 'LIMIT':
            if not price:
                return jsonify({
                    "success": False,
                    "error": "price required for LIMIT orders"
                }), 400
            price_precision = get_price_precision(symbol)
            order_params['price'] = str(round(float(price), price_precision))
            order_params['timeInForce'] = data.get('timeInForce', 'GTC')
        
        # Place order
        result = aster_request('POST', '/fapi/v3/order', order_params,
                               user_address, agent_address, agent_key)
        
        logger.info(f"✅ Order placed: {symbol} {aster_side} {rounded_qty} @ {order_type}")
        
        return jsonify({
            "success": True,
            "orderId": result.get('orderId'),
            "clientOrderId": result.get('clientOrderId'),
            "symbol": result.get('symbol'),
            "side": result.get('side'),
            "type": result.get('type'),
            "status": result.get('status'),
            "price": result.get('price'),
            "avgPrice": result.get('avgPrice'),
            "origQty": result.get('origQty'),
            "executedQty": result.get('executedQty'),
            "cumQuote": result.get('cumQuote'),
            "message": f"Position opened: {side} {symbol}"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error opening position: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/close-position', methods=['POST'])
def close_position():
    """
    Close a position on Aster DEX.
    Places a market order in the opposite direction with reduceOnly=true.
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "quantity": 0.01      // Optional — if omitted, closes full position
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        close_qty = data.get('quantity') or data.get('size')
        
        if not all([user_wallet, token]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        # Get current position to determine direction and size
        positions = aster_request('GET', '/fapi/v3/positionRisk',
                                  {'symbol': symbol},
                                  user_address, agent_address, agent_key)
        
        current_pos = None
        for pos in positions:
            if pos.get('symbol') == symbol and float(pos.get('positionAmt', 0)) != 0:
                current_pos = pos
                break
        
        if not current_pos:
            return jsonify({
                "success": True,
                "message": f"No open position found for {symbol} — may already be closed",
                "status": "already_closed"
            })
        
        pos_amt = float(current_pos['positionAmt'])
        close_side = 'SELL' if pos_amt > 0 else 'BUY'
        
        if not close_qty:
            close_qty = abs(pos_amt)
        
        qty_precision = get_quantity_precision(symbol)
        rounded_qty = round(float(close_qty), qty_precision)
        
        # Place close order
        order_params = {
            'symbol': symbol,
            'side': close_side,
            'type': 'MARKET',
            'quantity': str(rounded_qty),
            'reduceOnly': 'true',
        }
        
        result = aster_request('POST', '/fapi/v3/order', order_params,
                               user_address, agent_address, agent_key)
        
        logger.info(f"✅ Position closed: {symbol} {close_side} {rounded_qty}")
        
        return jsonify({
            "success": True,
            "orderId": result.get('orderId'),
            "symbol": result.get('symbol'),
            "side": result.get('side'),
            "status": result.get('status'),
            "executedQty": result.get('executedQty'),
            "avgPrice": result.get('avgPrice'),
            "entryPrice": float(current_pos.get('entryPrice', 0)),
            "unrealizedProfit": float(current_pos.get('unRealizedProfit', 0)),
            "message": f"Position closed: {symbol}"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error closing position: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/set-take-profit', methods=['POST'])
def set_take_profit():
    """
    Set a take profit order on an existing position.
    Uses TAKE_PROFIT_MARKET order type.
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "stopPrice": 100000,     // TP trigger price
        // OR
        "takeProfitPercent": 0.30,  // 30% TP (requires entryPrice + side)
        "entryPrice": 95000,
        "side": "long"
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        stop_price = data.get('stopPrice')
        tp_percent = data.get('takeProfitPercent')
        entry_price = data.get('entryPrice')
        side = data.get('side')
        
        if not all([user_wallet, token]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        # If percent-based, calculate stop_price
        if tp_percent and entry_price and side:
            if side.lower() == 'long':
                stop_price = float(entry_price) * (1 + float(tp_percent))
            else:
                stop_price = float(entry_price) * (1 - float(tp_percent))
        elif not stop_price:
            # Try to get position info to calculate
            positions = aster_request('GET', '/fapi/v3/positionRisk',
                                      {'symbol': symbol},
                                      user_address, agent_address, agent_key)
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('positionAmt', 0)) != 0:
                    entry_price = float(pos['entryPrice'])
                    pos_amt = float(pos['positionAmt'])
                    side = 'long' if pos_amt > 0 else 'short'
                    pct = float(tp_percent or 0.30)
                    if side == 'long':
                        stop_price = entry_price * (1 + pct)
                    else:
                        stop_price = entry_price * (1 - pct)
                    break
            
            if not stop_price:
                return jsonify({
                    "success": False,
                    "error": "stopPrice required, or provide takeProfitPercent + entryPrice + side"
                }), 400
        
        # Determine close side (opposite of position)
        if not side:
            positions = aster_request('GET', '/fapi/v3/positionRisk',
                                      {'symbol': symbol},
                                      user_address, agent_address, agent_key)
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('positionAmt', 0)) != 0:
                    side = 'long' if float(pos['positionAmt']) > 0 else 'short'
                    break
        
        close_side = 'SELL' if side and side.lower() == 'long' else 'BUY'
        
        price_precision = get_price_precision(symbol)
        rounded_price = round(float(stop_price), price_precision)
        
        # Fetch current mark price to pre-validate
        try:
            price_resp = http_requests.get(
                f"{ASTER_BASE_URL}/fapi/v3/premiumIndex",
                params={"symbol": symbol},
                timeout=10
            )
            if price_resp.status_code == 200:
                mark_price = float(price_resp.json().get('markPrice', 0))
                if mark_price > 0:
                    is_long = side and side.lower() == 'long'
                    # For long TP: stopPrice must be ABOVE mark price
                    # For short TP: stopPrice must be BELOW mark price
                    if is_long and rounded_price <= mark_price:
                        return jsonify({
                            "success": False,
                            "error": f"Take profit price ({rounded_price:,.2f}) must be ABOVE the current mark price ({mark_price:,.2f}) for a long position. "
                                     f"Your TP would trigger immediately. Increase the TP price or percentage.",
                            "code": -2021,
                            "markPrice": mark_price,
                            "requestedTpPrice": rounded_price,
                            "side": side
                        }), 400
                    elif not is_long and rounded_price >= mark_price:
                        return jsonify({
                            "success": False,
                            "error": f"Take profit price ({rounded_price:,.2f}) must be BELOW the current mark price ({mark_price:,.2f}) for a short position. "
                                     f"Your TP would trigger immediately. Decrease the TP price or percentage.",
                            "code": -2021,
                            "markPrice": mark_price,
                            "requestedTpPrice": rounded_price,
                            "side": side
                        }), 400
        except Exception as e:
            logger.warning(f"⚠️ Could not pre-validate TP price: {e}")
        
        order_params = {
            'symbol': symbol,
            'side': close_side,
            'type': 'TAKE_PROFIT_MARKET',
            'stopPrice': str(rounded_price),
            'closePosition': 'true',
            'workingType': 'MARK_PRICE',
        }
        
        result = aster_request('POST', '/fapi/v3/order', order_params,
                               user_address, agent_address, agent_key)
        
        logger.info(f"✅ Take profit set: {symbol} @ {rounded_price}")
        
        return jsonify({
            "success": True,
            "orderId": result.get('orderId'),
            "symbol": symbol,
            "tpPrice": rounded_price,
            "side": side,
            "message": f"Take profit set at {rounded_price}"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error setting take profit: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/set-stop-loss', methods=['POST'])
def set_stop_loss():
    """
    Set a stop loss order on an existing position.
    Uses STOP_MARKET order type.
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "stopPrice": 85000,      // SL trigger price
        // OR
        "stopLossPercent": 0.10,   // 10% SL (requires entryPrice + side)
        "entryPrice": 95000,
        "side": "long"
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        stop_price = data.get('stopPrice')
        sl_percent = data.get('stopLossPercent')
        entry_price = data.get('entryPrice')
        side = data.get('side')
        
        if not all([user_wallet, token]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        # If percent-based, calculate stop_price
        if sl_percent and entry_price and side:
            if side.lower() == 'long':
                stop_price = float(entry_price) * (1 - float(sl_percent))
            else:
                stop_price = float(entry_price) * (1 + float(sl_percent))
        elif not stop_price:
            # Try to get position info to calculate
            positions = aster_request('GET', '/fapi/v3/positionRisk',
                                      {'symbol': symbol},
                                      user_address, agent_address, agent_key)
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('positionAmt', 0)) != 0:
                    entry_price = float(pos['entryPrice'])
                    pos_amt = float(pos['positionAmt'])
                    side = 'long' if pos_amt > 0 else 'short'
                    pct = float(sl_percent or 0.10)
                    if side == 'long':
                        stop_price = entry_price * (1 - pct)
                    else:
                        stop_price = entry_price * (1 + pct)
                    break
            
            if not stop_price:
                return jsonify({
                    "success": False,
                    "error": "stopPrice required, or provide stopLossPercent + entryPrice + side"
                }), 400
        
        # Determine close side
        if not side:
            positions = aster_request('GET', '/fapi/v3/positionRisk',
                                      {'symbol': symbol},
                                      user_address, agent_address, agent_key)
            for pos in positions:
                if pos.get('symbol') == symbol and float(pos.get('positionAmt', 0)) != 0:
                    side = 'long' if float(pos['positionAmt']) > 0 else 'short'
                    break
        
        close_side = 'SELL' if side and side.lower() == 'long' else 'BUY'
        
        price_precision = get_price_precision(symbol)
        rounded_price = round(float(stop_price), price_precision)
        
        # Fetch current mark price to pre-validate
        try:
            price_resp = http_requests.get(
                f"{ASTER_BASE_URL}/fapi/v3/premiumIndex",
                params={"symbol": symbol},
                timeout=10
            )
            if price_resp.status_code == 200:
                mark_price = float(price_resp.json().get('markPrice', 0))
                if mark_price > 0:
                    is_long = side and side.lower() == 'long'
                    # For long SL: stopPrice must be BELOW mark price
                    # For short SL: stopPrice must be ABOVE mark price
                    if is_long and rounded_price >= mark_price:
                        return jsonify({
                            "success": False,
                            "error": f"Stop loss price ({rounded_price:,.2f}) must be BELOW the current mark price ({mark_price:,.2f}) for a long position. "
                                     f"Your SL would trigger immediately. Decrease the SL price or percentage.",
                            "code": -2021,
                            "markPrice": mark_price,
                            "requestedSlPrice": rounded_price,
                            "side": side
                        }), 400
                    elif not is_long and rounded_price <= mark_price:
                        return jsonify({
                            "success": False,
                            "error": f"Stop loss price ({rounded_price:,.2f}) must be ABOVE the current mark price ({mark_price:,.2f}) for a short position. "
                                     f"Your SL would trigger immediately. Increase the SL price or percentage.",
                            "code": -2021,
                            "markPrice": mark_price,
                            "requestedSlPrice": rounded_price,
                            "side": side
                        }), 400
        except Exception as e:
            logger.warning(f"⚠️ Could not pre-validate SL price: {e}")
        
        order_params = {
            'symbol': symbol,
            'side': close_side,
            'type': 'STOP_MARKET',
            'stopPrice': str(rounded_price),
            'closePosition': 'true',
            'workingType': 'MARK_PRICE',
        }
        
        result = aster_request('POST', '/fapi/v3/order', order_params,
                               user_address, agent_address, agent_key)
        
        logger.info(f"✅ Stop loss set: {symbol} @ {rounded_price}")
        
        return jsonify({
            "success": True,
            "orderId": result.get('orderId'),
            "symbol": symbol,
            "slPrice": rounded_price,
            "side": side,
            "message": f"Stop loss set at {rounded_price}"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error setting stop loss: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/change-leverage', methods=['POST'])
def change_leverage():
    """
    Change leverage for a symbol.
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "leverage": 10
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        leverage = data.get('leverage')
        
        if not all([user_wallet, token, leverage]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol, leverage"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        result = aster_request('POST', '/fapi/v3/leverage', {
            'symbol': symbol,
            'leverage': int(leverage),
        }, user_address, agent_address, agent_key)
        
        logger.info(f"✅ Leverage changed: {symbol} → {leverage}x")
        
        return jsonify({
            "success": True,
            "symbol": result.get('symbol'),
            "leverage": result.get('leverage'),
            "maxNotionalValue": result.get('maxNotionalValue'),
            "message": f"Leverage set to {leverage}x for {symbol}"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error changing leverage: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/cancel-order', methods=['POST'])
def cancel_order():
    """
    Cancel an active order.
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "orderId": 12345
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress')
        token = data.get('symbol') or data.get('market')
        order_id = data.get('orderId')
        client_order_id = data.get('clientOrderId')
        
        if not all([user_wallet, token]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol"
            }), 400
        
        if not order_id and not client_order_id:
            return jsonify({
                "success": False,
                "error": "orderId or clientOrderId required"
            }), 400
        
        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)
        
        params = {'symbol': symbol}
        if order_id:
            params['orderId'] = order_id
        if client_order_id:
            params['origClientOrderId'] = client_order_id
        
        result = aster_request('DELETE', '/fapi/v3/order', params,
                               user_address, agent_address, agent_key)
        
        logger.info(f"✅ Order cancelled: {symbol} orderId={order_id}")
        
        return jsonify({
            "success": True,
            "orderId": result.get('orderId'),
            "symbol": result.get('symbol'),
            "status": result.get('status'),
            "message": "Order cancelled"
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg, "code": e.code}), e.status_code
    except Exception as e:
        logger.error(f"Error cancelling order: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/all-orders', methods=['POST'])
def get_all_orders():
    """
    Get all order history for a symbol (active, canceled, and filled orders).
    
    Request body:
    {
        "userAddress": "0x...",
        "symbol": "BTC",
        "limit": 50,
        "orderId": 12345,           // optional
        "startTime": 1709251200000, // optional (ms)
        "endTime": 1709856000000    // optional (ms)
    }
    """
    try:
        data = request.json or {}
        user_wallet = data.get('userAddress') or data.get('address')
        token = data.get('symbol') or data.get('market')

        if not all([user_wallet, token]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: userAddress, symbol"
            }), 400

        user_address, agent_address, agent_key = get_agent_credentials(user_wallet)
        symbol = resolve_symbol(token)

        params = {'symbol': symbol}

        if data.get('orderId') is not None:
            params['orderId'] = int(data.get('orderId'))
        if data.get('startTime') is not None:
            params['startTime'] = int(data.get('startTime'))
        if data.get('endTime') is not None:
            params['endTime'] = int(data.get('endTime'))
        if data.get('limit') is not None:
            params['limit'] = int(data.get('limit'))

        result = aster_request('GET', '/fapi/v3/allOrders', params,
                               user_address, agent_address, agent_key)

        return jsonify({
            "success": True,
            "orders": result,
            "count": len(result),
            "symbol": symbol
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except AsterAPIError as e:
        return jsonify({"success": False, "error": e.msg}), e.status_code
    except Exception as e:
        logger.error(f"Error getting all orders: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('ASTER_SERVICE_PORT', 5003))
    logger.info(f"🚀 Aster DEX service running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)