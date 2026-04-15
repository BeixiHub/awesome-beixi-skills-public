from __future__ import annotations

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from broker_client import BrokerClient, BrokerConfig

from market_data_client import MarketDataClient


SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = SKILL_DIR / "state"
ACCOUNT_CONFIG_PATH = STATE_DIR / "account_config.json"
INNER_CODE_CACHE_PATH = STATE_DIR / "inner_code_cache.json"
AUDIT_LOG_PATH = STATE_DIR / "trade_audit_log.jsonl"
_BJT = timezone(timedelta(hours=8))
_CACHE_WRITE_LOCK = threading.Lock()


@dataclass
class AccountConfig:
    user_token: str
    account_id: str
    base_url: str = "https://swww.niuguwang.com"
    contest: int = 1
    share: int = 0


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def now_bjt() -> datetime:
    return datetime.now(tz=_BJT)


def now_iso() -> str:
    return now_bjt().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def save_json(path: Path, payload: Any) -> None:
    ensure_state_dir()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    ensure_state_dir()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_account_config() -> AccountConfig:
    raw = load_json(ACCOUNT_CONFIG_PATH, {})
    if not isinstance(raw, dict):
        raw = {}
    import os

    return AccountConfig(
        user_token=os.getenv("PAPER_TRADING_USER_TOKEN", raw.get("user_token", "")),
        account_id=os.getenv("PAPER_TRADING_ACCOUNT_ID", raw.get("account_id", "")),
        base_url=os.getenv("PAPER_TRADING_BASE_URL", raw.get("base_url", "https://swww.niuguwang.com")),
        contest=int(raw.get("contest", 1)),
        share=int(raw.get("share", 0)),
    )


def build_client() -> BrokerClient:
    config = load_account_config()
    if not config.user_token:
        raise ValueError("未配置 user_token，请填写 state/account_config.json 或设置 PAPER_TRADING_USER_TOKEN")
    return BrokerClient(
        BrokerConfig(
            user_token=config.user_token,
            account_id=config.account_id,
            base_url=config.base_url,
            contest=config.contest,
            share=config.share,
        )
    )


def build_market_client() -> MarketDataClient:
    """构造免鉴权的行情客户端（新浪公开接口，无需 token）。"""
    return MarketDataClient()


def normalize_code(value: str) -> str:
    digits = "".join(ch for ch in value.upper() if ch.isdigit())
    return digits if len(digits) == 6 else ""


