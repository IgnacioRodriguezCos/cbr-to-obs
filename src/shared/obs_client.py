"""OBS (Object Storage Service) client for Huawei Cloud.

Handles job state persistence and cross-region object copy.
Uses V4 signing for authentication.
"""

import json

import requests

from .huawei_auth import sign_obs_request
from .regions import get_endpoint, get_region_config


class OBSClient:
    """Client for Huawei Cloud OBS operations.

    Used for storing migration job state and copying objects cross-region.
    """

    def __init__(self, access_key, secret_key):
        self.ak = access_key
        self.sk = secret_key

    def _do_request(self, region_input, bucket_name, method, object_key="", body=b"", extra_headers=None):
        """Execute a signed OBS request.

        Args:
            region_input: Region alias or ID.
            bucket_name: Target bucket name.
            method: HTTP method.
            object_key: Object key within bucket.
            body: Request body bytes.
            extra_headers: Additional headers.

        Returns:
            requests.Response object.
        """
        region_config = get_region_config(region_input)
        region_id = region_config["id"]
        endpoint = get_endpoint(region_input, "obs")

        headers = {}
        if extra_headers:
            headers.update(extra_headers)

        url, signed_headers = sign_obs_request(
            method=method,
            endpoint=endpoint,
            bucket_name=bucket_name,
            object_key=object_key,
            headers=headers,
            ak=self.ak,
            sk=self.sk,
            region_id=region_id,
            payload=body,
        )

        resp = requests.request(
            method=method,
            url=url,
            headers=signed_headers,
            data=body if body else None,
            timeout=60,
        )
        return resp

    def put_object(self, region_input, bucket_name, object_key, data, content_type="application/json"):
        """Upload an object to OBS.

        Args:
            region_input: Region alias or ID.
            bucket_name: Target bucket name.
            object_key: Object key.
            data: Data to upload (str or bytes).
            content_type: Content-Type header value.

        Returns:
            True if successful, False otherwise.
        """
        if isinstance(data, str):
            data = data.encode("utf-8")

        headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
        resp = self._do_request(
            region_input, bucket_name, "PUT", object_key, body=data, extra_headers=headers
        )
        return resp.status_code in (200, 201, 204)

    def get_object(self, region_input, bucket_name, object_key):
        """Download an object from OBS.

        Args:
            region_input: Region alias or ID.
            bucket_name: Source bucket name.
            object_key: Object key.

        Returns:
            Object content as bytes, or None if not found.
        """
        resp = self._do_request(region_input, bucket_name, "GET", object_key)
        if resp.status_code == 200:
            return resp.content
        return None

    def delete_object(self, region_input, bucket_name, object_key):
        """Delete an object from OBS.

        Args:
            region_input: Region alias or ID.
            bucket_name: Bucket name.
            object_key: Object key to delete.

        Returns:
            True if successful (or already absent), False otherwise.
        """
        resp = self._do_request(region_input, bucket_name, "DELETE", object_key)
        return resp.status_code in (200, 204)

    def object_exists(self, region_input, bucket_name, object_key):
        """Check if an object exists in a bucket (HEAD request).

        Args:
            region_input: Region alias or ID.
            bucket_name: Bucket name.
            object_key: Object key.

        Returns:
            True if the object exists, False otherwise.
        """
        resp = self._do_request(region_input, bucket_name, "HEAD", object_key)
        return resp.status_code == 200

    def list_objects(self, region_input, bucket_name, prefix=""):
        """List objects in a bucket with optional prefix.

        Args:
            region_input: Region alias or ID.
            bucket_name: Bucket name.
            prefix: Key prefix filter.

        Returns:
            List of object key strings.
        """
        region_config = get_region_config(region_input)
        region_id = region_config["id"]
        endpoint = get_endpoint(region_input, "obs")
        host = endpoint.replace("https://", "")
        url_host = f"{bucket_name}.{host}"

        query = "list-type=2"
        if prefix:
            query += f"&prefix={prefix}"

        url = f"https://{url_host}/?{query}"

        headers = {"host": url_host}
        from .huawei_auth import sign_obs_request

        url, signed_headers = sign_obs_request(
            method="GET",
            endpoint=endpoint,
            bucket_name=bucket_name,
            object_key="",
            headers=headers,
            ak=self.ak,
            sk=self.sk,
            region_id=region_id,
            payload=b"",
        )
        url = f"https://{url_host}/?{query}"
        resp = requests.get(url, headers=signed_headers, timeout=60)

        if resp.status_code != 200:
            return []

        import xml.etree.ElementTree as ET

        root = ET.fromstring(resp.content)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = []
        for obj in root.findall(".//s3:Contents/s3:Key", ns):
            if obj.text:
                keys.append(obj.text)
        if not keys:
            for obj in root.findall(".//Contents/Key"):
                if obj.text:
                    keys.append(obj.text)
        return keys

    def save_job_state(self, region_input, state_bucket, job_id, state):
        """Save migration job state to OBS as JSON.

        Args:
            region_input: Region for the state bucket.
            state_bucket: Bucket name for state storage.
            job_id: Unique job identifier.
            state: Job state dict.

        Returns:
            True if successful.
        """
        object_key = f"state/{job_id}.json"
        return self.put_object(
            region_input, state_bucket, object_key, json.dumps(state, indent=2)
        )

    def load_job_state(self, region_input, state_bucket, job_id):
        """Load migration job state from OBS.

        Args:
            region_input: Region for the state bucket.
            state_bucket: Bucket name for state storage.
            job_id: Unique job identifier.

        Returns:
            Job state dict, or None if not found.
        """
        object_key = f"state/{job_id}.json"
        data = self.get_object(region_input, state_bucket, object_key)
        if data:
            return json.loads(data.decode("utf-8"))
        return None

    def list_pending_jobs(self, region_input, state_bucket):
        """List all pending job states from OBS.

        Args:
            region_input: Region for the state bucket.
            state_bucket: Bucket name for state storage.

        Returns:
            List of job state dicts.
        """
        keys = self.list_objects(region_input, state_bucket, prefix="state/")
        jobs = []
        for key in keys:
            data = self.get_object(region_input, state_bucket, key)
            if data:
                try:
                    jobs.append(json.loads(data.decode("utf-8")))
                except json.JSONDecodeError:
                    pass
        return jobs

    def delete_job_state(self, region_input, state_bucket, job_id):
        """Delete a job state from OBS.

        Args:
            region_input: Region for the state bucket.
            state_bucket: Bucket name for state storage.
            job_id: Unique job identifier.

        Returns:
            True if successful.
        """
        object_key = f"state/{job_id}.json"
        return self.delete_object(region_input, state_bucket, object_key)

    def copy_object_cross_region(
        self,
        source_region,
        source_bucket,
        source_key,
        target_region,
        target_bucket,
        target_key,
    ):
        """Copy an object from one region's bucket to another region's bucket.

        Uses OBS server-side copy (works only within same region).
        For cross-region, downloads and re-uploads the object.

        Args:
            source_region: Source region alias or ID.
            source_bucket: Source bucket name.
            source_key: Source object key.
            target_region: Target region alias or ID.
            target_bucket: Target bucket name.
            target_key: Target object key.

        Returns:
            True if successful, False otherwise.
        """
        from .regions import is_cross_region

        if not is_cross_region(source_region, target_region):
            region_config = get_region_config(source_region)
            region_id = region_config["id"]
            endpoint = get_endpoint(source_region, "obs")
            host = endpoint.replace("https://", "")
            url_host = f"{target_bucket}.{host}"
            url = f"https://{url_host}/{target_key}"

            headers = {
                "host": url_host,
                "x-amz-copy-source": f"/{source_bucket}/{source_key}",
            }
            url, signed_headers = sign_obs_request(
                method="PUT",
                endpoint=endpoint,
                bucket_name=target_bucket,
                object_key=target_key,
                headers=headers,
                ak=self.ak,
                sk=self.sk,
                region_id=region_id,
                payload=b"",
            )
            resp = requests.put(url, headers=signed_headers, timeout=120)
            return resp.status_code in (200, 201)

        data = self.get_object(source_region, source_bucket, source_key)
        if data is None:
            return False
        return self.put_object(
            target_region, target_bucket, target_key, data, content_type="application/octet-stream"
        )
