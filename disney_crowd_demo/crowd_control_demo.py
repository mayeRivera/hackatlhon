"""
Physical AI Crowd Flow Demo - FAST STABLE VERSION
=======================================================

Detector tuned for the existing pale pink/lilac circular markers.

Why this version is more stable:
1. Hough circle detection proposes circular candidates.
2. Each circle is scored by the actual marker color seen by the phone:
   pale pink/lilac, roughly HSV H~155, S~70.
3. Nearby duplicate circles are removed.
4. Only the best 10 physical markers are used.
5. Zone counts are debounced across several frames before being published.
   This stops A/B/C from changing every instant due to one noisy frame.

Camera-only test:
python crowd_control_demo_stable.py --camera-url http://10.212.1.188:8080/shot.jpg

Full hardware:
python crowd_control_demo_stable.py --esp32 192.168.1.6 --camera-url http://192.168.1.8:8080/shot.jpg
"""

from __future__ import annotations

import argparse
import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple, List

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify


# ============================================================
# DEMO CONFIG
# ============================================================

EXPECTED_TOTAL = 10

ZONE_WARNING = 4
ZONE_FULL = 5
SECURITY_MIN_EACH = 3
MIN_REDIRECT_DIFFERENCE = 2

FRAME_INTERVAL_SECONDS = 0.10
HTTP_TIMEOUT_SECONDS = 3.0

# Servo decisions only after stable visual state.
SERVO_STABLE_FRAMES = 3
MIN_GATE_COMMAND_INTERVAL = 1.0

ROTATE_DEGREES = 0


# ============================================================
# MARKER DETECTOR
#
# These values were tuned from the actual phone screenshot.
# The existing head markers are pale pink/lilac.
# ============================================================

MARKER_HUE_TARGET = 155.0
MARKER_SAT_TARGET = 70.0

# Hough candidate generation
HOUGH_PARAM1 = 80
HOUGH_PARAM2 = 14
MIN_RADIUS_RATIO = 0.012
MAX_RADIUS_RATIO = 0.045

# Reject candidates that do not look enough like the real marker.
MIN_MARKER_SCORE = 0.18

# Counts are published after the SAME distribution is seen twice in a row.
# This is much faster than a 7-frame majority, but still ignores one noisy frame.
COUNT_CONFIRM_FRAMES = 2

# Reduce image size before HoughCircles. This is the biggest speed improvement.
# 900 px is enough for the head markers and much faster than processing 1280/1920 px.
PROCESS_WIDTH = 900


# ============================================================
# ZONES
# Normalized image coordinates.
# ============================================================

ZONE_POLYGONS_NORM = {
    "A": [
        (0.12, 0.31),
        (0.40, 0.31),
        (0.40, 0.77),
        (0.12, 0.77),
    ],
    "B": [
        (0.40, 0.31),
        (0.64, 0.31),
        (0.64, 0.77),
        (0.40, 0.77),
    ],
    "C": [
        (0.64, 0.31),
        (0.92, 0.31),
        (0.92, 0.77),
        (0.64, 0.77),
    ],
}

ZONE_COLORS = {
    "A": (255, 80, 80),
    "B": (0, 220, 255),
    "C": (80, 220, 80),
}


# ============================================================
# GLOBAL STATE
# ============================================================

app = Flask(__name__)
lock = threading.Lock()
session = requests.Session()

ESP32_BASE_URL: str | None = None
CAMERA_URL = ""
CAMERA_MODE = "auto"

latest_jpeg: bytes | None = None

status = {
    "camera_ok": False,
    "camera_source": "",
    "hardware_connected": False,
    "detected_total": 0,
    "expected_total": EXPECTED_TOTAL,
    "counts": {"A": 0, "B": 0, "C": 0},
    "percentages": {"A": 0, "B": 0, "C": 0},
    "mode": "WAITING",
    "message": "Waiting for phone camera.",
    "gate1": "closed",
    "gate2": "closed",
    "auto": False,
    "fps": 0.0,
    "last_error": "",
}

last_gate_command_time = 0.0
last_sent_gates = ("closed", "closed")