def market_code(value: str) -> str:
    """
    将 6 位股票代码归一化为 '600519.SH' 格式。覆盖 A 股 + ETF：
    - 6xxxxx / 9xxxxx / 5xxxxx            → SH（沪主板、B股、沪市 ETF、科创板）
    - 0xxxxx / 2xxxxx / 3xxxxx / 1xxxxx   → SZ（深主板、B股、创业板、深市 ETF）
    - 4xxxxx / 8xxxxx                     → BJ（北交所）
    """
    raw = value.strip().upper()
    if raw.endswith((".SH", ".SZ", ".BJ")) and normalize_code(raw):
        return raw
    code = normalize_code(raw)
    if not code:
        return raw
    if code.startswith(("6", "9", "5")):
        return f"{code}.SH"
    if code.startswith(("0", "2", "3", "1")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SH"


def parse_success(result: Any) -> bool:
    return bool(isinstance(result, dict) and (result.get("code") == 0 or result.get("result") == 1))


def _normalize_direction(value: Any) -> str:
    if str(value) == "1":
        return "买入"
    if str(value) == "2":
        return "卖出"
    return str(value or "")


def _decode_delegate_state(value: Any) -> str:
    """
    牛股网虚拟盘委托状态码解析。观察值：
        0 = 待成交（挂单中）
        1 = 已成交（全部）
        2 = 已撤单 / 废单
        3 = 部分成交
    未知状态原样返回。
    """
    mapping = {
        "0": "待成交",
        "1": "已成交",
        "2": "已撤单",
        "3": "部分成交",
    }
    return mapping.get(str(value).strip(), str(value) if value is not None else "")


def _to_float(value: Any) -> float:
    """容忍 None、空串、数字、以及带 '%' 后缀的字符串（如 '1.23%'）。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_int(value: Any) -> int:
    return int(_to_float(value))


def account_summary(client: BrokerClient) -> dict[str, Any]:
    payload = client.get_account()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return {
        "status": "ok",
        "summary": {
            "total_assets": _to_float(data.get("totalAssets")),
            "available_asset": _to_float(data.get("avaliableAsset")),
            "today_profit_and_loss": _to_float(data.get("todayProfitAndLoss")),
            "today_profit_and_loss_rate": _to_float(data.get("todayProfitAndLossRate")),
            "total_profit": _to_float(data.get("totalProfit")),
            "total_profit_and_loss": _to_float(data.get("totalProfitAndLoss")),
            "rank": data.get("rank"),
        },
        "fetched_at": now_iso(),
    }


def holdings_summary(client: BrokerClient) -> dict[str, Any]:
    if not client.config.account_id:
        raise ValueError("未配置 account_id，请填写 state/account_config.json")
    payload = client.get_stock_holding()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    summary = data.get("summary", {}) or {}
    items = []
    for item in data.get("items", []) or []:
        items.append(
            {
                "stock_code": str(item.get("stockCode", "")).strip(),
                "stock_name": str(item.get("stockName", "")).strip(),
                "inner_code": item.get("innerCode"),
                "holding_amount": _to_int(item.get("holdingAmount")),
                "tradeable": _to_int(item.get("tradeable", item.get("holdingAmount"))),
                "current_unit_price": _to_float(item.get("currentUnitPrice")),
                "unit_price": _to_float(item.get("unitPrice")),
                "stock_market_price": _to_float(item.get("stockMarketPrice")),
                "stock_profit_and_loss": _to_float(item.get("stockProfitAndLoss")),
                "stock_profit_and_loss_rate": _to_float(item.get("stockProfitAndLossRate")),
                "position": _to_float(item.get("position")),
            }
        )
    return {
        "status": "ok",
        "summary": {
            "total_assets": _to_float(summary.get("totalAssets")),
            "available_asset": _to_float(summary.get("avaliableAsset")),
            "total_market_price": _to_float(summary.get("totalMarketPrice")),
            "total_profit_and_loss": _to_float(summary.get("totalProfitAndLoss")),
            "today_profit_and_loss": _to_float(summary.get("todayProfitAndLoss")),
            "position_count": len(items),
        },
        "items": items,
        "fetched_at": now_iso(),
    }


def deal_list_summary(client: BrokerClient, history: bool = False) -> dict[str, Any]:
    payload = client.get_stock_deal_history_list() if history else client.get_stock_deal_today_list()
    items = []
    for item in payload.get("data", []) or []:
        items.append(
            {
                "deal_time": item.get("dealTime"),
                "stock_name": item.get("stockName"),
                "stock_code": item.get("stockCode"),
                "unit_price": _to_float(item.get("unitPrice")),
                "amount": _to_int(item.get("amount")),
                "type": _normalize_direction(item.get("type")),
                "price": _to_float(item.get("price")),
            }
        )
    return {
        "status": "ok",
        "scope": "history" if history else "today",
        "count": len(items),
        "items": items,
        "fetched_at": now_iso(),
    }


def delegate_list_summary(client: BrokerClient, history: bool = False) -> dict[str, Any]:
    payload = client.get_stock_delegate_history_list() if history else client.get_stock_delegate_today_list()
    items = []
    for item in payload.get("data", []) or []:
        items.append(
            {
                "stock_code": item.get("stockCode"),
                "stock_name": item.get("stockName"),
                "inner_code": item.get("innerCode"),
                "delegate_amount": _to_int(item.get("delegateAmount")),
                "delegate_id": item.get("delegateID"),
                "delegate_state": item.get("delegateState"),
                "delegate_state_text": _decode_delegate_state(item.get("delegateState")),
                "delegate_time": item.get("delegateTime"),
                "delegate_type": _normalize_direction(item.get("delegateType")),
                "deal_number": _to_int(item.get("dealNumber")),
                "delegate_unit_price": _to_float(item.get("delegateUnitPrice")),
                "avg_price": _to_float(item.get("avgPrice")),
            }
        )
    return {
        "status": "ok",
        "scope": "history" if history else "today",
        "count": len(items),
        "items": items,
        "fetched_at": now_iso(),
    }


def find_holding_match(identifier: str, holding_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = identifier.strip()
    code = normalize_code(text)
    if code:
        for item in holding_items:
            if normalize_code(str(item.get("stock_code", ""))) == code:
                return item
    lowered = text.lower()
    exact = [item for item in holding_items if str(item.get("stock_name", "")).lower() == lowered]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [item for item in holding_items if lowered and lowered in str(item.get("stock_name", "")).lower()]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        names = ", ".join(f"{item['stock_name']}({item['stock_code']})" for item in fuzzy[:5])
        raise ValueError(f"匹配到多个持仓标的，请改用股票代码：{names}")
    return None


def resolve_symbol(identifier: str, holding_items: list[dict[str, Any]]) -> dict[str, Any]:
    holding_match = find_holding_match(identifier, holding_items)
    if holding_match:
        code = normalize_code(str(holding_match.get("stock_code", "")))
        return {
            "identifier": identifier,
            "stock_code": code,
            "market_code": market_code(code),
            "stock_name": holding_match.get("stock_name", ""),
            "inner_code": holding_match.get("inner_code"),
            "source": "holding",
        }

    code = normalize_code(identifier)
    if code:
        market_code_str = market_code(code)
        # 顺便从新浪 quote 拿一个股票名填进去（没拿到就留空，不是致命问题）
        backfill_name = ""
        try:
            q = fetch_quote(market_code_str)
            if q:
                backfill_name = str(q.get("name", "") or "")
        except Exception:
            pass
        return {
            "identifier": identifier,
            "stock_code": code,
            "market_code": market_code_str,
            "stock_name": backfill_name,
            "inner_code": None,
            "source": "code",
        }

    market = build_market_client()
    try:
        result = market.lookup_security_profile(identifier)
    except Exception as exc:
        raise ValueError(f"无法按名称识别股票：{identifier}（行情接口错误：{exc}）") from exc

    profile = result.get("profile") or {}
    ticker = str(profile.get("ticker", "")).strip()
    code = normalize_code(ticker)
    if not code:
        candidates = result.get("candidates") or []
        if len(candidates) > 1:
            names = ", ".join(f"{c['name']}({c['ticker']})" for c in candidates[:5])
            raise ValueError(f"名称 '{identifier}' 命中多条，请明确：{names}")
        raise ValueError(f"无法识别股票名称：{identifier}")

    return {
        "identifier": identifier,
        "stock_code": code,
        "market_code": market_code(ticker),
        "stock_name": profile.get("name", "") or identifier,
        "industry": profile.get("industry", ""),
        "inner_code": None,
        "source": "market_data",
    }


def fetch_quote(market_stock_code: str) -> dict[str, float] | None:
    market = build_market_client()
    try:
        return market.get_realtime_quote(market_stock_code)
    except Exception:
        return None


def load_inner_code_cache() -> dict[str, int]:
    raw = load_json(INNER_CODE_CACHE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except Exception:
            continue
    return result


def save_inner_code_cache(cache: dict[str, int]) -> None:
    with _CACHE_WRITE_LOCK:
        save_json(INNER_CODE_CACHE_PATH, cache)


def _collect_mappings_from_delegates(client: BrokerClient) -> dict[str, int]:
    """从 delegates-history + delegates-today 批量收集 stockCode → innerCode 映射。"""
    mappings: dict[str, int] = {}
    for payload in (
        client.get_stock_delegate_history_list(),
        client.get_stock_delegate_today_list(),
    ):
        for item in payload.get("data", []) or []:
            code = normalize_code(str(item.get("stockCode", "")))
            inner = item.get("innerCode")
            if code and inner:
                try:
                    mappings[code] = int(inner)
                except (TypeError, ValueError):
                    continue
    return mappings


def warm_inner_code_cache() -> dict[str, Any]:
    """
    预热 innerCode 缓存：从历史委托与持仓中批量收集映射并写入。
    返回新增条目数。用户可通过 `warm-cache` 命令手动触发。
    """
    client = build_client()
    cache = load_inner_code_cache()
    before = set(cache.keys())

    # 来源 1：delegates-history + delegates-today
    cache.update(_collect_mappings_from_delegates(client))

    # 来源 2：当前持仓
    try:
        holdings = client.get_stock_holding()
        for item in (holdings.get("data", {}) or {}).get("items", []) or []:
            code = normalize_code(str(item.get("stockCode", "")))
            inner = item.get("innerCode")
            if code and inner:
                cache[code] = int(inner)
    except Exception:
        pass

    save_inner_code_cache(cache)
    added = sorted(set(cache.keys()) - before)
    return {
        "status": "ok",
        "total": len(cache),
        "added_count": len(added),
        "added": added,
        "cache": cache,
    }


def _dynamic_resolve_single_stock(
    client: BrokerClient,
    target_stock_code: str,
    known_inner_codes: set[int],
    cache: dict[str, int],
    max_inner_code: int = 10000,
    concurrency: int = 20,
    verbose: bool = True,
) -> int | None:
    """
    动态反查单只股票的 innerCode（按需搜索 fallback）。

    策略：
      1. 跳过已在缓存中的 innerCode（避免重复工作）
      2. 并发 probe + verify（复用 _discover_inner_code）
      3. 边搜边把过程中发现的其它股票映射写入缓存（side-effect 收益）
      4. 命中目标立即终止剩余任务并返回

    返回 innerCode；搜完指定范围仍未找到则返回 None。
    """
    candidates = [ic for ic in range(1, max_inner_code + 1) if ic not in known_inner_codes]
    total = len(candidates)
    if total == 0:
        return None

    if verbose:
        print(
            f"[dynamic-resolve] 目标 {target_stock_code}，扫描候选 innerCode "
            f"{total} 个（已跳过 {max_inner_code - total} 个已知值）",
            flush=True,
        )

    found_inner_code: int | None = None
    cache_lock = threading.Lock()
    processed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_discover_inner_code, client, ic): ic for ic in candidates}
        try:
            for future in as_completed(futures):
                processed += 1
                try:
                    inner_code, discovered_code, _discovered_name = future.result()
                except Exception:
                    continue

                if discovered_code:
                    normalized = normalize_code(discovered_code)
                    if normalized:
                        with cache_lock:
                            cache[normalized] = inner_code
                        if normalized == target_stock_code:
                            found_inner_code = inner_code
                            break

                if verbose and processed % 100 == 0:
                    print(
                        f"[dynamic-resolve] 已扫 {processed}/{total}，"
                        f"缓存已累积 {len(cache)} 条",
                        flush=True,
                    )
        finally:
            # 命中目标或异常时立即取消剩余任务，节省时间和 broker 请求
            for f in futures:
                if not f.done():
                    f.cancel()

    # 搜索过程中发现的所有映射都已写入缓存
    save_inner_code_cache(cache)

    if verbose and found_inner_code is not None:
        print(
            f"[dynamic-resolve] 命中 {target_stock_code} → innerCode {found_inner_code}",
            flush=True,
        )

    return found_inner_code


def resolve_inner_code(
    client: BrokerClient,
    stock_code: str,
    market_stock_code: str,
    holding_items: list[dict[str, Any]],
) -> int:
    """
    按以下顺序查找 innerCode：

      1. 本地缓存 state/inner_code_cache.json（命中即返回，毫秒级）
      2. 当前持仓列表（接口直接返回 innerCode）
      3. 历史委托 + 今日委托（顺带把所有发现的映射批量回填到缓存）
      4. 以上都没命中 → 动态搜索（调用 _dynamic_resolve_single_stock）
      5. 动态搜索也找不到 → 抛出明确错误

    第 4 步即"先去库里搜，没有再去搜并把搜到的加进来"——搜到的顺带
    也把过程中发现的其它股票映射写入缓存，下次更快。
    """
    cache = load_inner_code_cache()
    if stock_code in cache:
        return cache[stock_code]

    holding_match = find_holding_match(stock_code, holding_items)
    if holding_match and holding_match.get("inner_code"):
        inner = int(holding_match["inner_code"])
        cache[stock_code] = inner
        save_inner_code_cache(cache)
        return inner

    # 从历史委托免费收割所有已知映射
    bulk = _collect_mappings_from_delegates(client)
    if bulk:
        cache.update(bulk)
        save_inner_code_cache(cache)
        if stock_code in bulk:
            return bulk[stock_code]

    # 最后兜底：动态搜索（会自动复用缓存跳过已知 innerCode）
    known = set(cache.values())
    found = _dynamic_resolve_single_stock(
        client=client,
        target_stock_code=stock_code,
        known_inner_codes=known,
        cache=cache,
    )
    if found is not None:
        return found

    raise ValueError(
        f"动态搜索完成，仍未找到 {stock_code} 的 innerCode。\n"
        f"可能原因：\n"
        f"  • 此股票的 innerCode 在 10000 以外（极少见）\n"
        f"  • broker 数据库中不存在此股票\n"
        f"  • 新上市股票尚未被 broker 录入\n"
        f"请先确认代码是否正确；如确认无误，尝试扩大范围后手动建档。"
    )


def _probe_inner_code(
    client: BrokerClient, inner_code: int
) -> tuple[str, str | None, str]:
    """
    单次 probe。返回 (status, stock_code_or_none, extra_info)。

    status 取值：
        "suspended"   —— 停牌，extra 是 stockCode
        "valid"       —— 活跃股票，extra 是跌停价字符串
        "not_found"   —— innerCode 无效 / 非股票
        "error"       —— 网络或其它异常
    """
    try:
        probe = client.request_json(
            "/tr/delegateadd.ashx",
            {
                "userToken": client.config.user_token,
                "innerCode": inner_code,
                "amount": 100,
                "price": 0.01,
                "type": 1,
                "contest": client.config.contest,
                "share": client.config.share,
            },
        )
    except Exception as exc:
        return ("error", None, str(exc)[:100])

    message = str(probe.get("message", ""))
    suspend_match = re.search(r"停牌中\((\d{6})\)", message)
    if suspend_match:
        return ("suspended", suspend_match.group(1), "")

    down_match = re.search(r"跌停价格\(([\d.]+)\)", message)
    if down_match:
        return ("valid", None, down_match.group(1))

    return ("not_found", None, message[:100])


def _discover_inner_code(
    client: BrokerClient, inner_code: int
) -> tuple[int, str | None, str | None]:
    """
    发现单个 innerCode 对应的 stockCode（配合 build_inner_code_map 使用）。

    流程：
        1. probe @ 0.01 → 如果停牌，直接拿到 stockCode
        2. probe 返回跌停价 → 以 (跌停价 + 0.01) 下真实小单
        3. 查 delegates-today，按 delegate_id 定位股票
        4. 无论成败立即撤单

    返回 (inner_code, stock_code_or_none, stock_name_or_none)。
    """
    status, suspended_code, info = _probe_inner_code(client, inner_code)

    if status == "suspended" and suspended_code:
        # 停牌股直接从 message 里拿到代码，无需下单
        return (inner_code, suspended_code, None)

    if status != "valid":
        return (inner_code, None, None)

    try:
        down_limit = float(info)
    except (ValueError, TypeError):
        return (inner_code, None, None)

    safe_price = round(down_limit + 0.01, 2)
    try:
        buy_result = client.delegate_buy_stock(inner_code, 100, safe_price)
    except Exception:
        return (inner_code, None, None)

    delegate_id = buy_result.get("delegateID")
    if not delegate_id or buy_result.get("code") != 0:
        return (inner_code, None, None)

    actual_code: str | None = None
    actual_name: str | None = None
    try:
        delegates = client.get_stock_delegate_today_list()
        for item in delegates.get("data", []) or []:
            if str(item.get("delegateID", "")) == str(delegate_id):
                actual_code = str(item.get("stockCode", "")).strip() or None
                actual_name = str(item.get("stockName", "")).strip() or None
                break
    finally:
        try:
            client.delegate_cancel(delegate_id)
        except Exception:
            pass

    return (inner_code, actual_code, actual_name)


def build_inner_code_map(
    start: int = 1,
    end: int = 6000,
    concurrency: int = 10,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    一次性构建全市场 innerCode → stockCode 映射表。

    这是针对新股票首次下单"innerCode 不可知"问题的彻底解法：
    一次性遍历 innerCode 空间，把结果写入 state/inner_code_cache.json。
    之后任何股票的下单都 O(1) 命中，永不再跑反查。

    参数：
        start:       起始 innerCode（含）
        end:         结束 innerCode（含）
        concurrency: 并发度（默认 10；降到 5 更稳，提到 20 更快）
        verbose:     是否打印进度

    可恢复：已在缓存中的 innerCode 会被自动跳过，所以 Ctrl+C 中断后再跑一次
    即可继续。
    """
    client = build_client()
    cache = load_inner_code_cache()

    # 先从历史委托免费收割一批
    cache.update(_collect_mappings_from_delegates(client))
    try:
        holdings = client.get_stock_holding()
        for item in (holdings.get("data", {}) or {}).get("items", []) or []:
            c = normalize_code(str(item.get("stockCode", "")))
            inner = item.get("innerCode")
            if c and inner:
                cache[c] = int(inner)
    except Exception:
        pass
    save_inner_code_cache(cache)

    already_known_inner = set(cache.values())
    todo = [ic for ic in range(start, end + 1) if ic not in already_known_inner]
    total = len(todo)

    if verbose:
        print(
            f"[build-map] 范围 {start}-{end}，已缓存 {len(cache)} 条，待处理 {total} 个 innerCode",
            flush=True,
        )

    discovered = 0
    errors = 0
    processed = 0
    cache_lock = threading.Lock()

    def _save_if_discovered(inner_code: int, code: str | None) -> None:
        nonlocal discovered
        if not code:
            return
        normalized = normalize_code(code)
        if not normalized:
            return
        with cache_lock:
            cache[normalized] = inner_code
            save_inner_code_cache(cache)
            discovered += 1

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_discover_inner_code, client, ic): ic for ic in todo}
            for future in as_completed(futures):
                processed += 1
                try:
                    inner_code, stock_code, _stock_name = future.result()
                except Exception:
                    errors += 1
                    continue
                _save_if_discovered(inner_code, stock_code)
                if verbose and processed % 50 == 0:
                    print(
                        f"[build-map] {processed}/{total} processed, "
                        f"{discovered} discovered, {errors} errors",
                        flush=True,
                    )
    except KeyboardInterrupt:
        if verbose:
            print("[build-map] 中断，已保存进度", flush=True)

    return {
        "status": "ok",
        "range": [start, end],
        "processed": processed,
        "discovered": discovered,
        "errors": errors,
        "cache_size": len(cache),
    }


