"""앱 서버와 같은 모델·v7 로직으로 웹캠 영상과 경고를 표시한다.

프로젝트 루트: python model_training/realtime_detect.py
영상 창에서 q: 종료, r: 개인 보정과 입력·경고 상태 초기화.
서버 감지와 동시에 실행하면 같은 카메라를 사용할 수 없으므로 하나만 실행한다.
"""

import argparse
from pathlib import Path
import sys
import threading
import time

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.detection_service import (
    CameraDetector, LEFT_EYE, RIGHT_EYE, load_models, model_runtime_info, models_ready,
)

try:
    import winsound
except ImportError:
    winsound = None


def trigger_alarm():
    if winsound is not None:
        winsound.Beep(1000, 200)
    else:
        print("\a", end="", flush=True)


def draw_eye_landmarks(frame, landmarks, indices, color):
    height, width = frame.shape[:2]
    points = [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices]
    for point in points:
        cv2.circle(frame, point, 2, color, -1)
    for index, point in enumerate(points):
        cv2.line(frame, point, points[(index + 1) % len(points)], color, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, help="웹캠 번호. 기본값은 서버 설정과 같습니다.")
    parser.add_argument("--no-sound", action="store_true", help="경고음 없이 화면만 표시합니다.")
    args = parser.parse_args(argv)
    load_models()
    if not models_ready():
        raise SystemExit("모델 로드 실패: 배포 모델·스케일러·메타데이터와 로그를 확인하세요.")
    info = model_runtime_info()
    print(f"모델: {info['model_directory']}")
    print(f"코드: {info['detection_code_version']} | 최소 경고 시간: {info['model_entry_seconds']}초")
    print("처음에는 눈을 뜨고 정면을 보세요. 영상 창: q 종료 / r 재보정 / m 경보 중지 확인")
    try:
        with CameraDetector(args.camera) as detector:
            while True:
                frame, data = detector.read()
                height, width = frame.shape[:2]
                color = (0, 0, 255) if data["alert_active"] else (0, 255, 0)
                if detector.latest_landmarks is not None:
                    for indices in (LEFT_EYE, RIGHT_EYE):
                        draw_eye_landmarks(frame, detector.latest_landmarks, indices, color)
                cv2.rectangle(frame, (0, 0), (width, 100), (30, 30, 30), -1)
                text = f"{data['state']} / LEVEL {data['drowsy_level']}"
                if data["state"] == "CALIBRATING":
                    text = f"EYES OPEN - CALIBRATING {data['calibration_progress'] * 100:.0f}%"
                cv2.putText(frame, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                score = f"{data['pred_score']:.3f}" if data["prediction_ready"] else "waiting"
                cv2.putText(frame, f"EAR {data['ear_value']:.3f} | score {score} | FPS {data['processing_fps']:.1f}",
                            (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.putText(frame, f"Closed {data['eye_closed_seconds']:.2f}s | q quit / r reset / m acknowledge",
                            (10, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                duration = max(data["eye_closed_seconds"], data["level_durations_seconds"]["level_1"])
                fill = min(duration / data["model_entry_seconds"], 1.0)
                cv2.rectangle(frame, (10, 83), (width - 10, 93), (90, 90, 90), -1)
                cv2.rectangle(frame, (10, 83), (10 + int((width - 20) * fill), 93), color, -1)
                if data["alert_active"]:
                    cv2.rectangle(frame, (0, height // 3), (width, height // 3 + 55), (0, 0, 180), -1)
                    warning = "REST RECOMMENDED" if data["risk_latched"] else (
                        "RECOVERY PENDING" if data["recovery_pending"] else "WAKE UP"
                    )
                    cv2.putText(frame, f"{warning} - LEVEL {data['drowsy_level']}", (15, height // 3 + 36),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)
                if data["alert_event"] and not args.no_sound:
                    # 경고음이 프레임 처리와 눈 감김 시간 측정을 지연시키지 않게 한다.
                    threading.Thread(target=trigger_alarm, daemon=True).start()
                cv2.imshow("DrowsyGuard - shared app detection", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    detector.reset()
                    print("개인 보정 초기화: 눈을 뜨고 정면을 보세요.")
                if key == ord("m"):
                    detector.processor.engine.acknowledge_alarm(time.monotonic())
                    print("경보 중지 확인: 2·3단계에서 정상 상태 10초 후 0단계로 회복합니다.")
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