candidate_servo_decision: Tuple[str, str, str, str] | None = None
candidate_servo_frames = 0

stable_counts = {"A": 0, "B": 0, "C": 0}
pending_counts_tuple = None
pending_counts_frames = 0


# ============================================================
# CAMERA
# ============================================================

class CameraSource:
    def __init__(self, url: str, mode: str = "auto"):
        self.url = url
        self.mode = mode
        self.cap = None

        if mode == "auto":
            lower = url.lower()

            if (
                lower.endswith(".jpg")
                or lower.endswith(".jpeg")
                or "shot.jpg" in lower
                or "capture" in lower
            ):
                self.active_mode = "snapshot"
            else:
                self.active_mode = "stream"
        else:
            self.active_mode = mode

        if self.active_mode == "stream":
            self._open_stream()

    def _open_stream(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass

        self.cap = cv2.VideoCapture(self.url)

        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def _snapshot(self) -> np.ndarray:
        response = session.get(
            self.url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"Cache-Control": "no-cache"},
        )
        response.raise_for_status()

        array = np.frombuffer(
            response.content,
            dtype=np.uint8,
        )

        frame = cv2.imdecode(
            array,
            cv2.IMREAD_COLOR,
        )

        if frame is None:
            raise RuntimeError(
                "Camera URL did not return a valid JPEG."
            )

        return frame

    def _stream(self) -> np.ndarray:
        if self.cap is None or not self.cap.isOpened():
            self._open_stream()

        ok, frame = self.cap.read()

        if not ok or frame is None:
            self._open_stream()
            time.sleep(0.15)
            ok, frame = self.cap.read()

        if not ok or frame is None:
            raise RuntimeError(
                "Could not read phone camera stream."
            )

        return frame

    def read(self) -> np.ndarray:
        if self.active_mode == "snapshot":
            frame = self._snapshot()
        else:
            frame = self._stream()

        if ROTATE_DEGREES == 90:
            frame = cv2.rotate(
                frame,
                cv2.ROTATE_90_CLOCKWISE,
            )
        elif ROTATE_DEGREES == 180:
            frame = cv2.rotate(
                frame,
                cv2.ROTATE_180,
            )
        elif ROTATE_DEGREES == 270:
            frame = cv2.rotate(
                frame,
                cv2.ROTATE_90_COUNTERCLOCKWISE,
            )

        return frame


camera_source: CameraSource | None = None


# ============================================================
# ZONES
# ============================================================

def polygon_pixels(
    points,
    width: int,
    height: int,
) -> np.ndarray:

    return np.array(
        [
            (int(x * width), int(y * height))
            for x, y in points
        ],
        dtype=np.int32,
    )


def get_zone_polygons(
    frame_shape,
) -> Dict[str, np.ndarray]:

    height, width = frame_shape[:2]

    return {
        zone: polygon_pixels(
            points,
            width,
            height,
        )
        for zone, points
        in ZONE_POLYGONS_NORM.items()
    }


def point_zone(
    point: Tuple[int, int],
    polygons: Dict[str, np.ndarray],
) -> str | None:

    x, y = point

    for zone in ("A", "B", "C"):
        if cv2.pointPolygonTest(
            polygons[zone],
            (float(x), float(y)),
            False,
        ) >= 0:
            return zone

    return None


# ============================================================
# PRECISION MARKER DETECTION
# ============================================================

@dataclass
class Marker:
    x: int
    y: int
    radius: int
    score: float
    zone: str


def hue_distance(
    hue: float,
    target: float,
) -> float:
    diff = abs(hue - target)
    return min(diff, 180.0 - diff)