def resolve_stock_name(
    client: BrokerClient,
    stock_code: str,
    fallback: str,
    holding_items: list[dict[str, Any]],
) -> str:
    holding_match = find_holding_match(stock_code, holding_items)
    if holding_match and holding_match.get("stock_name"):
        return str(holding_match["stock_name"])

    for payload in (client.get_stock_delegate_today_list(), client.get_stock_delegate_history_list()):
        for item in payload.get("data", []) or []:
            if normalize_code(str(item.get("stockCode", ""))) == stock_code and item.get("stockName"):
                return str(item["stockName"])
    return fallback


def sanitize_price(action: str, price: float, quote: dict[str, float] | None) -> tuple[float, list[str]]:
    order_price = round(float(price), 2)
    notes: list[str] = []
    if quote:
        upper = float(quote.get("upperLimit", 0) or 0)
        lower = float(quote.get("downLimit", 0) or 0)
        if action == "buy" and upper > 0 and order_price >= upper:
            order_price = round(max(upper - 0.01, 0.01), 2)
            notes.append(f"买入价触及涨停，已调整为 {order_price:.2f}")
        if action == "sell" and lower > 0 and order_price < lower:
            order_price = round(lower, 2)
            notes.append(f"卖出价低于跌停，已调整为 {order_price:.2f}")
    return order_price, notes


