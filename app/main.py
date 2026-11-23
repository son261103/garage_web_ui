"""
Garage Web UI — backend (FastAPI).

Giao diện quản trị Garage bằng tiếng Việt, chạy trong 1 container Docker
cùng mạng với Garage. Trình duyệt chỉ nói chuyện với backend này;
backend mới là bên gọi Garage Admin API bằng admin token.

Admin API — kết quả tra cứu từ tài liệu chính thức của Garage
(https://garagehq.deuxfleurs.fr, OpenAPI garage-admin-v2.json, bản v2.5.0)
và mã nguồn garage-webui (khairul169/garage-webui):
  * API v2 là bản hiện hành trên Garage 2.x (v1 đã deprecated từ Garage 2.0).
    V2 dùng kiểu POST /v2/<TênThaoTác> với JSON body, xác thực bằng header
    "Authorization: Bearer <admin_token>".
  * API v1 (deprecated) dùng kiểu REST: GET /v1/status, GET /v1/bucket?list,
    POST /v1/bucket, POST /v1/bucket/allow, POST /v1/key, ...
    Backend này vẫn giữ v1 làm đường dự phòng (fallback) nếu v2 báo endpoint
    không tồn tại (404/405, hoặc 400 kèm "Unknown API endpoint" — Garage trả
    400 cho endpoint v2 lạ thay vì 404), để tương thích với Garage cũ.
    Trên Garage 2.4.1 (roser) v2 là đường chính.

Các endpoint v2 đã kiểm chứng trong OpenAPI spec (garage-admin-v2.json v2.5.0):
  GET  /v2/GetClusterStatus        -> trạng thái cluster (node, layout)
  GET  /v2/GetClusterHealth        -> {status: healthy|degraded|unavailable,
                                        knownNodes, connectedNodes,
                                        storageNodes, storageNodesUp,
                                        partitions, partitionsQuorum,
                                        partitionsAllOk}
  GET  /v2/GetClusterLayout        -> layout hiện tại + staged changes
  GET  /v2/ListBuckets             -> [{id, created, globalAliases,
                                        localAliases}] — chỉ id + alias
  GET  /v2/GetBucketInfo?id= | ?globalAlias=
                                   -> {id, globalAliases[], objects, bytes,
                                       quotas:{maxSize,maxObjects},
                                       keys:[{accessKeyId,name,permissions:
                                               {read,write,owner}}], ...}
  POST /v2/CreateBucket            {globalAlias} -> GetBucketInfoResponse
  POST /v2/DeleteBucket?id=<id>    -> 200, 400 nếu bucket chưa rỗng
  GET  /v2/ListKeys                -> [{id, name, expired}] — chỉ id + tên
  GET  /v2/GetKeyInfo?id= | ?search= (&showSecretKey=true để lấy secret)
  LƯU Ý TƯƠNG THÍCH: /v2/ListBucketsInfo và /v2/ListKeysInfo (trả full info
  trong 1 lần gọi) chỉ có từ Garage v2.5.0+. Roser chạy v2.4.1 nên KHÔNG có
  2 endpoint này — gọi sẽ nhận 400 "Unknown API endpoint" (đã gặp live
  23/11/2026). Vì vậy code dùng ListBuckets/ListKeys rồi gọi GetBucketInfo /
  GetKeyInfo theo từng id để lấy đầy đủ thông tin; cách này chạy được trên
  v2.4.1 trở lên (kể cả v2.5.0).
  POST /v2/CreateKey               {name} -> {accessKeyId, secretAccessKey, ...}
  POST /v2/DeleteKey?id=<id>
  POST /v2/AllowBucketKey          {bucketId, accessKeyId,
                                    permissions:{read,write,owner}}
                                   (chỉ bật các cờ = true, cờ false giữ nguyên)
  POST /v2/DenyBucketKey           {bucketId, accessKeyId, permissions:{...}}
                                   (chỉ tắt các cờ = true, cờ false giữ nguyên)
  -> Muốn đặt chính xác quyền read/write/owner: so sánh với quyền hiện tại,
     gọi Allow cho cờ cần bật, Deny cho cờ cần tắt (xem set_bucket_permissions).

S3 (duyệt / tải lên / tải xuống file) dùng boto3 với endpoint của Garage,
signature v4, addressing_style = path (Garage không có root_domain nên
không dùng virtual-host style được). Cần S3_ACCESS_KEY_ID +
S3_SECRET_ACCESS_KEY; nếu thiếu, tab File báo rõ và các tab khác vẫn chạy.
"""

