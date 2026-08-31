"""Huawei Cloud authentication module.

Uses the official Huawei Cloud SDK signer (huaweicloudsdkcore) for
SDK-HMAC-SHA256 signing of all API requests. No IAM tokens needed.
"""

import datetime
import hashlib
import hmac
from urllib.parse import quote, urlparse

import requests

from .regions import get_endpoint, get_region_config


class HuaweiAuth:
    """Handles authentication for Huawei Cloud APIs using AK/SK signing.

    Uses the official Huawei Cloud SDK signer for correct SDK-HMAC-SHA256
    signing of CBR/EVS/IMS API requests.
    Uses OBS V4 algorithm for OBS API.
    """

    def __init__(self, access_key, secret_key):
        self.ak = access_key
        self.sk = secret_key

    def sign_request(self, method, url, headers, body=b""):
        """Sign a request using the Huawei Cloud SDK signer.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            url: Full URL.
            headers: Dict of request headers (will be modified in place).
            body: Request body (str or bytes).

        Returns:
            Modified headers dict with Authorization header added.
        """
        from huaweicloudsdkcore.auth.credentials import BasicCredentials
        from huaweicloudsdkcore.signer.signer import Signer
        from huaweicloudsdkcore.sdk_request import SdkRequest

        parsed = urlparse(url)
        schema = parsed.scheme or "https"
        host = parsed.hostname
        path = parsed.path or "/"
        query = parsed.query

        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = b""

        query_params = []
        if query:
            for pair in query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query_params.append((k, v))
                else:
                    query_params.append((pair, ""))

        header_params = dict(headers)

        credentials = BasicCredentials(ak=self.ak, sk=self.sk)
        signer = Signer(credentials)

        sdk_request = SdkRequest(
            method=method.upper(),
            schema=schema,
            host=host,
            resource_path=path,
            uri=path,
            query_params=query_params,
            header_params=header_params,
            body=body_bytes,
        )

        signed = signer.sign(sdk_request)

        return signed.header_params

    def get_auth_headers(self, region_input):
        """Return base headers for service APIs."""
        return {"Content-Type": "application/json"}


def _sha256_hex(data):
    """Calculate SHA256 hex digest of a string or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key, msg):
    """Calculate HMAC-SHA256 returning bytes."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(msg, str):
        msg = msg.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def sign_obs_request(
    method,
    endpoint,
    bucket_name,
    object_key,
    headers,
    ak,
    sk,
    region_id,
    payload=b"",
):
    """Sign an OBS request using V4 signing algorithm."""
    now = datetime.datetime.utcnow()
    date_stamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    host = urlparse(endpoint).hostname
    if bucket_name:
        url_host = f"{bucket_name}.{host}"
    else:
        url_host = host

    canonical_uri = "/"
    if object_key:
        canonical_uri = "/" + quote(object_key, safe="/")

    url = f"https://{url_host}{canonical_uri}"

    headers["host"] = url_host
    headers["x-amz-date"] = amz_date
    if payload:
        if isinstance(payload, bytes):
            headers["x-amz-content-sha256"] = _sha256_hex(payload)
        else:
            headers["x-amz-content-sha256"] = _sha256_hex(payload)
    else:
        headers["x-amz-content-sha256"] = _sha256_hex("")

    sorted_headers = sorted([(k.lower(), v.strip()) for k, v in headers.items()])
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted_headers)
    signed_headers = ";".join(k for k, _ in sorted_headers)

    payload_hash = headers["x-amz-content-sha256"]

    canonical_query = ""
    if "?" in url:
        raw_query = url.split("?", 1)[1]
        pairs = []
        for pair in raw_query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                pairs.append((quote(k, safe=""), quote(v, safe="")))
            else:
                pairs.append((quote(pair, safe=""), ""))
        pairs.sort()
        canonical_query = "&".join(f"{k}={v}" for k, v in pairs)

    canonical_request = (
        f"{method.upper()}\n"
        f"{canonical_uri}\n"
        f"{canonical_query}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    scope = f"{date_stamp}/{region_id}/obs"
    string_to_sign = (
        f"OBS\n"
        f"{amz_date}\n"
        f"{scope}\n"
        f"{_sha256_hex(canonical_request)}"
    )

    k_date = _hmac_sha256(sk, date_stamp)
    k_region = _hmac_sha256(k_date, region_id)
    k_service = _hmac_sha256(k_region, "obs")
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"OBS Credential:{ak}/{scope},"
        f"SignedHeaders:{signed_headers},"
        f"Signature:{signature}"
    )

    headers["Authorization"] = authorization

    return url, headers
