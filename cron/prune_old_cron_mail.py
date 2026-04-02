#!/usr/bin/env python3
"""
Drop local mbox messages that look like cron(8) notifications and are older
than CRON_MAIL_KEEP_DAYS (default 7). Other mail is left untouched.
"""
from __future__ import annotations

import email.utils
import mailbox
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional


def _mbox_path() -> str:
    p = os.environ.get("MAIL", "").strip()
    if p:
        return p
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return f"/var/mail/{user}" if user else ""


def _keep_days() -> int:
    try:
        return max(1, int(os.environ.get("CRON_MAIL_KEEP_DAYS", "7")))
    except ValueError:
        return 7


def is_cron_notification(msg: mailbox.mboxMessage) -> bool:
    subj = msg.get("Subject") or ""
    sender = msg.get("From") or ""
    if subj.startswith("Cron ") or subj.startswith("cron "):
        return True
    if "Cron <" in subj:
        return True
    if "Cron Daemon" in sender or "(Cron Daemon)" in sender:
        return True
    return False


def _parse_unixfrom_date(unixfrom: Optional[str]) -> Optional[datetime]:
    if not unixfrom or not unixfrom.startswith("From "):
        return None
    rest = unixfrom[5:].strip()
    if "  " in rest:
        _, date_part = rest.split("  ", 1)
    else:
        parts = rest.split()
        if len(parts) < 6:
            return None
        date_part = " ".join(parts[-5:])
    try:
        dt = email.utils.parsedate_to_datetime(date_part.strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def message_datetime_utc(msg: mailbox.mboxMessage) -> Optional[datetime]:
    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return _parse_unixfrom_date(msg.get_unixfrom())


def should_drop(msg: mailbox.mboxMessage, cutoff_utc: datetime) -> bool:
    if not is_cron_notification(msg):
        return False
    dt = message_datetime_utc(msg)
    if dt is None:
        return False
    return dt < cutoff_utc


def main() -> int:
    path = _mbox_path()
    if not path or not os.path.isfile(path):
        return 0
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return 0
    except OSError:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=_keep_days())
    maildir = os.path.dirname(path) or "."
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".mbox_prune_", dir=maildir)
    except OSError:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".mbox_prune_", dir=os.path.expanduser("~")
        )
    os.close(tmp_fd)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass

    keys_to_drop: list = []
    try:
        src = mailbox.mbox(path)
        src.lock()
        try:
            keys_to_drop = [k for k in src.keys() if should_drop(src[k], cutoff)]
            if keys_to_drop:
                drop_set = set(keys_to_drop)
                dst = mailbox.mbox(tmp_path)
                dst.lock()
                try:
                    for key in src.keys():
                        if key in drop_set:
                            continue
                        dst.add(src[key])
                    dst.flush()
                finally:
                    dst.unlock()
                    dst.close()
        finally:
            src.unlock()
            src.close()

        if keys_to_drop:
            os.replace(tmp_path, path)
            try:
                os.chown(path, st.st_uid, st.st_gid)
            except OSError:
                pass
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if not keys_to_drop:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