def place_order(action: str, identifier: str, quantity: int, price: float | None = None) -> dict[str, Any]:
    """直接下单：解析标的 → 校验 → 调用委托接口 → 返回结果（无二次确认）。"""
    if action not in {"buy", "sell"}:
        raise ValueError("Unsupported action")
    if quantity <= 0:
        raise ValueError("数量必须大于 0")
    if action == "buy" and quantity % 100 != 0:
        raise ValueError("买入数量必须是 100 股整数倍")

    client = build_client()
    account = account_summary(client)
    holdings = holdings_summary(client)
    holding_items = holdings["items"]
    resolved = resolve_symbol(identifier, holding_items)
    quote = fetch_quote(resolved["market_code"])

    if action == "sell":
        holding = find_holding_match(resolved["stock_code"], holding_items)
        if holding is None:
            raise ValueError(f"当前持仓中没有 {identifier}")
        tradeable = int(holding.get("tradeable", 0) or 0)
        if quantity > tradeable:
            raise ValueError(f"可卖数量不足：可卖 {tradeable} 股，请调整卖出数量")
        inner_code = int(holding.get("inner_code") or 0)
        if inner_code <= 0:
            inner_code = resolve_inner_code(client, resolved["stock_code"], resolved["market_code"], holding_items)
        stock_name = holding.get("stock_name") or resolved.get("stock_name") or identifier
    else:
        inner_code = resolve_inner_code(client, resolved["stock_code"], resolved["market_code"], holding_items)
        stock_name = resolve_stock_name(
            client,
            resolved["stock_code"],
            resolved.get("stock_name") or identifier,
            holding_items,
        )

    working_price = price
    if working_price is None:
        if not quote or quote.get("latest", 0) <= 0:
            raise ValueError("无法获取实时价，请显式指定委托价格")
        working_price = float(quote["latest"])

    order_price, notes = sanitize_price(action, float(working_price), quote)
    estimated_turnover = round(quantity * order_price, 2)

    if action == "buy":
        available_asset = float(account["summary"]["available_asset"])
        if estimated_turnover > available_asset:
            raise ValueError(f"可用资金不足：预计需要 {estimated_turnover:.2f}，当前可用 {available_asset:.2f}")

    if action == "buy":
        raw_result = client.delegate_buy_stock(
            inner_code=int(inner_code),
            amount=int(quantity),
            price=float(order_price),
        )
    else:
        raw_result = client.delegate_sell_stock(
            inner_code=int(inner_code),
            amount=int(quantity),
            price=float(order_price),
        )

    acct = account_summary(client)
    result = {
        "status": "ok" if parse_success(raw_result) else "rejected",
        "submitted_at": now_iso(),
        "action": action,
        "stock_code": resolved["stock_code"],
        "market_code": resolved["market_code"],
        "stock_name": stock_name,
        "inner_code": inner_code,
        "quantity": quantity,
        "order_price": order_price,
        "estimated_turnover": estimated_turnover,
        "price_source": "user" if price is not None else "quote",
        "warnings": notes,
        "delegate_id": raw_result.get("delegateID"),
        "message": raw_result.get("message", ""),
        "available_asset": acct["summary"]["available_asset"],
    }
    # Audit log keeps full detail; CLI output stays compact to avoid context overflow
    audit_result = dict(result, raw_result=raw_result)
    append_jsonl(AUDIT_LOG_PATH, {"event": "place_order", "result": audit_result, "logged_at": now_iso()})
    return result


