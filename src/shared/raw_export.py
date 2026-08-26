"""Raw export path helpers for volumes larger than the IMS 1TB limit.

Flow: restored EVS volume is attached to a temporary ECS. Cloud-init
user data on that ECS installs obsutil and streams the raw block device
to OBS (dd | obsutil), then writes a .SUCCESS marker object.
The status_checker polls for the marker to advance the job.

Note: AK/SK are injected in user data, readable only from inside this
temporary instance via metadata service. Use a restricted IAM sub-user
and the instance is deleted after completion.
"""

import secrets
import string

from .regions import get_region_config


def marker_key(object_key):
    """Return the success marker key for a raw export object."""
    return f"{object_key}.SUCCESS"


def generate_password(length=16):
    """Generate a random password for the temp ECS."""
    alphabet = string.ascii_letters + string.digits + "!@#$%?"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_user_data(job, config):
    """Build cloud-init user data script that streams the volume to OBS.

    Args:
        job: Job state dict (needs bucket_name, object_key, source/target region,
             temp_device).
        config: Config instance.

    Returns:
        Bash script string.
    """
    region = job["target_region"] if job.get("cross_region") else job["source_region"]
    region_id = get_region_config(region)["id"]
    device = job.get("temp_device", "/dev/vdb")
    bucket = job["bucket_name"]
    key = job["object_key"]
    marker = marker_key(key)
    part_mb = max(100, min(config.raw_part_mb, 1024))
    concurrency = max(1, min(config.raw_concurrency, 16))

    return f"""#!/bin/bash
exec >> /var/log/cbr_raw_export.log 2>&1
set -x

DEVICE={device}
while [ ! -b "$DEVICE" ]; do sleep 5; done

curl -sSL -o /tmp/obsutil.tgz "{config.obsutil_url}"
mkdir -p /tmp/obsbin
tar -zxf /tmp/obsutil.tgz -C /tmp/obsbin
OBSUTIL=$(find /tmp/obsbin -type f -name obsutil | head -1)
chmod +x "$OBSUTIL"

"$OBSUTIL" config -i {config.access_key} -k {config.secret_key} -e https://obs.{region_id}.myhuaweicloud.com

dd if=$DEVICE bs=64M | "$OBSUTIL" cp - obs://{bucket}/{key} -ps={part_mb}MB -p={concurrency} -f
RC=$?

if [ $RC -eq 0 ]; then
  echo ok | "$OBSUTIL" cp - obs://{bucket}/{marker} -f
fi
"""
