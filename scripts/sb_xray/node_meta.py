"""Derive node-display metadata (show-config.sh §30-75 port).

Reads ``GEOIP_INFO``, ``DOMAIN``, ``ISP_TAG``, ``IS_8K_SMOOTH``, ``IP_TYPE``
from ``os.environ`` and writes the derived presentation fields back into
``os.environ`` so ``subscription._remark`` and ``display`` can render them:

* ``NODE_NAME``    — first label of ``DOMAIN`` (e.g. ``jp.example.com`` → ``jp``)
* ``NODE_IP``      — portion of ``GEOIP_INFO`` after the first ``|``
* ``REGION_INFO``  — portion before the ``|`` (e.g. ``Tokyo Japan``)
* ``FLAG_INFO``    — emoji flag matching ``REGION_INFO``
* ``FLAG_PREFIX``  — ``"<flag> "`` when a flag is matched, else ``""``
* ``NODE_SUFFIX``  — accumulative tag string (e.g. ``" ✈ 高速 ✈ super ✈ isp"``)
"""

from __future__ import annotations

import os

from sb_xray import display

_FAST_DOMAIN_PREFIXES = ("dmit", "dc", "jp")


def derive_and_export() -> None:
    """Populate display-time metadata in ``os.environ``.

    Idempotent: re-invoking with the same inputs yields the same output.
    Respects an externally-provided ``NODE_SUFFIX`` (concatenates onto it)
    so callers can pre-seed custom tags before calling.
    """
    domain = os.environ.get("DOMAIN", "")
    geoip = os.environ.get("GEOIP_INFO", "")

    node_name = domain.split(".", 1)[0] if domain else ""
    region_info, _, node_ip = geoip.partition("|")

    flag = display.get_flag_emoji(region_info)
    flag_prefix = f"{flag} " if flag else ""

    os.environ["NODE_NAME"] = node_name
    os.environ["NODE_IP"] = node_ip
    os.environ["REGION_INFO"] = region_info
    os.environ["FLAG_INFO"] = flag
    os.environ["FLAG_PREFIX"] = flag_prefix

    suffix = os.environ.get("NODE_SUFFIX", "")

    if any(domain.startswith(prefix) for prefix in _FAST_DOMAIN_PREFIXES):
        suffix = f"{suffix} ✈ 高速"

    isp_tag = os.environ.get("ISP_TAG", "")
    is_8k = os.environ.get("IS_8K_SMOOTH", "")
    ip_type = os.environ.get("IP_TYPE", "")

    if isp_tag and isp_tag != "direct" and is_8k == "true":
        suffix += " ✈ good"
    elif ip_type == "isp" and is_8k == "true":
        suffix += " ✈ super"

    if ip_type:
        suffix += f" ✈ {ip_type}"

    os.environ["NODE_SUFFIX"] = suffix
