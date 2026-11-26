"""Builds an authenticated simple-salesforce client from Settings."""

from __future__ import annotations

import json
import shutil
import subprocess

from simple_salesforce import Salesforce

from .config import Settings


class AuthError(RuntimeError):
    pass


def _token_from_sf_cli(alias: str) -> tuple[str, str]:
    """Reuse a session from the Salesforce CLI (`sf org login web --alias <alias>`)."""
    sf_bin = shutil.which("sf")
    if not sf_bin:
        raise AuthError("SF_TARGET_ORG is set but the Salesforce CLI ('sf') was not found in PATH.")
    proc = subprocess.run(
        [sf_bin, "org", "display", "--target-org", alias, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        result = json.loads(proc.stdout)["result"]
        return result["instanceUrl"], result["accessToken"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise AuthError(f"Could not read org '{alias}' from the Salesforce CLI: {proc.stderr or proc.stdout}") from exc


def connect(settings: Settings) -> Salesforce:
    if settings.target_org:
        instance_url, token = _token_from_sf_cli(settings.target_org)
        return Salesforce(instance_url=instance_url, session_id=token)
    if settings.instance_url and settings.access_token:
        return Salesforce(instance_url=settings.instance_url, session_id=settings.access_token)
    if settings.username and settings.password:
        return Salesforce(
            username=settings.username,
            password=settings.password,
            security_token=settings.security_token or "",
            domain=settings.domain,
        )
    raise AuthError(
        "No Salesforce credentials found. Set SF_TARGET_ORG, or SF_INSTANCE_URL + SF_ACCESS_TOKEN, "
        "or SF_USERNAME + SF_PASSWORD + SF_SECURITY_TOKEN (see .env.example)."
    )
