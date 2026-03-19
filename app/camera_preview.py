from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

_running = True


def _handle_sigterm(_signum, _frame):
    global _running
    _running = False


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.camera_preview <output_jpg>")
        return 2

    out_path = Path(sys.argv[1]).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gaze_path = out_path.with_name("gaze_status.json")

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        import cv2
    except Exception as e:
        print(f"cv2 import failed: {e}")
        return 1

    try:
        import mediapipe as mp
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,  # needed for iris landmarks
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        mediapipe_ok = True
    except Exception:
        face_mesh = None
        mediapipe_ok = False

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("camera open failed")
        return 1

    # Gaze tracking state
    LOOK_AWAY_THRESHOLD_SEC = 10.0
    look_away_start: float | None = None
    total_look_away_sec = 0.0
    look_away_count = 0
    currently_away = False

    try:
        consecutive_failures = 0
        while _running:
            ok, frame = cap.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures >= 50:
                    print("camera read failed")
                    return 1
                time.sleep(0.1)
                continue
            consecutive_failures = 0

            gaze_direction = "center"
            show_warning = False

            if mediapipe_ok and face_mesh is not None:
                try:
                    import numpy as np
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = face_mesh.process(rgb)

                    if results.multi_face_landmarks:
                        landmarks = results.multi_face_landmarks[0].landmark
                        h, w = frame.shape[:2]

                        # Iris landmarks (require refine_landmarks=True)
                        # Left eye iris center: 468, Left eye corners: 33 (left), 133 (right)
                        # Right eye iris center: 473, Right eye corners: 362 (left), 263 (right)
                        left_iris_x = landmarks[468].x
                        left_corner_l = landmarks[33].x
                        left_corner_r = landmarks[133].x

                        right_iris_x = landmarks[473].x
                        right_corner_l = landmarks[362].x
                        right_corner_r = landmarks[263].x

                        def gaze_ratio(iris_x, corner_l, corner_r):
                            span = corner_r - corner_l
                            if abs(span) < 1e-6:
                                return 0.5
                            return (iris_x - corner_l) / span

                        left_ratio = gaze_ratio(left_iris_x, left_corner_l, left_corner_r)
                        right_ratio = gaze_ratio(right_iris_x, right_corner_l, right_corner_r)
                        avg_ratio = (left_ratio + right_ratio) / 2.0

                        if avg_ratio < 0.35:
                            gaze_direction = "left"
                        elif avg_ratio > 0.65:
                            gaze_direction = "right"
                        else:
                            gaze_direction = "center"
                    else:
                        # No face detected
                        gaze_direction = "away"

                except Exception:
                    gaze_direction = "center"

            # Track look-away duration
            now = time.time()
            is_away = gaze_direction in {"left", "right", "away"}

            if is_away:
                if look_away_start is None:
                    look_away_start = now
                elapsed = now - look_away_start
                if elapsed >= LOOK_AWAY_THRESHOLD_SEC:
                    show_warning = True
                    if not currently_away:
                        look_away_count += 1
                        currently_away = True
            else:
                if look_away_start is not None:
                    total_look_away_sec += now - look_away_start
                look_away_start = None
                currently_away = False

            # Draw warning text on frame if looking away too long
            if show_warning:
                text = "Please look at the camera"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 1.2
                thickness = 3
                color = (0, 0, 255)  # Red in BGR

                text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
                text_x = (frame.shape[1] - text_size[0]) // 2
                text_y = 60

                # Draw dark shadow for readability
                cv2.putText(frame, text, (text_x + 2, text_y + 2),
                            font, font_scale, (0, 0, 0), thickness + 2)
                # Draw red text
                cv2.putText(frame, text, (text_x, text_y),
                            font, font_scale, color, thickness)

            # Write gaze status JSON for main app to read
            try:
                gaze_data = {
                    "direction": gaze_direction,
                    "show_warning": show_warning,
                    "total_look_away_sec": round(total_look_away_sec, 1),
                    "look_away_count": look_away_count,
                    "timestamp": now,
                }
                tmp_gaze = gaze_path.with_suffix(".tmp")
                tmp_gaze.write_text(json.dumps(gaze_data))
                tmp_gaze.replace(gaze_path)
            except Exception:
                pass

            # Write frame JPEG
            ok2, buf = cv2.imencode(".jpg", frame)
            if ok2:
                tmp_path = out_path.with_suffix(".tmp")
                tmp_path.write_bytes(buf.tobytes())
                tmp_path.replace(out_path)

            time.sleep(0.1)

    finally:
        if face_mesh is not None:
            face_mesh.close()
        cap.release()
        cv2.destroyAllWindows()
        time.sleep(0.3)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