from __future__ import annotations

import io
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Cấu hình (chỉ đọc từ biến môi trường — không hardcode secret nào ở đây)
# ---------------------------------------------------------------------------
ADMIN_URL = os.environ.get("GARAGE_ADMIN_URL", "http://garage:3903").rstrip("/")
ADMIN_TOKEN = os.environ.get("GARAGE_ADMIN_TOKEN", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "http://garage:3900").rstrip("/")
S3_REGION = os.environ.get("S3_REGION", "garage")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class GarageError(Exception):
    """Lỗi trả về từ Garage Admin API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _is_missing_endpoint(exc: GarageError) -> bool:
    """True khi Garage báo endpoint không tồn tại.

    Garage trả 404/405 cho endpoint lạ, nhưng với endpoint v2 không tồn tại
    (vd /v2/ListBucketsInfo trên Garage 2.4.1) nó trả 400 kèm message
    "Unknown API endpoint: GET /v2/...". Cả 2 dạng đều được coi là
    "endpoint không tồn tại" để fallback sang v1.
    """
    if exc.status in (404, 405):
        return True
    return exc.status == 400 and "Unknown API endpoint" in (exc.message or "")


# ---------------------------------------------------------------------------
# Client gọi Garage Admin API (v2 chính, v1 dự phòng)
# ---------------------------------------------------------------------------
class GarageAdmin:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> Any:
        if not self.token:
            raise GarageError(503, "Chưa cấu hình GARAGE_ADMIN_TOKEN. Hãy điền token quản trị vào file .env của Garage rồi khởi động lại container admin-ui.")
        url = f"{self.base_url}{path}"
        try:
            # trust_env=False: KHÔNG dùng proxy hệ thống (HTTP_PROXY/NO_PROXY)
            # khi gọi Garage trong mạng Docker / localhost. Dùng proxy ở đây
            # vừa sai (Garage không đi qua internet) vừa có thể làm httpx
            # lỗi khi parse NO_PROXY chứa địa chỉ IPv6 dạng [::1].
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.request(
                    method, url, headers=self._headers(), json=json, params=params
                )
        except httpx.ConnectError:
            raise GarageError(503, f"Không kết nối được tới Garage Admin API tại {self.base_url}. Hãy kiểm tra container garage có đang chạy không (docker compose ps).")
        except httpx.TimeoutException:
            raise GarageError(504, "Garage Admin API phản hồi quá chậm (hết thời gian chờ 30 giây).")
        except httpx.HTTPError as exc:
            raise GarageError(502, f"Lỗi khi gọi Garage Admin API: {exc}")
        if resp.status_code >= 400:
            raise GarageError(resp.status_code, _garage_error_text(resp))
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def _call_v2_or_v1(self, v2, v1):
        """Thử v2 trước; nếu v2 báo endpoint không tồn tại thì thử v1."""
        try:
            return await v2(), "v2"
        except GarageError as exc:
            if _is_missing_endpoint(exc):
                return await v1(), "v1"
            raise

    # -- trạng thái / layout -------------------------------------------------
    async def health(self) -> dict:
        try:
            return await self._request("GET", "/v2/GetClusterHealth"), "v2"
        except GarageError as exc:
            if _is_missing_endpoint(exc):
                return await self._request("GET", "/v1/health"), "v1"
            raise

    async def status(self) -> tuple[Any, str]:
        return await self._call_v2_or_v1(
            lambda: self._request("GET", "/v2/GetClusterStatus"),
            lambda: self._request("GET", "/v1/status"),
        )

    async def layout(self) -> tuple[Any, str]:
        return await self._call_v2_or_v1(
            lambda: self._request("GET", "/v2/GetClusterLayout"),
            lambda: self._request("GET", "/v1/layout"),
        )

    # -- bucket ---------------------------------------------------------------
    async def _enrich_buckets(self, items: list[dict]) -> list[dict]:
        """Lấy đầy đủ thông tin từng bucket (objects/bytes/keys/quotas).

        ListBuckets (v2) và list v1 chỉ trả id + alias, nên gọi thêm
        GetBucketInfo theo từng id. Bucket nào lỗi thì giữ nguyên bản
        liệt kê để giao diện vẫn hiện tên, không vỡ cả danh sách.
        """
        out = []
        for item in items:
            bid = item.get("id") or item.get("bucketId")
            if bid:
                try:
                    info, _ = await self.bucket_info(bucket_id=bid)
                    out.append(info)
                except GarageError:
                    out.append(item)
            else:
                out.append(item)
        return out

    async def list_buckets(self) -> tuple[list[dict], str]:
        async def v2():
            data = await self._request("GET", "/v2/ListBuckets")
            return await self._enrich_buckets(_as_list(data))

        async def v1():
            data = await self._request("GET", "/v1/bucket", params={"list": ""})
            return await self._enrich_buckets(_as_list(data))

        return await self._call_v2_or_v1(v2, v1)

    async def bucket_info(
        self, *, bucket_id: str | None = None, alias: str | None = None
    ) -> tuple[dict, str]:
        params: dict[str, str] = {}
        if bucket_id:
            params["id"] = bucket_id
        if alias:
            params["globalAlias"] = alias

        async def v2():
            return await self._request("GET", "/v2/GetBucketInfo", params=params)

        async def v1():
            return await self._request("GET", "/v1/bucket", params=params)

        return await self._call_v2_or_v1(v2, v1)

    async def create_bucket(self, name: str) -> tuple[dict, str]:
        async def v2():
            return await self._request("POST", "/v2/CreateBucket", json={"globalAlias": name})

        async def v1():
            return await self._request("POST", "/v1/bucket", json={"globalAlias": name})

        return await self._call_v2_or_v1(v2, v1)

    async def delete_bucket(self, bucket_id: str) -> tuple[Any, str]:
        async def v2():
            return await self._request("POST", "/v2/DeleteBucket", params={"id": bucket_id})

        async def v1():
            return await self._request("DELETE", "/v1/bucket", params={"id": bucket_id})

        return await self._call_v2_or_v1(v2, v1)

    # -- key ------------------------------------------------------------------
    async def _enrich_keys(self, items: list[dict]) -> list[dict]:
        """Lấy đầy đủ thông tin từng key (buckets, permissions).

        ListKeys (v2) chỉ trả {id, name, expired} nên gọi thêm GetKeyInfo
        theo từng id. Key nào lỗi thì giữ nguyên bản liệt kê.
        Chú ý: ListKeys trả trường "id" là Key ID, còn GetKeyInfo trả
        "accessKeyId" — normalize_key xử lý cả hai.
        """
        out = []
        for item in items:
            kid = item.get("id") or item.get("accessKeyId")
            if kid:
                try:
                    info, _ = await self.key_info(kid)
                    out.append(info)
                except GarageError:
                    out.append(item)
            else:
                out.append(item)
        return out

    async def list_keys(self) -> tuple[list[dict], str]:
        async def v2():
            data = await self._request("GET", "/v2/ListKeys")
            return await self._enrich_keys(_as_list(data))

        async def v1():
            data = await self._request("GET", "/v1/key", params={"list": ""})
            return await self._enrich_keys(_as_list(data))

        return await self._call_v2_or_v1(v2, v1)

    async def key_info(self, key_id: str) -> tuple[dict, str]:
        async def v2():
            return await self._request(
                "GET", "/v2/GetKeyInfo", params={"id": key_id, "showSecretKey": "false"}
            )

        async def v1():
            return await self._request("GET", "/v1/key", params={"id": key_id})

        return await self._call_v2_or_v1(v2, v1)

    async def create_key(self, name: str) -> tuple[dict, str]:
        async def v2():
            return await self._request("POST", "/v2/CreateKey", json={"name": name})

        async def v1():
            return await self._request("POST", "/v1/key", json={"name": name})

        return await self._call_v2_or_v1(v2, v1)

    async def delete_key(self, key_id: str) -> tuple[Any, str]:
        async def v2():
            return await self._request("POST", "/v2/DeleteKey", params={"id": key_id})

        async def v1():
            return await self._request("DELETE", "/v1/key", params={"id": key_id})

        return await self._call_v2_or_v1(v2, v1)

    async def set_bucket_permissions(
        self, bucket_id: str, key_id: str, *, read: bool, write: bool, owner: bool
    ) -> dict:
        """Đặt chính xác quyền read/write/owner.

        Lưu ý ngữ nghĩa đặc biệt của Garage (ghi trong OpenAPI spec):
        Allow chỉ bật cờ = true, Deny chỉ tắt cờ = true, cờ = false giữ nguyên.
        Vì vậy phải đọc quyền hiện tại rồi gọi Allow/Deny theo từng cờ thay đổi.
        """
        info, _ = await self.bucket_info(bucket_id=bucket_id)
        current = {"read": False, "write": False, "owner": False}
        for entry in info.get("keys") or []:
            if entry.get("accessKeyId") == key_id:
                perms = entry.get("permissions") or {}
                current = {
                    "read": bool(perms.get("read")),
                    "write": bool(perms.get("write")),
                    "owner": bool(perms.get("owner")),
                }
        wanted = {"read": read, "write": write, "owner": owner}
        to_allow = {k: True for k, v in wanted.items() if v and not current[k]}
        to_deny = {k: True for k, v in wanted.items() if not v and current[k]}
        result: dict = {}
        if to_allow:
            result = await self._allow_or_deny("AllowBucketKey", bucket_id, key_id, to_allow)
        if to_deny:
            result = await self._allow_or_deny("DenyBucketKey", bucket_id, key_id, to_deny)
        if not to_allow and not to_deny:
            return info
        return result

    async def _allow_or_deny(
        self, op: str, bucket_id: str, key_id: str, permissions: dict
    ) -> dict:
        body = {"bucketId": bucket_id, "accessKeyId": key_id, "permissions": permissions}

        async def v2():
            return await self._request("POST", f"/v2/{op}", json=body)

        async def v1():
            endpoint = "allow" if op == "AllowBucketKey" else "deny"
            return await self._request("POST", f"/v1/bucket/{endpoint}", json=body)

        data, _ = await self._call_v2_or_v1(v2, v1)
        return data


def _garage_error_text(resp: httpx.Response) -> str:
    """Trích thông báo lỗi từ Garage (JSON hoặc text thuần)."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            if data.get(key):
                return str(data[key])
        return str(data)
    text = (resp.text or "").strip()
    if not text:
        return f"Garage trả về lỗi HTTP {resp.status_code}."
    if "Bucket is not empty" in text or "not empty" in text:
        return "Bucket chưa rỗng nên không xóa được. Hãy xóa hết file trong bucket trước."
    if "already exists" in text.lower():
        return "Tên này đã tồn tại. Hãy chọn tên khác."
    return text