def marker_color_score(
    hsv: np.ndarray,
    x: int,
    y: int,
    radius: int,
) -> float:
    """
    Scores the CENTER of a circular candidate.

    The real markers in the provided phone image cluster around:
      Hue ~ 152-166
      Saturation ~ 40-90
      Value ~ 140-200

    Brightness is deliberately weakly weighted because shadows
    should not make a person disappear.
    """

    height, width = hsv.shape[:2]

    inner_radius = max(
        3,
        int(radius * 0.42),
    )

    x0 = max(0, x - inner_radius)
    x1 = min(width, x + inner_radius + 1)
    y0 = max(0, y - inner_radius)
    y1 = min(height, y + inner_radius + 1)

    patch = hsv[y0:y1, x0:x1]

    if patch.size == 0:
        return 0.0

    yy, xx = np.ogrid[
        y0 - y:y1 - y,
        x0 - x:x1 - x,
    ]

    disk = (
        xx * xx
        + yy * yy
        <= inner_radius * inner_radius
    )

    pixels = patch[disk]

    if len(pixels) < 8:
        return 0.0

    hue = float(
        np.median(pixels[:, 0])
    )

    saturation = float(
        np.median(pixels[:, 1])
    )

    value = float(
        np.median(pixels[:, 2])
    )

    dh = hue_distance(
        hue,
        MARKER_HUE_TARGET,
    )

    # Broad hue tolerance.
    hue_score = math.exp(
        -((dh / 13.0) ** 2)
    )

    # Broad saturation tolerance.
    sat_score = math.exp(
        -(
            (
                saturation
                - MARKER_SAT_TARGET
            )
            / 65.0
        ) ** 2
    )

    # Reject extremely dark candidates, but shadows are allowed.
    value_score = float(
        np.clip(
            (value - 75.0) / 95.0,
            0.0,
            1.0,
        )
    )

    return (
        0.58 * hue_score
        + 0.30 * sat_score
        + 0.12 * value_score
    )


def suppress_duplicates(
    candidates: List[Marker],
) -> List[Marker]:

    ordered = sorted(
        candidates,
        key=lambda marker: marker.score,
        reverse=True,
    )

    selected: List[Marker] = []

    for marker in ordered:
        duplicate = False

        for kept in selected:
            distance = math.hypot(
                marker.x - kept.x,
                marker.y - kept.y,
            )

            minimum = max(
                14.0,
                0.95
                * max(
                    marker.radius,
                    kept.radius,
                ),
            )

            if distance < minimum:
                duplicate = True
                break

        if not duplicate:
            selected.append(marker)

    return selected


def detect_markers(
    frame: np.ndarray,
):
    """
    Hough proposes circles.
    Marker color ranks them.
    Best 10 are selected.
    """

    polygons = get_zone_polygons(
        frame.shape
    )

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    hsv = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2HSV,
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )

    enhanced = clahe.apply(gray)

    enhanced = cv2.GaussianBlur(
        enhanced,
        (7, 7),
        1.25,
    )

    height, width = frame.shape[:2]
    base = min(height, width)

    min_radius = max(
        6,
        int(
            base
            * MIN_RADIUS_RATIO
        ),
    )

    max_radius = max(
        min_radius + 5,
        int(
            base
            * MAX_RADIUS_RATIO
        ),
    )

    min_distance = max(
        15,
        int(base * 0.025),
    )

    circles = cv2.HoughCircles(
        enhanced,
        cv2.HOUGH_GRADIENT,
        dp=1.10,
        minDist=min_distance,
        param1=HOUGH_PARAM1,
        param2=HOUGH_PARAM2,
        minRadius=min_radius,
        maxRadius=max_radius,
    )

    candidates: List[Marker] = []

    if circles is not None:
        for (
            x,
            y,
            radius,
        ) in np.round(
            circles[0]
        ).astype(int):

            zone = point_zone(
                (int(x), int(y)),
                polygons,
            )

            if zone is None:
                continue

            score = marker_color_score(
                hsv,
                int(x),
                int(y),
                int(radius),
            )

            if score < MIN_MARKER_SCORE:
                continue

            candidates.append(
                Marker(
                    x=int(x),
                    y=int(y),
                    radius=int(radius),
                    score=float(score),
                    zone=zone,
                )
            )

    candidates = suppress_duplicates(
        candidates
    )

    candidates = sorted(
        candidates,
        key=lambda marker: marker.score,
        reverse=True,
    )

    # The physical demo has exactly 10 marked figures.
    markers = candidates[
        :EXPECTED_TOTAL
    ]

    return markers, polygons


# ============================================================
# COUNT STABILIZATION
# ============================================================