def cancel_order(delegate_id: str | int) -> dict[str, Any]:
    """直接撤单：调用 /tr/delegatecancel.ashx 撤销指定委托单。"""
    if delegate_id is None or str(delegate_id).strip() == "":
        raise ValueError("缺少 delegate_id：请提供要撤销的委托单 ID")

    client = build_client()
    raw_result = client.delegate_cancel(delegate_id)

    result = {
        "status": "ok" if parse_success(raw_result) else "rejected",
        "submitted_at": now_iso(),
        "delegate_id": str(delegate_id),
        "returned_delegate_id": raw_result.get("delegateID"),
        "message": raw_result.get("message", ""),
        "available_asset": raw_result.get("avaliableAsset"),
    }
    audit_result = dict(result, raw_result=raw_result)
    append_jsonl(AUDIT_LOG_PATH, {"event": "cancel_order", "result": audit_result, "logged_at": now_iso()})
    return result


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper trading skill helper (A-share / ETF)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("account")
    subparsers.add_parser("holdings")
    subparsers.add_parser("warm-cache", help="从历史委托/持仓批量预热 innerCode 缓存")

    build_map = subparsers.add_parser(
        "build-map",
        help="一次性建档全市场 innerCode 映射（耗时较长，一般只需运行一次）",
    )
    build_map.add_argument("--start", type=int, default=1)
    build_map.add_argument("--end", type=int, default=6000)
    build_map.add_argument("--concurrency", type=int, default=10)

    subparsers.add_parser("deals-today")
    subparsers.add_parser("deals-history")
    subparsers.add_parser("delegates-today")
    subparsers.add_parser("delegates-history")

    resolve_parser = subparsers.add_parser("resolve-symbol")
    resolve_parser.add_argument("identifier")

    quote_parser = subparsers.add_parser("quote")
    quote_parser.add_argument("identifier")

    buy = subparsers.add_parser("buy", help="直接买入（无二次确认）")
    buy.add_argument("identifier")
    buy.add_argument("quantity", type=int)
    buy.add_argument("--price", type=float, default=None)

    sell = subparsers.add_parser("sell", help="直接卖出（无二次确认）")
    sell.add_argument("identifier")
    sell.add_argument("quantity", type=int)
    sell.add_argument("--price", type=float, default=None)

    cancel = subparsers.add_parser("cancel", help="撤销委托单")
    cancel.add_argument("delegate_id")

    resolve_inner = subparsers.add_parser(
        "resolve-inner",
        help="按股票代码反查 innerCode（先查缓存，未命中则动态搜索）",
    )
    resolve_inner.add_argument("stock_code")

    args = parser.parse_args()
    try:
        if args.command == "account":
            print_json(account_summary(build_client()))
            return 0
        if args.command == "holdings":
            print_json(holdings_summary(build_client()))
            return 0
        if args.command == "warm-cache":
            print_json(warm_inner_code_cache())
            return 0
        if args.command == "build-map":
            print_json(
                build_inner_code_map(
                    start=args.start,
                    end=args.end,
                    concurrency=args.concurrency,
                )
            )
            return 0
        if args.command == "deals-today":
            print_json(deal_list_summary(build_client(), history=False))
            return 0
        if args.command == "deals-history":
            print_json(deal_list_summary(build_client(), history=True))
            return 0
        if args.command == "delegates-today":
            print_json(delegate_list_summary(build_client(), history=False))
            return 0
        if args.command == "delegates-history":
            print_json(delegate_list_summary(build_client(), history=True))
            return 0
        if args.command == "resolve-symbol":
            client = build_client()
            holdings = holdings_summary(client)
            print_json({"status": "ok", "resolved": resolve_symbol(args.identifier, holdings["items"])})
            return 0
        if args.command == "quote":
            client = build_client()
            holdings = holdings_summary(client)
            resolved = resolve_symbol(args.identifier, holdings["items"])
            print_json({"status": "ok", "resolved": resolved, "quote": fetch_quote(resolved["market_code"])})
            return 0
        if args.command == "buy":
            print_json(place_order("buy", args.identifier, args.quantity, args.price))
            return 0
        if args.command == "sell":
            print_json(place_order("sell", args.identifier, args.quantity, args.price))
            return 0
        if args.command == "cancel":
            print_json(cancel_order(args.delegate_id))
            return 0
        if args.command == "resolve-inner":
            code = normalize_code(args.stock_code)
            if not code:
                print_json({"status": "error", "message": f"非法股票代码: {args.stock_code}"})
                return 1
            client = build_client()
            holdings = holdings_summary(client)
            try:
                inner = resolve_inner_code(
                    client=client,
                    stock_code=code,
                    market_stock_code=market_code(code),
                    holding_items=holdings["items"],
                )
                print_json({"status": "ok", "stock_code": code, "inner_code": inner})
            except ValueError as exc:
                print_json({"status": "error", "stock_code": code, "message": str(exc)})
                return 1
            return 0
    except Exception as exc:
        print_json({"status": "error", "message": str(exc)})
        return 1
    print_json({"status": "error", "message": f"unknown command: {args.command}"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