def _as_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("buckets", "keys", "items", "results"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


# ---------------------------------------------------------------------------
# Chuẩn hóa dữ liệu trả về (v2 và v1 có hình dạng hơi khác nhau)
# ---------------------------------------------------------------------------
def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_node(node: dict) -> dict:
    role = node.get("role") or {}
    if not isinstance(role, dict):
        role = {}
    capacity = node.get("capacity", role.get("capacity"))
    zone = node.get("zone", role.get("zone"))
    return {
        "id": node.get("id") or node.get("nodeId") or node.get("node") or "",
        "hostname": node.get("hostname") or node.get("host") or "",
        "addr": node.get("addr") or node.get("address") or "",
        "version": node.get("version") or node.get("garageVersion") or "",
        "zone": zone or "",
        "capacity": _num(capacity),
        "dataAvail": _num(node.get("dataAvail") or node.get("dataAvailable")),
        "isUp": bool(node.get("isUp", node.get("connected", node.get("isConnected", True)))),
        "tags": node.get("tags") or role.get("tags") or [],
    }


def normalize_status(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    nodes_raw = raw.get("nodes") or []
    if isinstance(nodes_raw, dict):
        nodes_raw = list(nodes_raw.values())
    nodes = [normalize_node(n) for n in nodes_raw if isinstance(n, dict)]
    layout = raw.get("layout") or {}
    if not isinstance(layout, dict):
        layout = {}
    return {
        "node": raw.get("node") or (nodes[0]["id"] if nodes else ""),
        "garageVersion": raw.get("garageVersion") or raw.get("version") or (nodes[0]["version"] if nodes else ""),
        "nodes": nodes,
        "layoutVersion": _num(layout.get("version", raw.get("layoutVersion"))),
        "layout": layout,
        "stagedLayout": raw.get("stagedLayout") or raw.get("staged") or {},
        "raw": raw,
    }


def normalize_bucket(raw: dict) -> dict:
    aliases = raw.get("globalAliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    name = aliases[0] if aliases else (raw.get("globalAlias") or raw.get("id") or "")
    quotas = raw.get("quotas") or {}
    keys = []
    for entry in raw.get("keys") or []:
        if not isinstance(entry, dict):
            continue
        perms = entry.get("permissions") or {}
        keys.append(
            {
                "accessKeyId": entry.get("accessKeyId") or entry.get("id") or "",
                "name": entry.get("name") or "",
                "permissions": {
                    "read": bool(perms.get("read")),
                    "write": bool(perms.get("write")),
                    "owner": bool(perms.get("owner")),
                },
            }
        )
    return {
        "id": raw.get("id") or "",
        "name": name,
        "globalAliases": aliases,
        "objects": _num(raw.get("objects")),
        "bytes": _num(raw.get("bytes")),
        "created": raw.get("created") or "",
        "websiteAccess": bool(raw.get("websiteAccess")),
        "quotas": {
            "maxSize": quotas.get("maxSize"),
            "maxObjects": quotas.get("maxObjects"),
        },
        "keys": keys,
        "unfinishedUploads": _num(raw.get("unfinishedUploads")),
    }


def normalize_key(raw: dict) -> dict:
    buckets = []
    for entry in raw.get("buckets") or []:
        if isinstance(entry, dict):
            perms = entry.get("permissions") or {}
            buckets.append(
                {
                    "bucketId": entry.get("bucketId") or entry.get("id") or "",
                    "permissions": {
                        "read": bool(perms.get("read")),
                        "write": bool(perms.get("write")),
                        "owner": bool(perms.get("owner")),
                    },
                }
            )
        elif isinstance(entry, str):
            buckets.append({"bucketId": entry, "permissions": {"read": False, "write": False, "owner": False}})
    return {
        "id": raw.get("accessKeyId") or raw.get("id") or "",
        "name": raw.get("name") or "",
        "secretAccessKey": raw.get("secretAccessKey") or "",
        "created": raw.get("created") or "",
        "canCreateBucket": bool(raw.get("canCreateBucket")),
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# S3 (boto3) — chỉ dùng khi đã có S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
# ---------------------------------------------------------------------------
_s3_client = None


def s3_client():
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:
        return None
    import boto3
    from botocore.config import Config

    _s3_client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _s3_client


def s3_not_configured_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "Chưa cấu hình key S3 để duyệt file. Hãy tạo 1 key ở tab Khóa "
            "(vd tên app-key), gán quyền đọc/ghi cho bucket, rồi thêm "
            "S3_ACCESS_KEY_ID và S3_SECRET_ACCESS_KEY vào biến môi trường "
            "của container admin-ui và chạy lại: docker compose up -d admin-ui. "
            "Các tab Tổng quan, Bucket, Khóa, Bố cục vẫn dùng được bình thường."
        ),
    )


def _s3_error_to_http(exc: Exception) -> HTTPException:
    msg = str(exc)
    if "NoSuchBucket" in msg or "does not exist" in msg.lower():
        return HTTPException(status_code=404, detail="Không tìm thấy bucket này. Có thể bucket đã bị xóa.")
    if "AccessDenied" in msg or "Forbidden" in msg or "SignatureDoesNotMatch" in msg:
        return HTTPException(status_code=403, detail="Key S3 không có quyền làm việc này, hoặc Secret key sai. Hãy kiểm tra lại key và quyền của key trên bucket.")
    if "InvalidAccessKeyId" in msg:
        return HTTPException(status_code=403, detail="Key ID S3 không đúng. Hãy kiểm tra lại S3_ACCESS_KEY_ID.")
    return HTTPException(status_code=502, detail=f"Lỗi khi gọi S3: {msg}")


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Garage Web UI", version="1.0.0", docs_url="/api/docs", redoc_url=None)
admin = GarageAdmin(ADMIN_URL, ADMIN_TOKEN)


@app.exception_handler(GarageError)
async def garage_error_handler(request: Request, exc: GarageError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


@app.get("/api/health")
async def api_health():
    """Kiểm tra backend có gọi được Garage không (dùng cho Docker healthcheck)."""
    try:
        health, api_version = await admin.health()
        garage_ok = True
    except GarageError as exc:
        return {"ok": False, "garage": {"connected": False, "error": exc.message}, "s3Configured": bool(S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY)}
    status = health if isinstance(health, dict) else {}
    return {
        "ok": True,
        "garage": {
            "connected": True,
            "apiVersion": api_version,
            "health": status.get("status", "healthy"),
            "storageNodesUp": status.get("storageNodesUp"),
            "partitionsQuorum": status.get("partitionsQuorum"),
            "partitions": status.get("partitions"),
        },
        "s3Configured": bool(S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY),
    }


@app.get("/api/config")
async def api_config():
    return {
        "s3Configured": bool(S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY),
        "s3Endpoint": S3_ENDPOINT,
        "region": S3_REGION,
        "adminConfigured": bool(ADMIN_TOKEN),
        "adminUrl": ADMIN_URL,
    }


@app.get("/api/status")
async def api_status():
    """Tổng quan: health + status + layout + tổng bucket/key/object."""
    health, health_api = await admin.health()
    raw_status, status_api = await admin.status()
    status = normalize_status(raw_status)
    try:
        raw_layout, _ = await admin.layout()
    except GarageError:
        raw_layout = {}
    buckets, _ = await admin.list_buckets()
    keys, _ = await admin.list_keys()
    norm_buckets = [normalize_bucket(b) for b in buckets]
    total_objects = sum(b["objects"] for b in norm_buckets)
    total_bytes = sum(b["bytes"] for b in norm_buckets)
    total_capacity = sum(n["capacity"] for n in status["nodes"])
    health_status = health.get("status", "healthy") if isinstance(health, dict) else "healthy"
    return {
        "health": health if isinstance(health, dict) else {},
        "healthStatus": health_status,
        "apiVersion": status_api,
        "healthApiVersion": health_api,
        "nodes": status["nodes"],
        "garageVersion": status["garageVersion"],
        "layoutVersion": status["layoutVersion"],
        "layout": raw_layout if isinstance(raw_layout, dict) else {},
        "totals": {
            "buckets": len(norm_buckets),
            "keys": len(keys),
            "objects": total_objects,
            "bytes": total_bytes,
            "capacity": total_capacity,
        },
    }


@app.get("/api/layout")
async def api_layout():
    raw, api_version = await admin.layout()
    return {"apiVersion": api_version, "layout": raw if isinstance(raw, dict) else {}}


@app.get("/api/buckets")
async def api_list_buckets():
    buckets, api_version = await admin.list_buckets()
    return {"apiVersion": api_version, "buckets": [normalize_bucket(b) for b in buckets]}


@app.post("/api/buckets")
async def api_create_bucket(payload: dict):
    name = (payload.get("name") or "").strip().lower()
    if not BUCKET_NAME_RE.match(name or ""):
        raise HTTPException(
            status_code=400,
            detail="Tên bucket không hợp lệ. Tên phải dài 3–63 ký tự, chỉ gồm chữ thường, số, dấu gạch ngang (-) và dấu chấm (.), bắt đầu và kết thúc bằng chữ hoặc số (vd: media, anh-cau-hoi).",
        )
    if name in ("localhost",):
        raise HTTPException(status_code=400, detail="Tên bucket này không dùng được, hãy chọn tên khác.")
    data, _ = await admin.create_bucket(name)
    return normalize_bucket(data if isinstance(data, dict) else {})


@app.get("/api/buckets/{bucket_id}")
async def api_bucket_info(bucket_id: str):
    data, _ = await admin.bucket_info(bucket_id=bucket_id)
    return normalize_bucket(data if isinstance(data, dict) else {})


@app.delete("/api/buckets/{bucket_id}")
async def api_delete_bucket(bucket_id: str):
    await admin.delete_bucket(bucket_id)
    return {"ok": True, "message": "Đã xóa bucket."}


@app.post("/api/buckets/{bucket_id}/permissions")
async def api_bucket_permissions(bucket_id: str, payload: dict):
    key_id = (payload.get("accessKeyId") or "").strip()
    if not key_id:
        raise HTTPException(status_code=400, detail="Thiếu Key ID. Hãy chọn 1 key để gán quyền.")
    result = await admin.set_bucket_permissions(
        bucket_id,
        key_id,
        read=bool(payload.get("read")),
        write=bool(payload.get("write")),
        owner=bool(payload.get("owner")),
    )
    return normalize_bucket(result if isinstance(result, dict) else {})


@app.get("/api/keys")
async def api_list_keys():
    keys, api_version = await admin.list_keys()
    return {"apiVersion": api_version, "keys": [normalize_key(k) for k in keys]}


@app.post("/api/keys")
async def api_create_key(payload: dict):
    name = (payload.get("name") or "").strip()
    if not KEY_NAME_RE.match(name or ""):
        raise HTTPException(
            status_code=400,
            detail="Tên key không hợp lệ. Tên dài 1–64 ký tự, chỉ gồm chữ, số, dấu chấm (.), gạch dưới (_) và gạch ngang (-), bắt đầu bằng chữ hoặc số (vd: app-key, cms-key).",
        )
    data, _ = await admin.create_key(name)
    created = normalize_key(data if isinstance(data, dict) else {})
    if not created.get("secretAccessKey"):
        # Một số bản Garage chỉ trả secret ở lần tạo; nếu thiếu, báo rõ.
        created["secretAccessKey"] = data.get("secretAccessKey") or data.get("secretKey") or ""
    return created


@app.get("/api/keys/{key_id}")
async def api_key_info(key_id: str):
    data, _ = await admin.key_info(key_id)
    return normalize_key(data if isinstance(data, dict) else {})


@app.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: str):
    await admin.delete_key(key_id)
    return {"ok": True, "message": "Đã xóa key."}


# ---------------------------------------------------------------------------
# File (S3 qua boto3)
# ---------------------------------------------------------------------------
@app.get("/api/objects")
async def api_list_objects(
    bucket: str = Query(...),
    prefix: str = Query(""),
    search: str = Query(""),
):
    client = s3_client()
    if client is None:
        raise s3_not_configured_error()
    try:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Delimiter": "/", "MaxKeys": 1000}
        if prefix:
            kwargs["Prefix"] = prefix
        resp = client.list_objects_v2(**kwargs)
        folders = [
            {"key": p.get("Prefix", ""), "name": p.get("Prefix", "").rstrip("/").split("/")[-1]}
            for p in resp.get("CommonPrefixes", [])
        ]
        files = []
        for obj in resp.get("Contents", []):
            key = obj.get("Key", "")
            if key == prefix:
                continue
            if search and search.lower() not in key.lower():
                continue
            files.append(
                {
                    "key": key,
                    "name": key.split("/")[-1],
                    "size": _num(obj.get("Size")),
                    "lastModified": str(obj.get("LastModified") or ""),
                    "isImage": _is_image_key(key),
                }
            )
        if search and not prefix:
            # Tìm theo tiền tố: liệt kê sâu hơn khi người dùng gõ tìm kiếm.
            resp2 = client.list_objects_v2(Bucket=bucket, Prefix=search, MaxKeys=1000)
            seen = {f["key"] for f in files}
            for obj in resp2.get("Contents", []):
                key = obj.get("Key", "")
                if key not in seen:
                    files.append(
                        {
                            "key": key,
                            "name": key.split("/")[-1],
                            "size": _num(obj.get("Size")),
                            "lastModified": str(obj.get("LastModified") or ""),
                            "isImage": _is_image_key(key),
                        }
                    )
        return {"bucket": bucket, "prefix": prefix, "folders": folders, "files": files, "isTruncated": bool(resp.get("IsTruncated"))}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - chuyển mọi lỗi S3 thành HTTP
        raise _s3_error_to_http(exc)


def _is_image_key(key: str) -> bool:
    return key.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"))


@app.post("/api/objects/upload")
async def api_upload_objects(
    bucket: str = Query(...),
    prefix: str = Query(""),
    files: list[UploadFile] = File(...),
):
    client = s3_client()
    if client is None:
        raise s3_not_configured_error()
    if not files:
        raise HTTPException(status_code=400, detail="Chưa chọn file nào để tải lên.")
    uploaded, failed = [], []
    for upload in files:
        filename = (upload.filename or "file").split("/")[-1].split("\\")[-1]
        if not filename:
            continue
        key = f"{prefix.rstrip('/')}/{filename}" if prefix else filename
        content_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            data = await upload.read()
            client.upload_fileobj(
                io.BytesIO(data),
                bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            uploaded.append({"key": key, "name": filename, "size": len(data)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"name": filename, "error": str(exc)})
    if failed and not uploaded:
        raise HTTPException(status_code=502, detail=f"Tải lên thất bại: {failed[0]['error']}")
    return {"uploaded": uploaded, "failed": failed}


def _s3_get_object(bucket: str, key: str):
    client = s3_client()
    if client is None:
        raise s3_not_configured_error()
    try:
        return client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        raise _s3_error_to_http(exc)


@app.get("/api/objects/download")
async def api_download_object(bucket: str = Query(...), key: str = Query(...)):
    obj = _s3_get_object(bucket, key)
    filename = key.split("/")[-1]
    return StreamingResponse(
        obj["Body"],
        media_type=obj.get("ContentType") or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/objects/preview")
async def api_preview_object(bucket: str = Query(...), key: str = Query(...)):
    obj = _s3_get_object(bucket, key)
    return StreamingResponse(
        obj["Body"],
        media_type=obj.get("ContentType") or "application/octet-stream",
        headers={"Content-Disposition": "inline"},
    )


@app.delete("/api/objects")
async def api_delete_object(bucket: str = Query(...), key: str = Query(...)):
    client = s3_client()
    if client is None:
        raise s3_not_configured_error()
    try:
        if key.endswith("/"):
            # Xóa cả "thư mục": xóa mọi object có tiền tố đó.
            resp = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1000)
            targets = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if targets:
                client.delete_objects(Bucket=bucket, Delete={"Objects": targets})
            return {"ok": True, "deleted": len(targets)}
        client.delete_object(Bucket=bucket, Key=key)
        return {"ok": True, "deleted": 1}
    except Exception as exc:  # noqa: BLE001
        raise _s3_error_to_http(exc)


# ---------------------------------------------------------------------------
# Phục vụ giao diện tĩnh
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return {"message": "Garage Web UI backend đang chạy. Thiếu file giao diện index.html."}
    return FileResponse(str(index_file))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