def raw_counts_from_markers(
    markers: List[Marker],
) -> Dict[str, int]:

    counts = {
        "A": 0,
        "B": 0,
        "C": 0,
    }

    for marker in markers:
        counts[marker.zone] += 1

    return counts


def update_stable_counts(
    raw_counts: Dict[str, int],
    detected_total: int,
) -> Dict[str, int]:
    """
    Fast debounce:
    - Only complete 10/10 frames are considered.
    - A new A/B/C distribution is accepted after it appears
      in COUNT_CONFIRM_FRAMES consecutive complete frames.
    - Bad/incomplete frames never erase the last stable result.
    """

    global stable_counts
    global pending_counts_tuple
    global pending_counts_frames

    if detected_total != EXPECTED_TOTAL:
        return dict(stable_counts)

    current = (
        raw_counts["A"],
        raw_counts["B"],
        raw_counts["C"],
    )

    if current == pending_counts_tuple:
        pending_counts_frames += 1
    else:
        pending_counts_tuple = current
        pending_counts_frames = 1

    if pending_counts_frames >= COUNT_CONFIRM_FRAMES:
        stable_counts = {
            "A": int(current[0]),
            "B": int(current[1]),
            "C": int(current[2]),
        }

    return dict(stable_counts)


def percentages_from_counts(
    counts: Dict[str, int],
) -> Dict[str, int]:

    return {
        zone: int(
            round(
                counts[zone]
                * 100
                / EXPECTED_TOTAL
            )
        )
        for zone in counts
    }


# ============================================================
# CROWD DECISION
# ============================================================

def decide_action(
    counts: Dict[str, int],
):
    a = counts["A"]
    b = counts["B"]
    c = counts["C"]

    if min(a, b, c) >= SECURITY_MIN_EACH:
        return (
            "closed",
            "closed",
            "SECURITY",
            "All zones occupied. Notify security and control incoming flow.",
        )

    if b >= ZONE_FULL:
        can_a = (
            b - a
            >= MIN_REDIRECT_DIFFERENCE
        )

        can_c = (
            b - c
            >= MIN_REDIRECT_DIFFERENCE
        )

        if can_a and can_c:
            if abs(a - c) <= 1:
                return (
                    "open",
                    "open",
                    "REDIRECT",
                    "Zone B is full. Open both passages.",
                )

            if a < c:
                return (
                    "open",
                    "closed",
                    "REDIRECT",
                    "Zone B is full. Redirect toward Zone A.",
                )

            return (
                "closed",
                "open",
                "REDIRECT",
                "Zone B is full. Redirect toward Zone C.",
            )

        if can_a:
            return (
                "open",
                "closed",
                "REDIRECT",
                "Zone B is full. Redirect toward Zone A.",
            )

        if can_c:
            return (
                "closed",
                "open",
                "REDIRECT",
                "Zone B is full. Redirect toward Zone C.",
            )

    if (
        a >= ZONE_FULL
        and a - b
        >= MIN_REDIRECT_DIFFERENCE
    ):
        return (
            "open",
            "closed",
            "REDIRECT",
            "Zone A is full. Open passage A-B.",
        )

    if (
        c >= ZONE_FULL
        and c - b
        >= MIN_REDIRECT_DIFFERENCE
    ):
        return (
            "closed",
            "open",
            "REDIRECT",
            "Zone C is full. Open passage B-C.",
        )

    if max(a, b, c) >= ZONE_WARNING:
        return (
            "closed",
            "closed",
            "WARNING",
            "Crowd concentration increasing. Monitoring.",
        )

    return (
        "closed",
        "closed",
        "NORMAL",
        "Occupancy balanced.",
    )


# ============================================================
# ESP32 / SERVOS
# ============================================================

