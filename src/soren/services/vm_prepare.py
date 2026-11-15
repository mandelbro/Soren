"""Pristine VM preparation for Soren."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path  # for type hint in config_path

from soren.services.config_loader import load_settings
from soren.services.lume_client import LumeError
from soren.services.vm_manager import VMManager

logger = logging.getLogger(__name__)

# Default base image for pristine VM
DEFAULT_BASE_IMAGE = "macos-sequoia-cua:latest"

# TCC service constant
KTCC_SERVICE_APPLE_EVENTS = "kTCCServiceAppleEvents"


def _tcc_insert_sql(client_path: str, target_bundle: str) -> str:
    """Build TCC INSERT statement for Apple Events permission."""
    return (
        "INSERT OR REPLACE INTO access "
        "(service, client, client_type, auth_value, auth_reason, "
        "auth_version, indirect_object_identifier, "
        "indirect_object_identifier_type, flags) "
        f"VALUES ('{KTCC_SERVICE_APPLE_EVENTS}', "
        f"'{client_path}', 1, 2, 4, 1, "
        f"'{target_bundle}', 0, 0)"
    )


async def prepare_pristine_vm(
    settings=None,
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    config_path: Path | None = None,
) -> None:
    """Create and configure the pristine VM image.

    Steps:
    1. Pull or create base VM from image
    2. Boot VM, wait for ready
    3. Grant TCC permissions (python3 → Safari, python3 → System Events)
    4. Configure Safari defaults if needed
    5. Stop VM (saved as pristine for cloning)

    If TCC.db manipulation fails (SIP enabled), logs warning with recovery instructions.
    """
    settings = settings or load_settings(config_path)
    manager = VMManager(settings.vm)
    pristine_name = settings.vm.pristine_name

    # Step 1: Pull base image with pristine name
    logger.info("Pulling base image %s as %s", base_image, pristine_name)
    try:
        await manager._client.pull_image(base_image, name=pristine_name)
    except LumeError as e:
        raise RuntimeError(f"Cannot pull image {base_image}: {e}") from e

    # Step 2: Boot VM, wait for ready
    logger.info("Booting pristine VM %s", pristine_name)
    await manager._client.start_vm(pristine_name)
    # Use the pristine name temporarily for this VM
    original_name = manager._vm_name
    manager._vm_name = pristine_name
    try:
        await manager._wait_for_ready(timeout_s=180)
        await manager.connect_computer()
    finally:
        manager._vm_name = original_name

    # Step 3: Grant TCC permissions via sqlite3 (inside VM)
    try:
        iface = manager._computer.interface
        # Get python3 path in VM
        which_result = await iface.run_command("which python3")
        python_path = (which_result.stdout or "").strip() or "/usr/bin/python3"
        # Use $HOME so path expands in VM's shell
        tcc_db_path = "$HOME/Library/Application Support/com.apple.TCC/TCC.db"

        for target in ["com.apple.Safari", "com.apple.systemevents"]:
            sql = _tcc_insert_sql(python_path, target)
            # Use echo + pipe to avoid shell escaping issues with SQL
            escaped_sql = sql.replace('"', '\\"')
            cmd = f'echo "{escaped_sql}" | sqlite3 "{tcc_db_path}"'
            result = await iface.run_command(cmd)
            if result.returncode != 0 and result.stderr:
                logger.warning(
                    "TCC permission for %s may have failed: %s. "
                    "If SIP is enabled, boot VM in recovery mode (lume run --recovery-mode) "
                    "and run 'csrutil disable', then retry prepare.",
                    target,
                    result.stderr,
                )
            else:
                logger.info("TCC permission granted: python3 → %s", target)
    except Exception as e:
        logger.warning(
            "TCC permission setup failed: %s. "
            "SIP may block TCC.db writes. Boot VM in recovery mode to disable SIP, "
            "or manually grant Automation permissions in System Settings.",
            e,
        )

    # Step 4: Configure Safari defaults (minimal: ensure Safari is available)
    try:
        await manager._computer.interface.run_command("open -a Safari")
        await asyncio.sleep(3)
        # Close Safari so we can stop the VM cleanly
        await manager._computer.interface.run_command("osascript -e 'tell application \"Safari\" to quit'")
        await asyncio.sleep(2)
    except Exception as e:
        logger.warning("Safari configuration skipped: %s", e)

    # Step 5: Stop VM (saved as pristine)
    logger.info("Stopping pristine VM %s", pristine_name)
    manager._computer = None
    await manager._client.stop_vm(pristine_name)
