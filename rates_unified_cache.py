#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый кеш L1 (по источнику) + L2 (готовый текст/сводка команд).

CLI ``rates.py`` (сводка) и бот используют один файл (по умолчанию
``.rates_unified_cache.json`` или ``RATES_UNIFIED_CACHE_FILE``).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA_VERSION = 1

_UNIFIED_OVERRIDE = (os.environ.get("RATES_UNIFIED_CACHE_FILE") or "").strip()
_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_UNIFIED_CACHE_PATH = (
    Path(_UNIFIED_OVERRIDE) if _UNIFIED_OVERRIDE else _SCRIPT_DIR / ".rates_unified_cache.json"
)

# TTL секунд по типу L1 (значения по умолчанию; можно переопределить env)
def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


TTL_L1_RATE_SOURCE_SEC = _env_int("RATES_UNIFIED_TTL_RS", 30 * 60)
TTL_L1_USDT_BRANCH_SEC = _env_int("RATES_UNIFIED_TTL_USDT", 60)
TTL_L1_CASH_CELL_SEC = _env_int("RATES_UNIFIED_TTL_CASH_CELL", 15 * 60)
TTL_L1_CASH_TT_SEC = _env_int("RATES_UNIFIED_TTL_CASH_TT", 15 * 60)
TTL_L1_EXCHANGE_STORES_SEC = _env_int("RATES_UNIFIED_TTL_EX_TT_STORES", 10 * 60)
TTL_L1_EXCHANGE_CUR_SEC = _env_int("RATES_UNIFIED_TTL_EX_TT_CUR", 10 * 60)
TTL_L1_EX24_SEC = _env_int("RATES_UNIFIED_TTL_EX24", 10 * 60)

TTL_L2_SUMMARY_SEC = _env_int("RATES_UNIFIED_TTL_L2_SUMMARY", 30 * 60)
TTL_L2_USDT_SEC = _env_int("RATES_UNIFIED_TTL_L2_USDT", 60)
TTL_L2_CASH_SEC = _env_int("RATES_UNIFIED_TTL_L2_CASH", 15 * 60)
TTL_L2_CASH_THB_SEC = _env_int("RATES_UNIFIED_TTL_L2_CASH_THB", 15 * 60)
TTL_L2_EXCHANGE_SEC = _env_int("RATES_UNIFIED_TTL_L2_EXCHANGE", 10 * 60)

_save_lock = threading.Lock()
_l1_lock = threading.Lock()


