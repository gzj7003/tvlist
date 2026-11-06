from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

import cv2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(os.getenv("IPTV_SCANNER_HOME", r"D:\APP\IPTV-Scanner"))
SOURCE_DIR = BASE_DIR / "udpzj"
OUTPUT_DIR = BASE_DIR / "txt_files"

# FOFA 单页返回条数，不是总结果数。程序会持续翻页，直到读取完所有结果。
PER_PAGE_RESULTS = 100
FOFA_PAGE_DELAY = float(os.getenv("FOFA_PAGE_DELAY", "0.8"))
HTTP_TIMEOUT = (8, 30)
VALIDATION_WORKERS = max(1, int(os.getenv("IPTV_VALIDATION_WORKERS", "8")))

FOFA_API_URL = "https://api.fofa.info/v1/search"
FOFA_KEY_API_URL = "https://fofa.info/api/v1/search/all"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

EXTRA_CHANNELS = """# 苏州新闻综合
苏州新闻综合,https://live-auth.51kandianshi.com/szgd/csztv1.m3u8
# 苏州社会经济
苏州社会经济,https://live-auth.51kandianshi.com/szgd/csztv2.m3u8
# 苏州文化生活
苏州文化生活,https://live-auth.51kandianshi.com/szgd/csztv3.m3u8
# 苏州生活资讯
苏州生活资讯,https://live-auth.51kandianshi.com/szgd/csztv5.m3u8
# 苏州4K
苏州4K,https://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8"""

_URL_RE = re.compile(
    r"(?<![0-9A-Za-z_.-])https?://"
    r"(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}(?![0-9])",
    re.IGNORECASE,
)
_HOST_PORT_RE = re.compile(
    r"(?<![0-9A-Za-z_.-])(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}(?![0-9])"
)
_RTP_RE = re.compile(r"rtp://[^\s,]+", re.IGNORECASE)

log = logging.getLogger("iptv-scanner")


@dataclass(frozen=True)
class Source:
    province: str
    isp: str
    path: Path
    content: str
    multicast: str


@dataclass(frozen=True)
class FofaCredentials:
    api_key: str = ""
    cookie: str = ""


