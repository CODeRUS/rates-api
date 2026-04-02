import random
import threading
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import List


PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
]


def fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_proxy_list(url: str, limit: int = 300) -> List[str]:
    try:
        text = fetch_text(url)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[:limit]
    except Exception:
        return []


def normalize_proxy(proxy: str) -> str:
    proxy = proxy.strip()
    if not proxy:
        return ""
    if proxy.startswith(("http://", "https://")):
        return proxy
    return f"http://{proxy}"


def load_existing_proxies(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [normalize_proxy(line) for line in f.readlines()]
        return [line for line in lines if line]
    except FileNotFoundError:
        return []


def save_proxies(path: str, proxies: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for proxy in proxies:
            f.write(proxy + "\n")


def check_proxy_http(proxy: str, url: str, timeout: int = 10) -> bool:
    proxy = normalize_proxy(proxy)
    if not proxy:
        return False

    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy,
        "https": proxy,
    })

    opener = urllib.request.build_opener(proxy_handler)
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0"),
    ]

    try:
        with opener.open(url, timeout=timeout) as resp:
            code = getattr(resp, "status", resp.getcode())
            return code in (200, 301, 302, 403)
    except Exception:
        return False


def collect_candidate_proxies(per_source_limit: int = 200, shuffle: bool = True) -> List[str]:
    candidates = []

    for source in PROXY_SOURCES:
        proxies = fetch_proxy_list(source, limit=per_source_limit)
        candidates.extend(proxies)

    seen = set()
    unique = []
    for proxy in candidates:
        p = normalize_proxy(proxy)
        if p and p not in seen:
            seen.add(p)
            unique.append(p)

    if shuffle:
        random.shuffle(unique)

    return unique


def find_working_proxies(
    url: str,
    need: int = 20,
    output_file: str = "working_proxies.txt",
    per_source_limit: int = 200,
    check_limit: int = 200,
    workers: int = 20,
    timeout: int = 10,
) -> List[str]:
    found: List[str] = []
    found_set = set()
    lock = threading.Lock()

    existing = load_existing_proxies(output_file)
    seen = set()
    head: List[str] = []
    for p in existing:
        if p not in seen:
            seen.add(p)
            head.append(p)

    tail = collect_candidate_proxies(per_source_limit=per_source_limit)
    tail = [p for p in tail if p not in seen][:check_limit]

    to_check = head + tail
    if not to_check:
        save_proxies(output_file, [])
        return []

    print(f"Checking {len(to_check)} proxies ({len(head)} from {output_file}, then public lists) ...")

    def worker(proxy: str):
        ok = check_proxy_http(proxy, url, timeout=timeout)
        return proxy, ok

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = set()
        iterator = iter(to_check)

        for _ in range(min(workers, len(to_check))):
            try:
                proxy = next(iterator)
            except StopIteration:
                break
            pending.add(executor.submit(worker, proxy))

        while pending and len(found) < need:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)

            for future in done:
                proxy, ok = future.result()

                with lock:
                    if ok and proxy not in found_set:
                        found.append(proxy)
                        found_set.add(proxy)
                        print(f"[OK]   {proxy}")
                    else:
                        print(f"[FAIL] {proxy}")

                if len(found) >= need:
                    break

                try:
                    next_proxy = next(iterator)
                except StopIteration:
                    continue
                pending.add(executor.submit(worker, next_proxy))

        for future in pending:
            future.cancel()

    out = found[:need]
    save_proxies(output_file, out)
    return out


if __name__ == "__main__":
    target_url = "https://ex24.pro/"
    output_file = ".ex24_proxies"

    result = find_working_proxies(
        url=target_url,
        need=3,
        output_file=output_file,
        per_source_limit=150,
        check_limit=100,
        workers=20,
        timeout=3,
    )

    print("\nDone.")
    print(f"Found: {len(result)}")
    print(f"Saved to: {output_file}")