def stable_digest(obj: Any) -> str:
    """Стабильный короткий ключ из JSON-совместимого объекта."""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_unified(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or DEFAULT_UNIFIED_CACHE_PATH
    if not p.is_file():
        return _empty_doc()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_doc()
    if raw.get("schema") != _SCHEMA_VERSION:
        return _empty_doc()
    raw.setdefault("l1", {})
    raw.setdefault("l2", {})
    raw.setdefault("version_counter", 0)
    return raw


def _empty_doc() -> Dict[str, Any]:
    return {"schema": _SCHEMA_VERSION, "l1": {}, "l2": {}, "version_counter": 0}


def save_unified(doc: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or DEFAULT_UNIFIED_CACHE_PATH
    doc = dict(doc)
    doc["schema"] = _SCHEMA_VERSION
    tmp = p.with_suffix(p.suffix + ".tmp")
    txt = json.dumps(doc, ensure_ascii=False, indent=2)
    with _save_lock:
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(p)


def _next_version(doc: Dict[str, Any]) -> int:
    v = int(doc.get("version_counter", 0)) + 1
    doc["version_counter"] = v
    return v


def l1_get_valid(
    doc: Dict[str, Any], key: str, *, now: Optional[float] = None
) -> Optional[Tuple[int, Any]]:
    """Вернёт (version, payload) если запись есть и TTL не вышел."""
    t = time.time() if now is None else now
    ent = doc.get("l1", {}).get(key)
    if not isinstance(ent, dict):
        return None
    saved = float(ent.get("saved_unix", 0))
    ttl = int(ent.get("ttl_sec", 60))
    if t - saved > ttl:
        return None
    return int(ent.get("version", 0)), ent.get("payload")


def l1_set(
    doc: Dict[str, Any],
    key: str,
    payload: Any,
    *,
    ttl_sec: int,
) -> int:
    with _l1_lock:
        ver = _next_version(doc)
        doc.setdefault("l1", {})
        doc["l1"][key] = {
            "version": ver,
            "saved_unix": time.time(),
            "ttl_sec": ttl_sec,
            "payload": payload,
        }
        return ver


def l1_get_any(
    doc: Dict[str, Any], key: str
) -> Optional[Tuple[int, Any, float]]:
    """Сырой доступ (для stale-first): version, payload, saved_unix."""
    ent = doc.get("l1", {}).get(key)
    if not isinstance(ent, dict):
        return None
    return (
        int(ent.get("version", 0)),
        ent.get("payload"),
        float(ent.get("saved_unix", 0)),
    )


def l2_deps_match(doc: Dict[str, Any], deps: Dict[str, int]) -> bool:
    for k, ver in deps.items():
        ent = doc.get("l1", {}).get(k)
        if not isinstance(ent, dict):
            return False
        if int(ent.get("version", 0)) != int(ver):
            return False
    return True


def l2_get(
    doc: Dict[str, Any],
    key: str,
    *,
    ttl_sec: int,
    require_fresh: bool,
    allow_stale: bool,
) -> Optional[Dict[str, Any]]:
    """
    Вернёт запись L2 (dict с text/payload/deps) или None.

    * require_fresh: как при --refresh — не принимать L2.
    * allow_stale: если TTL L2 вышел, всё равно вернуть запись (для бота).
    """
    if require_fresh:
        return None
    ent = doc.get("l2", {}).get(key)
    if not isinstance(ent, dict):
        return None
    saved = float(ent.get("saved_unix", 0))
    if time.time() - saved > ttl_sec and not allow_stale:
        return None
    return ent


def l2_set(
    doc: Dict[str, Any],
    key: str,
    *,
    ttl_sec: int,
    text: str,
    deps: Dict[str, int],
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    doc.setdefault("l2", {})
    doc["l2"][key] = {
        "saved_unix": time.time(),
        "ttl_sec": ttl_sec,
        "text": text,
        "deps": dict(deps),
        "payload": payload or {},
    }


def invalidate_l2_keys(doc: Dict[str, Any], prefixes: Tuple[str, ...]) -> None:
    """Удалить L2-ключи, начинающиеся с любого из префиксов (после обновления L1)."""
    l2 = doc.get("l2", {})
    if not isinstance(l2, dict):
        return
    rm = [k for k in l2.keys() if any(str(k).startswith(p) for p in prefixes)]
    for k in rm:
        del l2[k]


def migrate_legacy_summary_cache(
    doc: Dict[str, Any],
    *,
    legacy_path: Path,
    cache_key: Dict[str, Any],
    cache_version: int,
    ttl_sec: int,
) -> bool:
    """Импорт из старого .rates_summary_cache.json в L2 (без L1-deps)."""
    l2_key = f"l2:summary:{stable_digest(cache_key)}"
    if l2_key in doc.get("l2", {}):
        return False
    if not legacy_path.is_file():
        return False
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if raw.get("v") != cache_version:
        return False
    if raw.get("key") != cache_key:
        return False
    rows = raw.get("rows") or []
    baseline = float(raw.get("baseline", 0))
    warnings = list(raw.get("warnings", []))
    l2_set(
        doc,
        l2_key,
        ttl_sec=ttl_sec,
        text="",
        deps={},
        payload={"rows": rows, "baseline": baseline, "warnings": warnings},
    )
    doc["l2"][l2_key]["saved_unix"] = float(raw.get("saved_unix", time.time()))
    return True


def migrate_legacy_usdt_cache(
    doc: Dict[str, Any],
    *,
    legacy_path: Path,
    usdt_key: Dict[str, Any],
    cache_version: int = 1,
) -> bool:
    l2_key = "l2:usdt:default"
    if l2_key in doc.get("l2", {}):
        return False
    if not legacy_path.is_file():
        return False
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if raw.get("v") != cache_version:
        return False
    if raw.get("key") != usdt_key:
        return False
    data = raw.get("data") or {}
    warnings = list(raw.get("warnings", []))
    l2_set(
        doc,
        l2_key,
        ttl_sec=TTL_L2_USDT_SEC,
        text="",
        deps={},
        payload={"data": data, "warnings": warnings},
    )
    doc["l2"][l2_key]["saved_unix"] = float(raw.get("saved_unix", time.time()))
    return True
