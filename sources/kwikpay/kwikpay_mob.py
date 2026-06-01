# -*- coding: utf-8 -*-
"""
KwikPay mobile API (mob.kwikpay.ru): POST /ru/api/v1/commissions.

Два сценария для сводки (категория TRANSFER):
  * OverseasDeposits — перевод на счёт, RUB → THB;
  * VisaDirect — перевод на карту, RUB → USD (курс RUB за 1 USD).
"""
from __future__ import annotations

import gzip
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from env_loader import patch_repo_dotenv
from rates_http import urlopen_retriable

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMISSIONS_URL = "https://mob.kwikpay.ru/ru/api/v1/commissions"
_REFRESH_SESSION_URL = "https://mob.kwikpay.ru/ru/api/v2/users/refresh_session"
_DEFAULT_APP_VERSION = "3.31.0"
_DEFAULT_SENDER_BANK_ID = "9000598"
_DEFAULT_ACCOUNT_RUB = 50_000.0
_DEFAULT_CARD_USD = 500.0


@dataclass(frozen=True)
class KwikpayMobFee:
    operation_type: str
    accepted_transfer_rub: float
    withdraw_amount: float
    withdraw_currency: str
    fee_rub: float
    api_rate: float

    @property
    def rub_per_thb(self) -> Optional[float]:
        if self.withdraw_currency != "THB" or self.withdraw_amount <= 0:
            return None
        return self.accepted_transfer_rub / self.withdraw_amount

    @property
    def rub_per_usd(self) -> Optional[float]:
        if self.withdraw_currency != "USD" or self.withdraw_amount <= 0:
            return None
        return self.accepted_transfer_rub / self.withdraw_amount

    def rub_per_thb_via_usd(self, thb_per_usd: float) -> Optional[float]:
        """RUB/THB как у Unired: ``rub_per_usd / thb_per_usd`` (VisaDirect → USD → банк TT)."""
        ru = self.rub_per_usd
        if ru is None or ru <= 0 or thb_per_usd <= 0:
            return None
        return ru / thb_per_usd


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _auth_token() -> str:
    tok = _env("KWIKPAY_AUTH_TOKEN")
    if not tok:
        raise RuntimeError("KWIKPAY_AUTH_TOKEN не задан")
    return tok


def _refresh_token() -> str:
    return _env("KWIKPAY_REFRESH_TOKEN")


def _sender_bank_id() -> str:
    return _env("KWIKPAY_SENDER_BANK_ID", _DEFAULT_SENDER_BANK_ID)


def _client_headers(*, with_auth: bool = True) -> Dict[str, str]:
    hdr = {
        "accept-language": "ru",
        "x-app-version": _env("KWIKPAY_APP_VERSION", _DEFAULT_APP_VERSION),
        "x-app-platform": "android",
        "content-type": "application/json; charset=UTF-8",
        "user-agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
    }
    if with_auth:
        hdr["x-auth-token"] = _auth_token()
    return hdr


def _api_headers() -> Dict[str, str]:
    return _client_headers(with_auth=True)


def _decode_body(raw: bytes) -> str:
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        return gzip.decompress(raw).decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


class KwikpayAuthError(RuntimeError):
    """401/403 от mob.kwikpay.ru — сессия недействительна и refresh не помог."""


def _format_http_error_detail(raw: bytes) -> str:
    text = _decode_body(raw).strip()
    if not text:
        return "(пустое тело ответа)"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:500]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type")
        if msg:
            return str(msg)
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return text[:500]


def _auth_error_message(code: int, detail: str) -> str:
    return (
        "KwikPay: сессия недействительна "
        f"(HTTP {code}): {detail}. "
        "Задайте KWIKPAY_REFRESH_TOKEN или обновите токены в приложении."
    )


def _persist_tokens(access_token: str, refresh_token: str) -> None:
    os.environ["KWIKPAY_AUTH_TOKEN"] = access_token
    os.environ["KWIKPAY_REFRESH_TOKEN"] = refresh_token
    patch_repo_dotenv(
        _REPO_ROOT,
        {
            "KWIKPAY_AUTH_TOKEN": access_token,
            "KWIKPAY_REFRESH_TOKEN": refresh_token,
        },
    )


