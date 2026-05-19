"""ds_pipeline.py — 마음돌봄 Vision Phase 2 DeepStream 파이프라인 빌더.

4가지 입력 소스를 지원한다:

  source_mode = "test"  → videotestsrc           (모델/카메라 없이 구동 검증)
  source_mode = "v4l2"  → v4l2src device=...     (USB 웹캠 직결)
  source_mode = "file"  → uridecodebin uri=...   (mp4/h264 회귀 테스트)
  source_mode = "ros"   → appsrc + push_frame()  (sensor_msgs/Image 입력)

파이프라인 (공통):
   <source> → nvvideoconvert → nvstreammux
        → nvinfer(PGIE: YOLOv8n-face)
        → nvtracker (NvDCF)
        → nvinfer(SGIE: emotion)
        → fakesink (probe 로 메타데이터 추출)

DeepStream/pyds 미설치 환경에서도 import 가 깨지지 않도록 가드를 둔다.
이 경우 start() 가 RuntimeError 를 발생시킨다.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# GStreamer / DeepStream 임포트 (실패해도 import 자체는 성공시킨다)
# ----------------------------------------------------------------------
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib  # type: ignore
    DEEPSTREAM_AVAILABLE = True
except Exception as _exc:  # pragma: no cover
    DEEPSTREAM_AVAILABLE = False
    Gst = None  # type: ignore
    GLib = None  # type: ignore
    logger.warning(
        "[ds_pipeline] GStreamer 사용 불가 — DeepStream SDK 미설치? "
        f"({_exc})"
    )

# Gst.init() 은 module-level 이 아니라 start() 시점에 1 회만 호출한다.
# (rclpy / DDS 초기화 전에 GLib/Gst 가 초기화되면 메인스레드 시그널 마스크
#  가 망가져 rclpy.Node 생성 시 seg-fault 가 발생하는 사례가 있음.)
_GST_INITED = False


def _ensure_gst_init() -> None:
    global _GST_INITED
    if not _GST_INITED and Gst is not None:
        Gst.init(None)
        _GST_INITED = True


_SOURCE_MODES = ("test", "v4l2", "file", "ros")


class DeepStreamPipeline:
    """DeepStream 파이프라인 매니저.

    Args:
        source_mode      : "test" | "v4l2" | "file" | "ros"
        pgie_config      : PGIE 설정 파일 절대경로
        sgie_config      : SGIE 설정 파일 절대경로
        tracker_config   : nvtracker 설정 파일 (yml). None 이면 NvDCF 기본값
        on_buffer_probe  : SGIE src pad 에 등록할 probe callback
        width, height    : 스트림 mux 출력 해상도
        fps              : 입력 fps (test/ros 모드에서만 유효)
        v4l2_device      : "/dev/video0" 등
        file_uri         : "file:///path/to/video.mp4"
    """

    DEFAULT_TRACKER_LIB = (
        "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"
    )

    def __init__(
        self,
        source_mode: str,
        pgie_config: str | Path,
        sgie_config: str | Path,
        on_buffer_probe: Callable,
        tracker_config: Optional[str | Path] = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        v4l2_device: str = "/dev/video0",
        file_uri: str = "",
    ) -> None:
        if source_mode not in _SOURCE_MODES:
            raise ValueError(
                f"source_mode 는 {_SOURCE_MODES} 중 하나여야 합니다 (got {source_mode!r})"
            )

        self.source_mode    = source_mode
        self.pgie_config    = str(pgie_config)
        self.sgie_config    = str(sgie_config)
        self.tracker_config = str(tracker_config) if tracker_config else None
        self.width          = width
        self.height         = height
        self.fps            = fps
        self.v4l2_device    = v4l2_device
        self.file_uri       = file_uri
        self.on_buffer_probe = on_buffer_probe

        self._pipeline: Optional["Gst.Pipeline"] = None
        self._appsrc:   Optional["Gst.Element"]  = None
        self._loop:     Optional["GLib.MainLoop"] = None
        self._thread:   Optional[threading.Thread] = None
        self._frame_lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not DEEPSTREAM_AVAILABLE:
            raise RuntimeError(
                "GStreamer/DeepStream 가 import 되지 않았습니다. "
                "DS 8.0 + pyds 설치 후 다시 시도하세요."
            )
        _ensure_gst_init()

        pipeline_str = self._make_pipeline_string()
        logger.info("[ds_pipeline] gst-launch:\n  %s", pipeline_str)

        pipeline = Gst.parse_launch(pipeline_str)
        if pipeline is None:
            raise RuntimeError("Gst.parse_launch 실패")
        self._pipeline = pipeline

        # appsrc (ros 모드만) 핸들 확보
        if self.source_mode == "ros":
            self._appsrc = pipeline.get_by_name("ros_src")
            if self._appsrc is None:
                raise RuntimeError("appsrc 'ros_src' 를 찾지 못함")
            caps_str = (
                f"video/x-raw,format=NV12,width={self.width},"
                f"height={self.height},framerate={self.fps}/1"
            )
            self._appsrc.set_property("caps", Gst.Caps.from_string(caps_str))
            self._appsrc.set_property("format", Gst.Format.TIME)
            self._appsrc.set_property("block", True)
            self._appsrc.set_property("is-live", True)

        # SGIE src pad 에 probe 등록
        sgie = pipeline.get_by_name("sgie")
        if sgie is None:
            raise RuntimeError("sgie 엘리먼트를 찾지 못함")
        src_pad = sgie.get_static_pad("src")
        if src_pad is None:
            raise RuntimeError("sgie src pad 없음")
        src_pad.add_probe(Gst.PadProbeType.BUFFER, self.on_buffer_probe, 0)

        # 버스 연결
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::eos",   self._on_bus_eos)
        bus.connect("message::warning", self._on_bus_warning)

        # GLib MainLoop 별도 스레드
        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(
            target=self._loop.run, name="ds-glib-loop", daemon=True
        )
        self._thread.start()

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("파이프라인 PLAYING 전환 실패")

        self._running = True
        logger.info("[ds_pipeline] 시작 OK (mode=%s)", self.source_mode)

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
            logger.warning("[ds_pipeline] stop() 중 예외: %s", exc)
        logger.info("[ds_pipeline] 정지")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # ROS 모드 — 외부에서 프레임 주입
    # ------------------------------------------------------------------
    def push_frame(self, frame_bgr: np.ndarray) -> bool:
        if self.source_mode != "ros":
            return False
        if not self._running or self._appsrc is None:
            return False

        with self._frame_lock:
            try:
                nv12 = _bgr_to_nv12(frame_bgr, self.width, self.height)
            except Exception as exc:
                logger.warning("[ds_pipeline] BGR→NV12 실패: %s", exc)
                return False
            buf = Gst.Buffer.new_wrapped(nv12.tobytes())
            ret = self._appsrc.emit("push-buffer", buf)
        return ret == Gst.FlowReturn.OK

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------
    def _make_pipeline_string(self) -> str:
        # 1. 입력 소스 → NV12 NVMM 으로 정규화
        if self.source_mode == "test":
            src = (
                f"videotestsrc is-live=true pattern=ball ! "
                f"video/x-raw,width={self.width},height={self.height},"
                f"framerate={self.fps}/1 ! "
                f"nvvideoconvert ! "
                f"video/x-raw(memory:NVMM),format=NV12,"
                f"width={self.width},height={self.height} "
            )
        elif self.source_mode == "v4l2":
            src = (
                f"v4l2src device={self.v4l2_device} ! "
                f"video/x-raw,width={self.width},height={self.height},"
                f"framerate={self.fps}/1 ! "
                f"nvvideoconvert ! "
                f"video/x-raw(memory:NVMM),format=NV12,"
                f"width={self.width},height={self.height} "
            )
        elif self.source_mode == "file":
            if not self.file_uri:
                raise ValueError("source_mode=file 인데 file_uri 가 비어 있음")
            src = (
                f"uridecodebin uri={self.file_uri} ! "
                f"nvvideoconvert ! "
                f"video/x-raw(memory:NVMM),format=NV12,"
                f"width={self.width},height={self.height} "
            )
        elif self.source_mode == "ros":
            # appsrc 는 system memory NV12 → nvvideoconvert 가 NVMM 으로 업로드
            src = (
                f"appsrc name=ros_src is-live=true do-timestamp=true ! "
                f"nvvideoconvert ! "
                f"video/x-raw(memory:NVMM),format=NV12,"
                f"width={self.width},height={self.height} "
            )
        else:  # pragma: no cover
            raise AssertionError(self.source_mode)

        # 2. 트래커 부분
        tracker_part = (
            f"nvtracker "
            f"  tracker-width=640 tracker-height=384 "
            f"  ll-lib-file={self.DEFAULT_TRACKER_LIB} "
        )
        if self.tracker_config:
            tracker_part += f"  ll-config-file={self.tracker_config} "

        # 3. 풀 파이프라인
        return (
            f"{src} ! "
            f"mux.sink_0 "
            f"nvstreammux name=mux batch-size=1 "
            f"  width={self.width} height={self.height} "
            f"  batched-push-timeout=4000000 live-source=1 ! "
            f"nvinfer name=pgie config-file-path={self.pgie_config} ! "
            f"{tracker_part} ! "
            f"nvinfer name=sgie config-file-path={self.sgie_config} ! "
            f"fakesink name=sink sync=false async=false"
        )

    # ------------------------------------------------------------------
    # 버스 핸들러
    # ------------------------------------------------------------------
    def _on_bus_error(self, _bus, message) -> None:
        err, debug = message.parse_error()
        logger.error("[ds_pipeline][gst-error] %s", err.message)
        logger.debug("                            debug=%s", debug)
        self.stop()

    def _on_bus_warning(self, _bus, message) -> None:
        warn, _debug = message.parse_warning()
        logger.warning("[ds_pipeline][gst-warning] %s", warn.message)

    def _on_bus_eos(self, _bus, _message) -> None:
        logger.info("[ds_pipeline] EOS")
        self.stop()


# ----------------------------------------------------------------------
# BGR → NV12 변환 유틸
# ----------------------------------------------------------------------
def _bgr_to_nv12(frame_bgr: np.ndarray, width: int, height: int) -> np.ndarray:
    """OpenCV BGR(H,W,3) → NV12 (Y plane + interleaved UV plane).

    필요 시 width/height 로 리사이즈한다. height/width 는 짝수여야 한다.
    """
    import cv2  # 지연 import (DS 미설치 환경 대비)

    if frame_bgr.shape[1] != width or frame_bgr.shape[0] != height:
        frame_bgr = cv2.resize(frame_bgr, (width, height), interpolation=cv2.INTER_AREA)

    yuv_i420 = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)
    h, w = height, width

    y_plane = yuv_i420[:h].reshape(-1)
    u_plane = yuv_i420[h : h + h // 4].reshape(-1)
    v_plane = yuv_i420[h + h // 4 :].reshape(-1)

    uv = np.empty(u_plane.size + v_plane.size, dtype=np.uint8)
    uv[0::2] = u_plane
    uv[1::2] = v_plane

    return np.concatenate([y_plane, uv])