class FofaSearchError(RuntimeError):
    pass


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def read_secret_file(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    text = re.sub(r"^(?:cookie|key)\s*:\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"[\r\n]+", " ", text).strip()


def load_credentials() -> FofaCredentials:
    api_key = (
        os.getenv("FOFA_API_KEY", "").strip()
        or os.getenv("FOFA_KEY", "").strip()
        or read_secret_file(BASE_DIR / "fofa_key.txt")
    )
    cookie = (
        os.getenv("FOFA_COOKIE", "").strip()
        or read_secret_file(BASE_DIR / "fofa_cookie.txt")
    )
    return FofaCredentials(api_key=api_key, cookie=cookie)


def create_session(cookie: str = "") -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": "https://fofa.info/",
            "Accept": "application/json, text/plain, */*",
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def load_sources(source_dir: Path) -> list[Source]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source_dir}")

    sources: list[Source] = []
    for path in sorted(source_dir.glob("*.txt")):
        stem_parts = path.stem.split("_")
        if len(stem_parts) != 2:
            log.warning("跳过文件名不符合“省份_运营商.txt”的文件：%s", path.name)
            continue

        province, isp = (part.strip() for part in stem_parts)
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            log.error("文件不是 UTF-8 编码，已跳过：%s", path)
            continue

        match = _RTP_RE.search(content)
        if not match:
            log.warning("文件内未找到 rtp:// 地址，已跳过：%s", path.name)
            continue

        sources.append(
            Source(
                province=province,
                isp=isp,
                path=path,
                content=content.strip() + "\n",
                multicast=match.group(0)[len("rtp://") :].strip(),
            )
        )
    return sources


def get_organization(province: str, isp: str) -> str:
    if isp == "联通":
        if province == "北京":
            return "China Unicom Beijing Province Network"
        return "CHINA UNICOM China169 Backbone"
    if isp == "电信":
        return "Chinanet"
    if isp == "移动":
        return "China Mobile communications corporation"
    raise ValueError(f"不支持的运营商：{province}_{isp}")


def build_fofa_query(province: str, isp: str) -> str:
    org = get_organization(province, isp)
    return (
        f'"udpxy" && country="CN" && region="{province}" && org="{org}"'
    )


def encode_query(query: str) -> str:
    return base64.b64encode(query.encode("utf-8")).decode("ascii")


def normalize_base_url(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None

    value = raw.strip().strip("\"'[],")
    if not value:
        return None
    if _HOST_PORT_RE.fullmatch(value):
        value = f"http://{value}"

    match = _URL_RE.search(value)
    if not match:
        return None

    parsed = urlparse(match.group(0))
    if not parsed.hostname or parsed.port is None:
        return None
    try:
        ipaddress.IPv4Address(parsed.hostname)
    except ValueError:
        return None
    if not 1 <= parsed.port <= 65535:
        return None

    scheme = parsed.scheme.lower()
    return f"{scheme}://{parsed.hostname}:{parsed.port}"


def asset_to_base_urls(asset: Any) -> list[str]:
    candidates: list[Any] = []
    if isinstance(asset, str):
        candidates.append(asset)
    elif isinstance(asset, Mapping):
        for key in ("host", "url", "link", "endpoint"):
            if asset.get(key):
                candidates.append(asset[key])

        ip = asset.get("ip")
        port = asset.get("port")
        if ip and port:
            candidates.append(f"http://{ip}:{port}")

        for port_info in asset.get("port_list") or []:
            if not isinstance(port_info, Mapping):
                continue
            port_value = port_info.get("port")
            if not ip or not port_value:
                continue
            scheme = "https" if str(port_info.get("protocol", "")).lower() == "https" else "http"
            candidates.append(f"{scheme}://{ip}:{port_value}")
    elif isinstance(asset, Sequence) and not isinstance(asset, (bytes, bytearray)):
        candidates.extend(asset)

    result: list[str] = []
    for candidate in candidates:
        base_url = normalize_base_url(str(candidate))
        if base_url and base_url not in result:
            result.append(base_url)
    return result


def extract_rows_and_total(payload: Mapping[str, Any]) -> tuple[list[Any], int]:
    code = payload.get("code")
    if payload.get("error") is True or code not in (None, 0, "0"):
        message = payload.get("message") or payload.get("errmsg") or "FOFA 请求失败"
        raise FofaSearchError(f"{message}（code={code}）")

    data = payload.get("data")
    if isinstance(data, Mapping):
        rows = data.get("assets")
        if not isinstance(rows, list):
            rows = data.get("results")
        if not isinstance(rows, list):
            rows = data.get("list")
        if not isinstance(rows, list):
            rows = []

        page_data = data.get("page")
        total = (
            page_data.get("total")
            if isinstance(page_data, Mapping)
            else data.get("total")
        )
    elif isinstance(data, list):
        rows = data
        total = payload.get("total")
    else:
        rows = payload.get("results")
        if rows is None:
            rows = payload.get("list")
        if not isinstance(rows, list):
            rows = []
        total = payload.get("total")

    try:
        total_number = int(total or 0)
    except (TypeError, ValueError):
        total_number = 0
    return rows, max(0, total_number)


def request_search_page(
    session: requests.Session,
    query: str,
    page: int,
    credentials: FofaCredentials,
) -> tuple[list[Any], int]:
    params: dict[str, Any] = {
        "qbase64": encode_query(query),
        "page": page,
        "size": PER_PAGE_RESULTS,
    }

    if credentials.api_key:
        endpoint = FOFA_KEY_API_URL
        params["key"] = credentials.api_key
        params["fields"] = "host,ip,port"
    elif credentials.cookie:
        endpoint = FOFA_API_URL
    else:
        raise FofaSearchError(
            "缺少 FOFA 凭据。请设置环境变量 FOFA_API_KEY，"
            "或将 API Key 保存到 D:\\APP\\IPTV-Scanner\\fofa_key.txt。"
        )

    response = session.get(endpoint, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise FofaSearchError("FOFA 返回的内容不是有效 JSON") from exc
    if not isinstance(payload, Mapping):
        raise FofaSearchError("FOFA 返回的数据结构无效")
    return extract_rows_and_total(payload)


def search_all_base_urls(
    session: requests.Session,
    query: str,
    credentials: FofaCredentials,
) -> list[str]:
    """分页读取全部结果；100 只是单页大小，不是总条数上限。"""
    page = 1
    total = 0
    base_urls: list[str] = []
    seen_urls: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()

    while True:
        rows, page_total = request_search_page(session, query, page, credentials)
        if page_total:
            total = page_total

        extracted: list[str] = []
        for row in rows:
            for base_url in asset_to_base_urls(row):
                if base_url not in seen_urls:
                    seen_urls.add(base_url)
                    base_urls.append(base_url)
                if base_url not in extracted:
                    extracted.append(base_url)

        log.info(
            "FOFA 第 %d 页：返回 %d 条，累计唯一地址 %d 个，总计 %s 条",
            page,
            len(rows),
            len(base_urls),
            total if total else "未知",
        )

        if not rows:
            break

        page_signature = tuple(sorted(extracted)) or tuple(
            str(row)[:200] for row in rows
        )
        if page_signature in seen_pages:
            log.warning("FOFA 返回了重复页面，停止翻页以避免死循环")
            break
        seen_pages.add(page_signature)

        if total and page * PER_PAGE_RESULTS >= total:
            break
        if not total and len(rows) < PER_PAGE_RESULTS:
            break

        page += 1
        if FOFA_PAGE_DELAY > 0:
            time.sleep(FOFA_PAGE_DELAY)

    log.info("“%s”全部结果读取完成，共得到 %d 个唯一地址", query, len(base_urls))
    return base_urls


def validate_proxy(base_url: str, multicast: str) -> bool:
    video_url = f"{base_url}/rtp/{multicast}"
    capture = cv2.VideoCapture()
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
    capture.open(video_url)
    try:
        if not capture.isOpened():
            return False
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width > 0 and height > 0:
            log.info("有效代理：%s（%dx%d）", video_url, width, height)
            return True
        return False
    finally:
        capture.release()


def validate_all(
    base_urls: Iterable[str],
    multicast: str,
) -> list[str]:
    candidates = list(dict.fromkeys(base_urls))
    if not candidates:
        return []

    valid_indices: set[int] = set()
    total = len(candidates)
    log.info("开始验证 %d 个代理，并发数 %d", total, VALIDATION_WORKERS)

    with ThreadPoolExecutor(max_workers=VALIDATION_WORKERS) as executor:
        futures = {
            executor.submit(validate_proxy, base_url, multicast): index
            for index, base_url in enumerate(candidates)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            index = futures[future]
            try:
                if future.result():
                    valid_indices.add(index)
            except Exception:
                log.exception("验证代理失败：%s", candidates[index])
            if completed % 20 == 0 or completed == total:
                log.info("代理验证进度：%d/%d，有效 %d 个", completed, total, len(valid_indices))

    return [candidates[index] for index in sorted(valid_indices)]


def build_playlist(source: Source, base_urls: Sequence[str]) -> str:
    blocks = [
        source.content.replace("rtp://", f"{base_url.rstrip('/')}/rtp/")
        for base_url in base_urls
    ]
    body = "\n\n".join(block.rstrip() for block in blocks)
    return f"{body}\n\n{EXTRA_CHANNELS.rstrip()}\n"


def write_playlist(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def process_source(
    source: Source,
    session: requests.Session,
    credentials: FofaCredentials,
) -> None:
    try:
        query = build_fofa_query(source.province, source.isp)
    except ValueError as exc:
        log.warning("%s，跳过 %s", exc, source.path.name)
        return

    log.info("查询 %s%s，组播地址 %s", source.province, source.isp, source.multicast)
    try:
        base_urls = search_all_base_urls(session, query, credentials)
    except (FofaSearchError, requests.RequestException) as exc:
        log.error("%s%s 搜索失败：%s", source.province, source.isp, exc)
        return

    valid_urls = validate_all(base_urls, source.multicast)
    if not valid_urls:
        log.warning("%s%s 没有验证到有效代理，不覆盖已有输出", source.province, source.isp)
        return

    output_path = OUTPUT_DIR / f"{source.province}{source.isp}.txt"
    write_playlist(output_path, build_playlist(source, valid_urls))
    log.info(
        "%s%s 已生成播放列表，有效代理 %d 个：%s",
        source.province,
        source.isp,
        len(valid_urls),
        output_path,
    )


def main() -> None:
    configure_logging()
    try:
        sources = load_sources(SOURCE_DIR)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return

    credentials = load_credentials()
    if not credentials.api_key and not credentials.cookie:
        log.error(
            "未找到 FOFA 凭据。请设置 FOFA_API_KEY，或写入 %s",
            BASE_DIR / "fofa_key.txt",
        )
        return

    log.info("本次查询 %d 个省份/运营商文件，FOFA 结果总数不设上限", len(sources))
    with create_session(credentials.cookie) as session:
        for source in sources:
            process_source(source, session, credentials)

    log.info("节目表制作完成，文件位于：%s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