def refresh_session(*, timeout: float = 30.0) -> Tuple[str, str]:
    """
    POST ``/ru/api/v2/users/refresh_session`` — новая пара access/refresh token.

    Обновляет ``os.environ`` и ``.env`` (``KWIKPAY_AUTH_TOKEN``, ``KWIKPAY_REFRESH_TOKEN``).
    """
    rt = _refresh_token()
    if not rt:
        raise KwikpayAuthError(
            "KWIKPAY_REFRESH_TOKEN не задан — автоматическое обновление сессии невозможно."
        )
    payload = json.dumps({"refresh_token": rt}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _REFRESH_SESSION_URL,
        data=payload,
        headers={
            **_client_headers(with_auth=False),
            "Content-Length": str(len(payload)),
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = _format_http_error_detail(e.read()[:5000])
        raise KwikpayAuthError(_auth_error_message(e.code, detail)) from e
    data = json.loads(_decode_body(raw))
    if not isinstance(data, dict):
        raise RuntimeError(f"KwikPay refresh_session: неожиданный ответ {type(data)!r}")
    try:
        access = str(data["access_token"]).strip()
        refresh = str(data["refresh_token"]).strip()
    except KeyError as e:
        raise RuntimeError(
            f"KwikPay refresh_session: нет access_token/refresh_token в ответе: {data!r}"
        ) from e
    if not access or not refresh:
        raise RuntimeError("KwikPay refresh_session: пустые access_token или refresh_token")
    _persist_tokens(access, refresh)
    return access, refresh


def _post_commissions_once(body: Dict[str, Any], *, timeout: float = 30.0) -> Dict[str, Any]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _COMMISSIONS_URL,
        data=payload,
        headers={**_api_headers(), "Content-Length": str(len(payload))},
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = _format_http_error_detail(e.read()[:5000])
        if e.code in (401, 403):
            raise KwikpayAuthError(_auth_error_message(e.code, detail)) from e
        raise RuntimeError(f"KwikPay HTTP {e.code}: {detail}") from e
    data = json.loads(_decode_body(raw))
    if not isinstance(data, dict):
        raise RuntimeError(f"KwikPay: неожиданный ответ {type(data).__name__}")
    return data


def post_commissions(body: Dict[str, Any], *, timeout: float = 30.0) -> Dict[str, Any]:
    try:
        return _post_commissions_once(body, timeout=timeout)
    except KwikpayAuthError:
        if not _refresh_token():
            raise
        refresh_session(timeout=timeout)
        return _post_commissions_once(body, timeout=timeout)


def _first_fee(data: Dict[str, Any]) -> Dict[str, Any]:
    fees = data.get("fees")
    if not isinstance(fees, list) or not fees:
        msg = data.get("message")
        raise RuntimeError(f"KwikPay: пустой fees (message={msg!r})")
    row = fees[0]
    if not isinstance(row, dict):
        raise RuntimeError("KwikPay: fees[0] не объект")
    return row


def _fee_from_row(row: Dict[str, Any], *, operation_type: str) -> KwikpayMobFee:
    try:
        pay = float(row["acceptedTransferAmount"])
        w = float(row["withdrawAmount"])
        fee = float(row.get("acceptedTotalFee") or 0)
        api_rate = float(row.get("rate") or 0)
        cur = str(row.get("withdrawCurrency") or "").upper()
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"KwikPay: неполный fee для {operation_type}: {row!r}") from e
    if pay <= 0 or w <= 0:
        raise RuntimeError(f"KwikPay: нулевые суммы для {operation_type}")
    return KwikpayMobFee(
        operation_type=operation_type,
        accepted_transfer_rub=pay,
        withdraw_amount=w,
        withdraw_currency=cur,
        fee_rub=fee,
        api_rate=api_rate,
    )


def fetch_overseas_deposits_thb(
    amount_rub: float,
    *,
    sender_bank_id: Optional[str] = None,
    timeout: float = 30.0,
) -> KwikpayMobFee:
    """Перевод на счёт: AcceptedAmount в RUB, зачисление THB."""
    body = {
        "acceptedCurrency": "RUB",
        "amount": float(amount_rub),
        "amountType": "AcceptedAmount",
        "countryCode": None,
        "operationType": "OverseasDeposits",
        "recipientAccount": None,
        "recipientBankId": None,
        "senderBankId": sender_bank_id or _sender_bank_id(),
        "values": None,
        "withdrawCurrency": "THB",
    }
    return _fee_from_row(
        _first_fee(post_commissions(body, timeout=timeout)),
        operation_type="OverseasDeposits",
    )


def fetch_visa_direct_usd(
    withdraw_usd: float,
    *,
    sender_bank_id: Optional[str] = None,
    timeout: float = 30.0,
) -> KwikpayMobFee:
    """Перевод на карту Visa: WithdrawAmount в USD, списание RUB."""
    body = {
        "acceptedCurrency": "RUB",
        "amount": float(withdraw_usd),
        "amountType": "WithdrawAmount",
        "operationType": "VisaDirect",
        "senderBankId": sender_bank_id or _sender_bank_id(),
        "values": None,
        "withdrawCurrency": "USD",
    }
    return _fee_from_row(
        _first_fee(post_commissions(body, timeout=timeout)),
        operation_type="VisaDirect",
    )


def _card_usd_for_receiving_thb(
    receiving_thb: float,
    thb_per_usd: float,
    *,
    probe_usd: Optional[float] = None,
    timeout: float = 30.0,
) -> float:
    """
    Сумма USD (WithdrawAmount) для VisaDirect: эквивалент THB на карте ≈ USD × BBL.

    Одна проба + масштабирование (как для счёта); при нелинейности — бинарный поиск.
    """
    target = float(receiving_thb)
    if target <= 0 or thb_per_usd <= 0:
        return float(probe_usd if probe_usd is not None else _DEFAULT_CARD_USD)
    usd0 = float(probe_usd if probe_usd is not None else _DEFAULT_CARD_USD)
    probe = fetch_visa_direct_usd(usd0, timeout=timeout)
    thb0 = probe.withdraw_amount * thb_per_usd
    if thb0 <= 0:
        return max(1.0, target / thb_per_usd)
    usd_est = max(1.0, target * probe.withdraw_amount / thb0)
    fee_est = fetch_visa_direct_usd(usd_est, timeout=timeout)
    thb_est = fee_est.withdraw_amount * thb_per_usd
    if thb_est >= target:
        return usd_est
    lo, hi = usd_est, max(usd_est * 2.0, 10_000.0)
    picked = usd_est
    for _ in range(10):
        mid = (lo + hi) / 2.0
        fee_mid = fetch_visa_direct_usd(mid, timeout=timeout)
        thb_mid = fee_mid.withdraw_amount * thb_per_usd
        if thb_mid >= target:
            picked = mid
            hi = mid
        else:
            lo = mid
    return max(1.0, picked)


def fetch_summary_fees(
    *,
    account_rub: Optional[float] = None,
    card_usd: Optional[float] = None,
    receiving_thb: Optional[float] = None,
    thb_per_usd: Optional[float] = None,
    timeout: float = 30.0,
) -> List[KwikpayMobFee]:
    """
    Котировки для сводки: счёт (THB) и карта (USD).

    При ``receiving_thb``:
      * счёт — подбор ``amount_rub`` (OverseasDeposits);
      * карта — подбор ``WithdrawAmount`` в USD, если задан ``thb_per_usd`` (BBL TT).
    """
    target_thb = (
        float(receiving_thb) if (receiving_thb is not None and float(receiving_thb) > 0) else None
    )
    bbl = float(thb_per_usd) if (thb_per_usd is not None and float(thb_per_usd) > 0) else None

    rub_probe = float(account_rub if account_rub is not None else _DEFAULT_ACCOUNT_RUB)
    if target_thb is not None:
        probe = fetch_overseas_deposits_thb(rub_probe, timeout=timeout)
        thb = probe.withdraw_amount
        if thb > 0:
            rub_per = probe.accepted_transfer_rub / thb
            rub_probe = max(1000.0, target_thb * rub_per)

    card_probe = float(card_usd if card_usd is not None else _DEFAULT_CARD_USD)
    if target_thb is not None and bbl is not None:
        card_probe = _card_usd_for_receiving_thb(
            target_thb, bbl, probe_usd=card_probe, timeout=timeout
        )
    out: List[KwikpayMobFee] = []
    out.append(fetch_overseas_deposits_thb(rub_probe, timeout=timeout))
    out.append(fetch_visa_direct_usd(card_probe, timeout=timeout))
    return out
