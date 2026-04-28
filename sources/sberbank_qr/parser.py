# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ParseResult:
    kind: str  # success | ignore | alert
    exchange_rate: Optional[float]
    reason: str


def parse_sber_qr_response(payload: Any) -> ParseResult:
    if not isinstance(payload, dict):
        return ParseResult("ignore", None, "payload is not dict")

    status = payload.get("status")
    if isinstance(status, dict) and int(status.get("code") or 0) == 3:
        for err in status.get("errors") or []:
            if isinstance(err, dict) and str(err.get("id") or "") == "EFSGW-154":
                return ParseResult("alert", None, "EFSGW-154: bad upstream response")
        return ParseResult("ignore", None, "status code 3 without EFSGW-154")

    body = payload.get("body")
    if isinstance(body, dict):
        flow = str(body.get("flow") or "")
        state = str(body.get("state") or "")
        if flow == "SberPayUERouter" and state == "onEnter":
            return ParseResult("ignore", None, "router onEnter soft failure")
        if flow == "SberPayUEPayment" and state == "mainScreen":
            output = body.get("output")
            if isinstance(output, dict):
                for screen in output.get("screens") or []:
                    if not isinstance(screen, dict):
                        continue
                    for widget in screen.get("widgets") or []:
                        if not isinstance(widget, dict):
                            continue
                        props = widget.get("properties")
                        if not isinstance(props, dict):
                            continue
                        rate = props.get("exchangeRate")
                        try:
                            rate_f = float(rate)
                        except (TypeError, ValueError):
                            continue
                        if rate_f > 0:
                            return ParseResult("success", rate_f, "ok")
            return ParseResult("ignore", None, "payment mainScreen without exchangeRate")

    return ParseResult("ignore", None, "unrecognized payload")
