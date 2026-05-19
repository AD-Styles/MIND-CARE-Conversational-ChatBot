"""ds_pose_pipeline.py — DS 8.0 yolov8n-pose 단일 PGIE 파이프라인.

Phase 2 의 face/emotion 파이프라인과 별도. SGIE 없음.

  <source> → nvvideoconvert → nvstreammux
       → nvinfer (PGIE: yolov8n-pose, output-tensor-meta=1)
       → nvtracker (NvDCF)
       → fakesink (probe)
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib  # type: ignore
    DEEPSTREAM_AVAILABLE = True
except Exception as _exc:  # pragma: no cover
    DEEPSTREAM_AVAILABLE = False
    Gst = None  # type: ignore
    GLib = None  # type: ignore
    logger.warning("[ds_pose_pipeline] GStreamer 사용 불가 — DS 미설치? (%s)", _exc)

_GST_INITED = False


def _ensure_gst_init() -> None:
    global _GST_INITED
    if not _GST_INITED and Gst is not None:
        Gst.init(None)
        _GST_INITED = True


_SOURCE_MODES = ("test", "v4l2", "file", "ros")


class PosePipeline:
    DEFAULT_TRACKER_LIB = (
        "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"
    )

    def __init__(
        self,
        source_mode: str,
        pgie_config: str | Path,
        on_buffer_probe: Callable,
        tracker_config: Optional[str | Path] = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        v4l2_device: str = "/dev/video0",
        file_uri: str = "",
    ) -> None:
        if source_mode not in _SOURCE_MODES:
            raise ValueError(f"source_mode 는 {_SOURCE_MODES} 중 하나 (got {source_mode!r})")
        self.source_mode = source_mode
        self.pgie_config = str(pgie_config)
        self.tracker_config = str(tracker_config) if tracker_config else None
        self.width = width
        self.height = height
        self.fps = fps
        self.v4l2_device = v4l2_device
        self.file_uri = file_uri
        self.on_buffer_probe = on_buffer_probe

        self._pipeline: Optional["Gst.Pipeline"] = None
        self._appsrc: Optional["Gst.Element"] = None
        self._loop: Optional["GLib.MainLoop"] = None
        self._thread: Optional[threading.Thread] = None
        self._frame_lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        if not DEEPSTREAM_AVAILABLE:
            raise RuntimeError("GStreamer/DeepStream 가 import 되지 않음.")
        _ensure_gst_init()

        pipeline_str = self._make_pipeline_string()
        logger.info("[ds_pose_pipeline] gst-launch:\n  %s", pipeline_str)
        pipeline = Gst.parse_launch(pipeline_str)
        if pipeline is None:
            raise RuntimeError("Gst.parse_launch 실패")
        self._pipeline = pipeline

        if self.source_mode == "ros":
            self._appsrc = pipeline.get_by_name("ros_src")
            if self._appsrc is None:
                raise RuntimeError("appsrc 'ros_src' 없음")
            caps_str = (f"video/x-raw,format=NV12,width={self.width},"
                        f"height={self.height},framerate={self.fps}/1")
            self._appsrc.set_property("caps", Gst.Caps.from_string(caps_str))
            self._appsrc.set_property("format", Gst.Format.TIME)
            self._appsrc.set_property("block", True)
            self._appsrc.set_property("is-live", True)

        # PGIE 의 src pad 에 probe — output-tensor-meta 가 여기서 보임
        pgie = pipeline.get_by_name("pgie")
        if pgie is None:
            raise RuntimeError("pgie 엘리먼트를 찾지 못함")
        src_pad = pgie.get_static_pad("src")
        if src_pad is None:
            raise RuntimeError("pgie src pad 없음")
        src_pad.add_probe(Gst.PadProbeType.BUFFER, self.on_buffer_probe, 0)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::eos",   self._on_bus_eos)
        bus.connect("message::warning", self._on_bus_warning)

        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run,
                                        name="ds-pose-glib-loop", daemon=True)
        self._thread.start()

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("파이프라인 PLAYING 전환 실패")
        self._running = True
        logger.info("[ds_pose_pipeline] 시작 OK (mode=%s)", self.source_mode)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
            if self._loop is not None and self._loop.is_running():
                self._loop.quit()
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=3.0)
        except Exception as exc:  # pragma: no cover
            logger.warning("[ds_pose_pipeline] stop() 중 예외: %s", exc)
        logger.info("[ds_pose_pipeline] 정지")

    @property
    def is_running(self) -> bool:
        return self._running

    def push_frame(self, frame_bgr: np.ndarray) -> bool:
        if self.source_mode != "ros" or not self._running or self._appsrc is None:
            return False
        with self._frame_lock:
            try:
                nv12 = _bgr_to_nv12(frame_bgr, self.width, self.height)
            except Exception as exc:
                logger.warning("[ds_pose_pipeline] BGR→NV12 실패: %s", exc)
                return False
            buf = Gst.Buffer.new_wrapped(nv12.tobytes())
            ret = self._appsrc.emit("push-buffer", buf)
        return ret == Gst.FlowReturn.OK

    def _make_pipeline_string(self) -> str:
        if self.source_mode == "test":
            src = (f"videotestsrc is-live=true pattern=ball ! "
                   f"video/x-raw,width={self.width},height={self.height},"
                   f"framerate={self.fps}/1 ! "
                   f"nvvideoconvert ! "
                   f"video/x-raw(memory:NVMM),format=NV12,"
                   f"width={self.width},height={self.height} ")
        elif self.source_mode == "v4l2":
            src = (f"v4l2src device={self.v4l2_device} ! "
                   f"video/x-raw,width={self.width},height={self.height},"
                   f"framerate={self.fps}/1 ! "
                   f"nvvideoconvert ! "
                   f"video/x-raw(memory:NVMM),format=NV12,"
                   f"width={self.width},height={self.height} ")
        elif self.source_mode == "file":
            if not self.file_uri:
                raise ValueError("source_mode=file 인데 file_uri 가 비어 있음")
            src = (f"uridecodebin uri={self.file_uri} ! "
                   f"nvvideoconvert ! "
                   f"video/x-raw(memory:NVMM),format=NV12,"
                   f"width={self.width},height={self.height} ")
        elif self.source_mode == "ros":
            src = (f"appsrc name=ros_src is-live=true do-timestamp=true ! "
                   f"nvvideoconvert ! "
                   f"video/x-raw(memory:NVMM),format=NV12,"
                   f"width={self.width},height={self.height} ")
        else:  # pragma: no cover
            raise AssertionError(self.source_mode)

        tracker_part = (f"nvtracker tracker-width=640 tracker-height=384 "
                        f"  ll-lib-file={self.DEFAULT_TRACKER_LIB} ")
        if self.tracker_config:
            tracker_part += f"  ll-config-file={self.tracker_config} "

        return (f"{src} ! mux.sink_0 "
                f"nvstreammux name=mux batch-size=1 "
                f"  width={self.width} height={self.height} "
                f"  batched-push-timeout=4000000 live-source=1 ! "
                f"nvinfer name=pgie config-file-path={self.pgie_config} ! "
                f"{tracker_part} ! "
                f"fakesink name=sink sync=false async=false")

    def _on_bus_error(self, _bus, message) -> None:
        err, debug = message.parse_error()
        logger.error("[ds_pose_pipeline][gst-error] %s", err.message)
        logger.debug("                                debug=%s", debug)
        self.stop()

    def _on_bus_warning(self, _bus, message) -> None:
        warn, _debug = message.parse_warning()
        logger.warning("[ds_pose_pipeline][gst-warning] %s", warn.message)

    def _on_bus_eos(self, _bus, _message) -> None:
        logger.info("[ds_pose_pipeline] EOS")
        self.stop()


def _bgr_to_nv12(frame_bgr: np.ndarray, width: int, height: int) -> np.ndarray:
    import cv2
    if frame_bgr.shape[1] != width or frame_bgr.shape[0] != height:
        frame_bgr = cv2.resize(frame_bgr, (width, height), interpolation=cv2.INTER_AREA)
    yuv_i420 = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)
    h, w = height, width
    y_plane = yuv_i420[:h].reshape(-1)
    u_plane = yuv_i420[h:h + h // 4].reshape(-1)
    v_plane = yuv_i420[h + h // 4:].reshape(-1)
    uv = np.empty(u_plane.size + v_plane.size, dtype=np.uint8)
    uv[0::2] = u_plane
    uv[1::2] = v_plane
    return np.concatenate([y_plane, uv])
