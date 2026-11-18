"""Chrome crop preprocessing: env-driven (legacy) and active-service-driven (ADR-0002)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.patches.vnc_screenshot import (
    patch_computer_screenshot,
)

# Sleeps run on a virtual clock (tests/conftest.py::vnc_virtual_clock).
pytestmark = pytest.mark.usefixtures("vnc_virtual_clock")


class TestChromeCropPreprocessing:
    """Crop the top N pixels of the VNC screenshot before sending to UI-TARS.

    Ground-truth screenshots showed UI-TARS-1.5-7B systematically clicking
    in Safari's chrome instead of the webpage header, because its training
    distribution had no concept of "browser chrome offset." Cropping the
    chrome out of the image before inference removes the ambiguity: the
    model's "top-right header" heuristic now correctly targets webpage
    elements because the image it sees IS the webpage.

    This is vision-pipeline preprocessing, fully consistent with the
    RFC's vision-based mandate (see §Alternatives Considered #1 carve-out
    for "screenshot preprocessing").
    """

    @pytest.mark.asyncio
    async def test_crop_disabled_by_default_no_op(self, monkeypatch):
        """With SOREN_CHROME_CROP_Y unset, behavior is identical to pre-crop."""
        import io

        from PIL import Image

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)

        buf = io.BytesIO()
        Image.new("RGB", (1440, 900)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": 900})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png_bytes):
            result = await interface.screenshot()

        # When crop is disabled, returned bytes are the raw capture
        assert result == png_bytes

    @pytest.mark.asyncio
    async def test_crop_removes_top_n_pixels_when_enabled(self, monkeypatch):
        """With SOREN_CHROME_CROP_Y=100, the returned screenshot is 100px shorter."""
        import io

        from PIL import Image

        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")

        buf = io.BytesIO()
        Image.new("RGB", (1440, 900)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": 900})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png_bytes):
            result = await interface.screenshot()

        # Decode the returned bytes and check its dimensions
        cropped_img = Image.open(io.BytesIO(result))
        assert cropped_img.size == (1440, 800), (
            f"Cropped image should be 1440x800 (removed 100px from top); got {cropped_img.size}"
        )

    @pytest.mark.asyncio
    async def test_click_y_offset_added_when_crop_active(self, monkeypatch):
        """UI-TARS coords are relative to the cropped image; dispatch must add the offset back."""
        import io

        from PIL import Image

        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")

        buf = io.BytesIO()
        Image.new("RGB", (1440, 900)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        orig_left_click = AsyncMock()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = orig_left_click
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": 900})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png_bytes):
            await interface.screenshot()
            # UI-TARS saw a 1440x800 cropped image and says "click at (820, 15)"
            # That's 15px from the top of the WEBPAGE (not top of screen).
            # Expected: dispatch at (820, 15+100) = (820, 115) in screen space.
            await interface.left_click(820, 15)

        orig_left_click.assert_awaited_once_with(820, 115)

    @pytest.mark.asyncio
    async def test_screenshot_full_bypasses_crop(self, monkeypatch):
        """interface.screenshot_full() must return uncropped bytes even when crop is active."""
        import io

        from PIL import Image

        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "100")

        buf = io.BytesIO()
        Image.new("RGB", (1440, 900)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": 900})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        # screenshot_full must exist after patching
        assert hasattr(interface, "screenshot_full"), (
            "patch_computer_screenshot must expose interface.screenshot_full for verifier use"
        )

        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png_bytes):
            full = await interface.screenshot_full()

        full_img = Image.open(io.BytesIO(full))
        assert full_img.size == (1440, 900), (
            f"screenshot_full must return full image even when crop is enabled; got {full_img.size}"
        )

    @pytest.mark.asyncio
    async def test_invalid_crop_env_ignored(self, monkeypatch):
        """Non-numeric or negative SOREN_CHROME_CROP_Y must be treated as 0 (no crop)."""
        import io

        from PIL import Image

        monkeypatch.setenv("SOREN_CHROME_CROP_Y", "not-a-number")

        buf = io.BytesIO()
        Image.new("RGB", (1440, 900)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": 900})
        computer.interface = interface

        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})

        patch_computer_screenshot(computer, lume_client, "test-vm")

        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png_bytes):
            result = await interface.screenshot()

        img = Image.open(io.BytesIO(result))
        assert img.size == (1440, 900)


class TestChromeCropFromActiveService:
    """Phase 2 (ADR-0002): the crop height comes from the active service; no env var needed."""

    @staticmethod
    def _png(w: int = 1440, h: int = 900) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (w, h)).save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _patched_interface(screen_h: int = 900):
        computer = MagicMock()
        interface = MagicMock()
        interface.left_click = AsyncMock()
        interface.get_screen_size = AsyncMock(return_value={"width": 1440, "height": screen_h})
        computer.interface = interface
        lume_client = AsyncMock()
        lume_client.get_vm = AsyncMock(return_value={"vncUrl": "vnc://:pw@localhost:5900"})
        orig_click = interface.left_click
        patch_computer_screenshot(computer, lume_client, "test-vm")
        return interface, orig_click

    @pytest.mark.asyncio
    async def test_screenshot_cropped_by_active_service(self, monkeypatch, tmp_path):
        import io

        from PIL import Image

        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, _ = self._patched_interface()
        svc = make_resolved_service(tmp_path, config={"chrome_height_px": 100})
        with (
            use_service(svc),
            patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=self._png()),
        ):
            cropped = await interface.screenshot()
            full = await interface.screenshot_full()
        assert Image.open(io.BytesIO(cropped)).size == (1440, 800)
        assert Image.open(io.BytesIO(full)).size == (1440, 900)

    @pytest.mark.asyncio
    async def test_click_offset_uses_active_service(self, monkeypatch, tmp_path):
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, orig_click = self._patched_interface()
        svc = make_resolved_service(tmp_path, config={"chrome_height_px": 90})
        with (
            use_service(svc),
            patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=self._png()),
        ):
            await interface.screenshot()
            await interface.left_click(820, 15)
        orig_click.assert_awaited_once_with(820, 105)

    @pytest.mark.asyncio
    async def test_service_with_zero_height_disables_crop(self, monkeypatch, tmp_path):
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, orig_click = self._patched_interface()
        png = self._png()
        svc = make_resolved_service(tmp_path, config={"chrome_height_px": 0})
        with use_service(svc), patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png):
            assert await interface.screenshot() == png
            await interface.left_click(10, 10)
        orig_click.assert_awaited_once_with(10, 10)

    @pytest.mark.asyncio
    async def test_crop_stops_after_service_context_exits(self, monkeypatch, tmp_path):
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, _ = self._patched_interface()
        png = self._png()
        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=png):
            with use_service(make_resolved_service(tmp_path, config={"chrome_height_px": 100})):
                await interface.screenshot()
            assert await interface.screenshot() == png

    @pytest.mark.asyncio
    async def test_crop_taller_than_screenshot_shifts_no_clicks(self, monkeypatch, tmp_path):
        """A crop the patch couldn't apply (image too short) must not move clicks either."""
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, orig_click = self._patched_interface(screen_h=300)
        short = self._png(1440, 300)
        svc = make_resolved_service(tmp_path, config={"chrome_height_px": 400})
        with use_service(svc), patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=short):
            assert await interface.screenshot() == short  # uncropped: UI-TARS sees the full image
            await interface.left_click(10, 10)
        orig_click.assert_awaited_once_with(10, 10)

    @pytest.mark.asyncio
    async def test_click_offset_matches_the_screenshot_the_model_saw(self, monkeypatch, tmp_path):
        """Within one task, the click uses the crop applied to the screenshot the model just saw."""
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, orig_click = self._patched_interface()
        with (
            patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=self._png()),
            use_service(make_resolved_service(tmp_path, "a", config={"chrome_height_px": 100})),
        ):
            await interface.screenshot()
            await interface.left_click(820, 15)
        orig_click.assert_awaited_once_with(820, 115)

    @pytest.mark.asyncio
    async def test_previous_tasks_crop_does_not_leak_into_the_next_task(self, monkeypatch, tmp_path):
        """A click before the next task's first screenshot uses that task's configured crop, not the last one applied."""
        from soren.services.service_context import use_service
        from tests.service_helpers import make_resolved_service

        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        interface, orig_click = self._patched_interface()
        with patch("soren.patches.vnc_screenshot._capture_vnc_screenshot", return_value=self._png()):
            with use_service(make_resolved_service(tmp_path, "a", config={"chrome_height_px": 100})):
                await interface.screenshot()
            with use_service(make_resolved_service(tmp_path, "b", config={"chrome_height_px": 0})):
                await interface.left_click(820, 15)
        orig_click.assert_awaited_once_with(820, 15)