def send_gate_command(
    g1: str,
    g2: str,
    force: bool = False,
):
    global last_gate_command_time
    global last_sent_gates

    if ESP32_BASE_URL is None:
        return True, ""

    now = time.time()

    if not force:
        if (
            g1,
            g2,
        ) == last_sent_gates:
            return True, ""

        if (
            now
            - last_gate_command_time
            < MIN_GATE_COMMAND_INTERVAL
        ):
            return True, ""

    try:
        response = session.get(
            f"{ESP32_BASE_URL}/gates",
            params={
                "g1": g1,
                "g2": g2,
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        last_gate_command_time = now
        last_sent_gates = (
            g1,
            g2,
        )

        with lock:
            status["gate1"] = g1
            status["gate2"] = g2

        return True, ""

    except Exception as exc:
        return False, str(exc)


def apply_servo_decision(
    decision,
):
    global candidate_servo_decision
    global candidate_servo_frames

    g1, g2, mode, message = decision

    with lock:
        status["mode"] = mode
        status["message"] = message
        auto_enabled = status["auto"]

    if not auto_enabled:
        return

    if decision == candidate_servo_decision:
        candidate_servo_frames += 1
    else:
        candidate_servo_decision = decision
        candidate_servo_frames = 1

    if (
        candidate_servo_frames
        >= SERVO_STABLE_FRAMES
    ):
        ok, error = send_gate_command(
            g1,
            g2,
        )

        if not ok:
            with lock:
                status["last_error"] = error


# ============================================================
# DRAWING
# ============================================================

def draw_overlay(
    frame: np.ndarray,
    markers: List[Marker],
    polygons,
    counts,
    percentages,
    raw_total: int,
):
    out = frame.copy()
    height, width = out.shape[:2]

    for zone in ("A", "B", "C"):
        color = ZONE_COLORS[zone]

        cv2.polylines(
            out,
            [polygons[zone]],
            True,
            color,
            3,
        )

        center = polygons[
            zone
        ].mean(
            axis=0
        ).astype(int)

        cv2.putText(
            out,
            (
                f"ZONE {zone}: "
                f"{counts[zone]} "
                f"({percentages[zone]}%)"
            ),
            (
                center[0] - 105,
                center[1],
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            color,
            2,
            cv2.LINE_AA,
        )

    for index, marker in enumerate(
        markers,
        start=1,
    ):
        color = ZONE_COLORS[
            marker.zone
        ]

        cv2.circle(
            out,
            (
                marker.x,
                marker.y,
            ),
            marker.radius + 3,
            (0, 255, 255),
            2,
        )

        cv2.circle(
            out,
            (
                marker.x,
                marker.y,
            ),
            3,
            color,
            -1,
        )

        cv2.putText(
            out,
            str(index),
            (
                marker.x + 10,
                marker.y - 9,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    with lock:
        mode = status["mode"]
        message = status["message"]
        gate1 = status["gate1"]
        gate2 = status["gate2"]

    mode_colors = {
        "NORMAL": (0, 200, 0),
        "WARNING": (0, 210, 255),
        "REDIRECT": (0, 140, 255),
        "SECURITY": (0, 0, 255),
        "MARKER CHECK": (0, 165, 255),
    }

    mode_color = mode_colors.get(
        mode,
        (255, 255, 255),
    )

    cv2.rectangle(
        out,
        (10, 10),
        (width - 10, 98),
        (20, 20, 20),
        -1,
    )

    cv2.putText(
        out,
        (
            f"{mode} | VISIBLE "
            f"{raw_total}/{EXPECTED_TOTAL}"
        ),
        (25, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        mode_color,
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        out,
        message[:92],
        (25, 72),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    cv2.putText(
        out,
        (
            f"G1(A-B): {gate1.upper()}   "
            f"G2(B-C): {gate2.upper()}"
        ),
        (25, height - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    return out


def resize_for_processing(frame: np.ndarray) -> np.ndarray:
    """
    Downscale large phone frames before Hough circle detection.
    Zone coordinates are normalized, so they remain correct.
    """
    height, width = frame.shape[:2]

    if width <= PROCESS_WIDTH:
        return frame

    scale = PROCESS_WIDTH / float(width)
    new_height = max(1, int(round(height * scale)))

    return cv2.resize(
        frame,
        (PROCESS_WIDTH, new_height),
        interpolation=cv2.INTER_AREA,
    )


# ============================================================
# MAIN PROCESSING LOOP
# ============================================================

def processing_worker():
    global latest_jpeg

    previous_time = time.time()

    while True:
        started = time.time()

        try:
            frame = camera_source.read()
            frame = resize_for_processing(frame)

            markers, polygons = (
                detect_markers(frame)
            )

            raw_total = len(markers)

            raw_counts = (
                raw_counts_from_markers(
                    markers
                )
            )

            counts = (
                update_stable_counts(
                    raw_counts,
                    raw_total,
                )
            )

            percentages = (
                percentages_from_counts(
                    counts
                )
            )

            with lock:
                status["camera_ok"] = True
                status["detected_total"] = raw_total
                status["counts"] = counts
                status["percentages"] = percentages
                status["last_error"] = ""

            if raw_total == EXPECTED_TOTAL:
                decision = decide_action(
                    counts
                )

                apply_servo_decision(
                    decision
                )

            else:
                # Do not replace stable counts when one visual frame is noisy.
                with lock:
                    status["mode"] = "MARKER CHECK"
                    status["message"] = (
                        f"Visible {raw_total}/{EXPECTED_TOTAL}. "
                        "Holding last stable occupancy until the next complete frame."
                    )

            annotated = draw_overlay(
                frame,
                markers,
                polygons,
                counts,
                percentages,
                raw_total,
            )

            ok, encoded = cv2.imencode(
                ".jpg",
                annotated,
                [
                    int(
                        cv2.IMWRITE_JPEG_QUALITY
                    ),
                    85,
                ],
            )

            if ok:
                with lock:
                    latest_jpeg = (
                        encoded.tobytes()
                    )

            now = time.time()
            delta = (
                now
                - previous_time
            )

            if delta > 0:
                with lock:
                    status["fps"] = round(
                        1.0 / delta,
                        1,
                    )

            previous_time = now

        except Exception as exc:
            with lock:
                status["camera_ok"] = False
                status["mode"] = "CAMERA ERROR"
                status["message"] = (
                    "Could not read phone camera."
                )
                status["last_error"] = str(exc)

        elapsed = (
            time.time()
            - started
        )

        time.sleep(
            max(
                0.02,
                FRAME_INTERVAL_SECONDS
                - elapsed,
            )
        )


# ============================================================
# DASHBOARD
# ============================================================

HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Physical AI Crowd Flow Demo</title>

<style>
body {
  margin:0;
  background:#10141f;
  color:white;
  font-family:Arial,sans-serif;
}
.container {
  max-width:1400px;
  margin:auto;
  padding:18px;
}
h1 {
  margin:0 0 6px;
  font-size:36px;
}
.subtitle {
  color:#aeb9cd;
  margin-bottom:18px;
  font-size:18px;
}
.grid {
  display:grid;
  grid-template-columns:minmax(0,2fr) minmax(330px,1fr);
  gap:18px;
}
.panel {
  background:#1b2232;
  border-radius:16px;
  padding:15px;
}
.camera {
  width:100%;
  display:block;
  border-radius:12px;
  background:#000;
}
.zones {
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:9px;
}
.zone {
  color:#111;
  border-radius:14px;
  text-align:center;
  padding:14px 6px;
}
.zone-a {background:#ff9797;}
.zone-b {background:#ffe47f;}
.zone-c {background:#9ee6a5;}
.count {
  font-size:42px;
  font-weight:800;
}
.percent {
  font-size:18px;
  font-weight:700;
}
.total {
  margin-top:12px;
  text-align:center;
  padding:12px;
  border-radius:10px;
  background:#273044;
  font-size:20px;
  font-weight:700;
}
.alert {
  margin-top:12px;
  padding:15px;
  border-radius:12px;
  font-weight:700;
}
.normal {background:#1e7d43;}
.warning {background:#887000;}
.redirect {background:#c76f00;}
.security {background:#b4232a;}
.marker-check {background:#9b6200;}
.camera-error {background:#b4232a;}
.status {
  margin-top:15px;
  line-height:1.65;
}
button {
  border:0;
  border-radius:8px;
  padding:10px 13px;
  margin:4px;
  font-weight:700;
  cursor:pointer;
}
.small {
  color:#b9c2d3;
  font-size:12px;
  overflow-wrap:anywhere;
  margin-top:7px;
}
@media (max-width:850px) {
  .grid {grid-template-columns:1fr;}
}
</style>
</head>

<body>
<div class="container">

<h1>Physical AI Crowd Flow Demo</h1>

<div class="subtitle">
Phone camera → fast stable tracking → Python decision → ESP32 → servo gates
</div>

<div class="grid">

<div class="panel">
<img id="camera" class="camera" src="/frame.jpg">
</div>

<div class="panel">

<div class="zones">

<div class="zone zone-a">
Zone A
<div class="count" id="aCount">0</div>
<div class="percent" id="aPct">0%</div>
</div>

<div class="zone zone-b">
Zone B
<div class="count" id="bCount">0</div>
<div class="percent" id="bPct">0%</div>
</div>

<div class="zone zone-c">
Zone C
<div class="count" id="cCount">0</div>
<div class="percent" id="cPct">0%</div>
</div>

</div>

<div class="total">
Visible markers: <span id="total">0</span>/10
</div>

<div id="alertBox" class="alert marker-check">
Waiting...
</div>

<div class="status">
Gate A-B: <b id="g1">closed</b><br>
Gate B-C: <b id="g2">closed</b><br>
Camera: <b id="cam">...</b><br>
FPS: <b id="fps">0</b><br>
Auto: <b id="auto">OFF</b><br>
Hardware: <b id="hardware">NO ESP32</b>
</div>

<div class="small" id="source"></div>
<div class="small" id="error"></div>

<hr>

<h3>Manual servo test</h3>

<button id="autoBtn">Auto ON/OFF</button><br>
<button id="g1Open">G1 Open</button>
<button id="g1Close">G1 Close</button><br>
<button id="g2Open">G2 Open</button>
<button id="g2Close">G2 Close</button>

<div class="small" id="result"></div>

</div>
</div>
</div>

<script>
async function post(url) {
  const result = document.getElementById('result');

  try {
    const response = await fetch(url,{method:'POST'});
    const data = await response.json();
    result.textContent = JSON.stringify(data);
  } catch(error) {
    result.textContent = String(error);
  }
}

function alertClass(mode) {
  const value = String(mode).toLowerCase();

  if(value==='security') return 'alert security';
  if(value==='redirect') return 'alert redirect';
  if(value==='warning') return 'alert warning';
  if(value==='marker check') return 'alert marker-check';
  if(value==='camera error') return 'alert camera-error';

  return 'alert normal';
}

async function refresh() {
  try {
    const response = await fetch('/api/status');
    const s = await response.json();

    aCount.textContent=s.counts.A;
    bCount.textContent=s.counts.B;
    cCount.textContent=s.counts.C;

    aPct.textContent=s.percentages.A+'%';
    bPct.textContent=s.percentages.B+'%';
    cPct.textContent=s.percentages.C+'%';

    total.textContent=s.detected_total;

    g1.textContent=s.gate1;
    g2.textContent=s.gate2;
    cam.textContent=s.camera_ok?'OK':'ERROR';
    fps.textContent=s.fps;
    auto.textContent=s.auto?'ON':'OFF';
    hardware.textContent=s.hardware_connected?'ESP32 CONNECTED':'NO ESP32';

    source.textContent='Camera: '+s.camera_source;
    error.textContent=s.last_error||'';

    const alertBox = document.getElementById('alertBox');
    alertBox.className = alertClass(s.mode);
    alertBox.textContent = s.mode + ': ' + s.message;

    camera.src='/frame.jpg?t='+Date.now();

  } catch(error) {}
}

document.getElementById('autoBtn').addEventListener(
  'click',
  ()=>post('/api/auto/toggle')
);
document.getElementById('g1Open').addEventListener(
  'click',
  ()=>post('/api/gate/1/open')
);
document.getElementById('g1Close').addEventListener(
  'click',
  ()=>post('/api/gate/1/close')
);
document.getElementById('g2Open').addEventListener(
  'click',
  ()=>post('/api/gate/2/open')
);
document.getElementById('g2Close').addEventListener(
  'click',
  ()=>post('/api/gate/2/close')
);

setInterval(refresh,450);
refresh();
</script>

</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def index():
    return Response(
        HTML,
        mimetype="text/html",
    )


@app.get("/frame.jpg")
def frame_jpg():
    with lock:
        jpg = latest_jpeg

    if jpg is None:
        blank = np.zeros(
            (480,640,3),
            dtype=np.uint8,
        )

        cv2.putText(
            blank,
            "Waiting for phone camera...",
            (70,240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255,255,255),
            2,
        )

        _, encoded = cv2.imencode(
            ".jpg",
            blank,
        )

        jpg = encoded.tobytes()

    return Response(
        jpg,
        mimetype="image/jpeg",
        headers={
            "Cache-Control":"no-store"
        },
    )


@app.get("/api/status")
def api_status():
    with lock:
        return jsonify(
            dict(status)
        )


@app.post("/api/auto/toggle")
def api_auto_toggle():
    with lock:
        if not status["hardware_connected"]:
            status["auto"] = False

            return jsonify({
                "ok": False,
                "message": "No ESP32 connected. Camera-only mode.",
            })

        status["auto"] = not status["auto"]
        value = status["auto"]

    return jsonify({
        "ok": True,
        "auto": value,
    })


@app.post("/api/gate/<int:gate>/<action>")
def api_gate(
    gate: int,
    action: str,
):
    if ESP32_BASE_URL is None:
        return jsonify({
            "ok": False,
            "message": "No ESP32 connected. Camera-only mode.",
        }), 400

    if gate not in (1,2):
        return jsonify({
            "ok":False,
            "error":"gate must be 1 or 2",
        }),400

    if action not in (
        "open",
        "close",
    ):
        return jsonify({
            "ok":False,
            "error":"invalid action",
        }),400

    try:
        response = session.get(
            f"{ESP32_BASE_URL}/gate",
            params={
                "g":gate,
                "state":action,
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        with lock:
            status[f"gate{gate}"] = (
                "open"
                if action=="open"
                else "closed"
            )

        return jsonify({
            "ok":True
        })

    except Exception as exc:
        return jsonify({
            "ok":False,
            "error":str(exc),
        }),500


# ============================================================
# MAIN
# ============================================================

def main():
    global ESP32_BASE_URL
    global CAMERA_URL
    global CAMERA_MODE
    global camera_source

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--esp32",
        default=None,
        help="Optional ESP32 IP, e.g. 192.168.1.6",
    )

    parser.add_argument(
        "--camera-url",
        required=True,
        help=(
            "Phone camera URL, e.g. "
            "http://10.212.1.188:8080/shot.jpg"
        ),
    )

    parser.add_argument(
        "--camera-mode",
        choices=[
            "auto",
            "snapshot",
            "stream",
        ],
        default="auto",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5000,
    )

    args = parser.parse_args()

    if args.esp32:
        ESP32_BASE_URL = (
            "http://"
            + args.esp32.strip()
        )

        with lock:
            status["hardware_connected"] = True
            status["auto"] = True

    else:
        ESP32_BASE_URL = None

        with lock:
            status["hardware_connected"] = False
            status["auto"] = False

    CAMERA_URL = (
        args.camera_url.strip()
    )

    CAMERA_MODE = (
        args.camera_mode
    )

    camera_source = CameraSource(
        CAMERA_URL,
        CAMERA_MODE,
    )

    with lock:
        status["camera_source"] = (
            CAMERA_URL
        )

    print("="*72)
    print("Physical AI Crowd Flow Demo - FAST STABLE VERSION")
    print(
        "ESP32:     "
        + (
            ESP32_BASE_URL
            if ESP32_BASE_URL
            else "not connected (camera-only mode)"
        )
    )
    print(f"Camera:    {CAMERA_URL}")
    print(f"Mode:      {camera_source.active_mode}")
    print(f"Dashboard: http://127.0.0.1:{args.port}")
    print("="*72)

    worker = threading.Thread(
        target=processing_worker,
        daemon=True,
    )

    worker.start()

    app.run(
        host="0.0.0.0",
        port=args.port,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()