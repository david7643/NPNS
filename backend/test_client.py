"""DrowsyGuard API 및 실시간 감지 진단용 터미널 클라이언트.

사용법:
    1. 백엔드 서버 실행: uvicorn app.main:app
    2. 다른 터미널에서 실행: python test_client.py

테스트 중 키 조작(Windows에서는 Enter 불필요):
    0: 실제 정상 상태
    1: 실제 졸음 상태
    u: 정답 라벨 해제
    q: 테스트 종료
"""

import asyncio
import csv
import json
import os
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import requests
import websockets

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"
REQUEST_TIMEOUT_SECONDS = 10
DISPLAY_INTERVAL_SECONDS = 0.5
RAW_DROWSY_THRESHOLD = 0.5
EXPECTED_DETECTION_CODE_VERSION = "event-risk-v7"

TEST_USER = {
    "username": "testdriver",
    "name": "홍길동",
    "password": "password123",
    "emergency_contact": "010-1234-5678",
}

CSV_FIELDS = [
    "received_at", "elapsed_seconds", "ground_truth", "state", "frame_index",
    "face_detected", "face_detection_rate", "processing_fps", "left_ear",
    "right_ear", "ear_value", "prediction_ready", "pred_score",
    "drowsy_level", "buffer_size", "buffer_required", "level_1_counter",
    "level_2_counter", "level_3_counter", "no_face_duration_ms",
    "inference_ms", "calibrated", "calibration_progress", "eyes_closed",
    "eye_closed_seconds", "left_ear_baseline", "right_ear_baseline",
    "left_close_threshold", "right_close_threshold",
    "level_1_seconds", "level_2_seconds", "level_3_seconds", "message",
    "final_evaluation_ready", "recovery_pending",
    "model_eye_support", "model_window_seconds",
    "detection_code_version", "model_directory", "model_normalization", "model_threshold",
    "model_entry_seconds", "eye_entry_seconds", "closed_gap_tolerance_seconds",
    "model_release_id", "alert_active", "alert_event", "observation_valid",
    "new_drowsiness_event", "recent_event_count", "emergency_condition",
    "risk_latched", "transition_reason", "server_recovery_seconds",
    "server_recovery_pending", "ear_closure_ratio", "observed_window_seconds",
    "alarm_acknowledged", "alarm_ack_required",
]


def _response_detail(response):
    """HTTP 오류 응답에서 사람이 읽을 수 있는 내용을 추출한다."""
    try:
        body = response.json()
        return body.get("detail", body)
    except ValueError:
        return response.text or f"HTTP {response.status_code}"


def register_and_login():
    """테스트 계정으로 로그인하여 JWT 토큰을 발급받는다."""
    print("🔄 1. 테스트 사용자 인증 진행 중...")
    try:
        response = requests.post(
            f"{BASE_URL}/auth/register",
            json=TEST_USER,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 201:
            print("  - 테스트 계정이 신규 생성되었습니다.")
        elif response.status_code == 409:
            print("  - 기존 테스트 계정을 사용합니다.")
        else:
            print(
                f"  - 회원가입 확인 중 예외 응답: {response.status_code} "
                f"({_response_detail(response)})"
            )
    except requests.RequestException as exc:
        print(f"❌ 서버에 연결할 수 없습니다. 서버 상태를 확인하세요. ({exc})")
        raise SystemExit(1) from exc

    try:
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": TEST_USER["username"],
                "password": TEST_USER["password"],
                "device_id": "diagnostic-pc",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"❌ 로그인 요청에 실패했습니다. ({exc})")
        raise SystemExit(1) from exc

    if response.status_code != 200:
        print("❌ 로그인에 실패했습니다:", _response_detail(response))
        raise SystemExit(1)

    print("✅ 인증 성공! JWT 토큰을 획득했습니다.")
    return response.json()["access_token"]


def get_or_create_session(token):
    """현재 테스트 계정의 활성 세션을 가져오거나 새로 생성한다."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(
            f"{BASE_URL}/sessions/current",
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            session_id = response.json()["id"]
            print(f"ℹ️ 기존 활성 세션을 사용합니다. (Session ID: {session_id})")
            return session_id, False
        if response.status_code != 404:
            print("❌ 현재 세션 조회 실패:", _response_detail(response))
            raise SystemExit(1)

        print("🔄 2. 새로운 운전 세션 시작 중...")
        response = requests.post(
            f"{BASE_URL}/sessions/start",
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"❌ 세션 API 요청에 실패했습니다. ({exc})")
        raise SystemExit(1) from exc

    if response.status_code != 201:
        print("❌ 세션 생성 실패:", _response_detail(response))
        raise SystemExit(1)

    session_id = response.json()["session_id"]
    print(f"✅ 새 세션이 시작되었습니다. (Session ID: {session_id})")
    return session_id, True


def start_detection_api(token, session_id):
    """서버의 카메라 감지 루프를 시작한다."""
    print("🔄 3. 서버 졸음 감지 시작 요청 (카메라 켜기)...")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{BASE_URL}/detection/start",
            json={"session_id": session_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"감지 시작 요청 실패: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"감지 시작 실패: {_response_detail(response)} "
            f"(HTTP {response.status_code})"
        )
    print("✅ 서버 감지 루프가 실행되었습니다! (카메라 켜짐)")


def stop_detection_api(token, session_id):
    """서버의 카메라 감지 루프를 종료한다."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{BASE_URL}/detection/stop",
            json={"session_id": session_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in (200, 404):
            print("\n🛑 서버 감지 루프(카메라)를 종료했습니다.")
        else:
            print(f"\n⚠️ 감지 종료 실패: {_response_detail(response)}")
    except requests.RequestException as exc:
        print(f"\n⚠️ 감지 종료 요청 실패: {exc}")


def end_session_api(token, session_id):
    """테스트에 사용한 운전 세션을 종료한다."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{BASE_URL}/sessions/end",
            json={"session_id": session_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in (200, 404):
            print("🛑 운전 세션을 종료했습니다.")
        else:
            print(f"⚠️ 운전 세션 종료 실패: {_response_detail(response)}")
    except requests.RequestException as exc:
        print(f"⚠️ 운전 세션 종료 요청 실패: {exc}")


class DiagnosticRecorder:
    """프레임별 진단값과 사용자가 입력한 정답을 CSV 및 통계로 기록한다."""

    def __init__(self, session_id, output_dir=None):
        base_dir = Path(output_dir) if output_dir else Path(__file__).parent / "diagnostics"
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = base_dir / f"session_{session_id}_{timestamp}.csv"
        self._file = self.path.open("w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
        self._writer.writeheader()

        self.started_at = time.monotonic()
        self.ground_truth = "unknown"
        self.total_frames = 0
        self.face_frames = 0
        self.prediction_frames = 0
        self.fps_sum = 0.0
        self.fps_samples = 0
        self.max_no_face_ms = 0
        self.confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        self.final_confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        self.final_unavailable_frames = 0
        self.recovery_frames = 0
        self.false_alarm_events = 0
        self._previous_alarm = False
        self._drowsy_started_at = None
        self._latency_recorded = False
        self.alert_latencies = []
        self.episodes = []
        self._episode = None
        self._recovery_started_at = None
        self.recovery_delays = []
        self._rows_since_flush = 0

    def set_ground_truth(self, label):
        """현재 시점 이후 프레임에 적용할 정답 라벨을 변경한다."""
        previous = self.ground_truth
        self.ground_truth = label
        if label == previous:
            return
        now = time.monotonic()
        if self._episode is not None:
            self._episode["completed"] = True
            self._episode = None
        if previous == "drowsy" and label == "awake" and self._previous_alarm:
            self._recovery_started_at = now
        else:
            self._recovery_started_at = None
        if label == "drowsy" and previous != "drowsy":
            self._drowsy_started_at = now
            self._latency_recorded = False
            self._episode = {
                "completed": False,
                "preexisting_alarm": self._previous_alarm,
                "detected": False,
            }
            self.episodes.append(self._episode)
        elif label != "drowsy":
            self._drowsy_started_at = None
            self._latency_recorded = False

    def record(self, data):
        """WebSocket 감지 결과 한 건을 기록하고 통계를 갱신한다."""
        now = time.monotonic()
        self.total_frames += 1
        face_detected = bool(data.get("face_detected", False))
        prediction_ready = bool(data.get("prediction_ready", False))
        pred_score = float(data.get("pred_score", 0.0) or 0.0)
        model_threshold = float(data.get("model_threshold", RAW_DROWSY_THRESHOLD))
        drowsy_level = int(data.get("drowsy_level", 0) or 0)
        alarm = drowsy_level > 0
        final_ready = (
            face_detected
            and bool(data.get("calibrated", False))
            and data.get("state") in ("AWAKE", "DROWSY_PENDING", "DROWSY")
        )
        recovery_pending = self._recovery_started_at is not None
        if recovery_pending and final_ready and not alarm:
            self.recovery_delays.append(now - self._recovery_started_at)
            self._recovery_started_at = None
            recovery_pending = False

        if face_detected:
            self.face_frames += 1
        if prediction_ready:
            self.prediction_frames += 1

        fps = float(data.get("processing_fps", 0.0) or 0.0)
        if fps > 0:
            self.fps_sum += fps
            self.fps_samples += 1

        no_face_ms = int(data.get("no_face_duration_ms", 0) or 0)
        self.max_no_face_ms = max(self.max_no_face_ms, no_face_ms)

        if prediction_ready and self.ground_truth in ("awake", "drowsy"):
            actual_drowsy = self.ground_truth == "drowsy"
            predicted_drowsy = pred_score >= model_threshold
            if actual_drowsy and predicted_drowsy:
                self.confusion["tp"] += 1
            elif actual_drowsy:
                self.confusion["fn"] += 1
            elif predicted_drowsy:
                self.confusion["fp"] += 1
            else:
                self.confusion["tn"] += 1

        if self.ground_truth in ("awake", "drowsy"):
            if final_ready:
                actual_drowsy = self.ground_truth == "drowsy"
                key = ("tp" if actual_drowsy else "fp") if alarm else (
                    "fn" if actual_drowsy else "tn"
                )
                self.final_confusion[key] += 1
                if recovery_pending:
                    self.recovery_frames += 1
            else:
                self.final_unavailable_frames += 1

        if (
            final_ready and self.ground_truth == "awake"
            and alarm and not self._previous_alarm and not recovery_pending
        ):
            self.false_alarm_events += 1

        if (
            self.ground_truth == "drowsy"
            and final_ready
            and alarm
            and self._drowsy_started_at is not None
            and not self._latency_recorded
            and self._episode is not None
            and not self._episode["preexisting_alarm"]
        ):
            self.alert_latencies.append(now - self._drowsy_started_at)
            self._latency_recorded = True
            self._episode["detected"] = True

        if final_ready:
            self._previous_alarm = alarm
        counters = data.get("level_counters") or {}
        durations = data.get("level_durations_seconds") or {}
        diagnostic_ear = data.get("ear_value", "") if face_detected else ""
        self._writer.writerow({
            "received_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_seconds": round(now - self.started_at, 3),
            "ground_truth": self.ground_truth,
            "state": data.get("state", "UNKNOWN"),
            "frame_index": data.get("frame_index", ""),
            "face_detected": face_detected,
            "face_detection_rate": data.get("face_detection_rate", ""),
            "processing_fps": fps,
            "left_ear": data.get("left_ear", ""),
            "right_ear": data.get("right_ear", ""),
            "ear_value": diagnostic_ear,
            "prediction_ready": prediction_ready,
            "pred_score": pred_score,
            "drowsy_level": drowsy_level,
            "buffer_size": data.get("buffer_size", ""),
            "buffer_required": data.get("buffer_required", ""),
            "level_1_counter": counters.get("level_1", ""),
            "level_2_counter": counters.get("level_2", ""),
            "level_3_counter": counters.get("level_3", ""),
            "no_face_duration_ms": no_face_ms,
            "inference_ms": data.get("inference_ms", ""),
            "calibrated": data.get("calibrated", ""),
            "calibration_progress": data.get("calibration_progress", ""),
            "eyes_closed": data.get("eyes_closed", ""),
            "eye_closed_seconds": data.get("eye_closed_seconds", ""),
            "left_ear_baseline": data.get("left_ear_baseline", ""),
            "right_ear_baseline": data.get("right_ear_baseline", ""),
            "left_close_threshold": data.get("left_close_threshold", ""),
            "right_close_threshold": data.get("right_close_threshold", ""),
            "level_1_seconds": durations.get("level_1", ""),
            "level_2_seconds": durations.get("level_2", ""),
            "level_3_seconds": durations.get("level_3", ""),
            "message": data.get("message", ""),
            "final_evaluation_ready": final_ready,
            "recovery_pending": recovery_pending,
            "model_eye_support": data.get("model_eye_support", ""),
            "model_window_seconds": data.get("model_window_seconds", ""),
            "detection_code_version": data.get("detection_code_version", ""),
            "model_directory": data.get("model_directory", ""),
            "model_normalization": data.get("model_normalization", ""),
            "model_threshold": model_threshold,
            "model_entry_seconds": data.get("model_entry_seconds", ""),
            "eye_entry_seconds": data.get("eye_entry_seconds", ""),
            "closed_gap_tolerance_seconds": data.get("closed_gap_tolerance_seconds", ""),
            "model_release_id": data.get("model_release_id", ""),
            "alert_active": data.get("alert_active", ""),
            "alert_event": data.get("alert_event", ""),
            "observation_valid": data.get("observation_valid", ""),
            **{key: data.get(key, "") for key in (
                "new_drowsiness_event", "recent_event_count", "emergency_condition",
                "risk_latched", "transition_reason", "ear_closure_ratio", "observed_window_seconds",
                "alarm_acknowledged", "alarm_ack_required",
            )},
            "server_recovery_seconds": data.get("recovery_seconds", ""),
            "server_recovery_pending": data.get("recovery_pending", ""),
        })
        self._rows_since_flush += 1
        if self._rows_since_flush >= 30:
            self._file.flush()
            self._rows_since_flush = 0

    def print_summary(self):
        """수집된 진단 결과의 요약을 터미널에 출력한다."""
        tp, fp = self.confusion["tp"], self.confusion["fp"]
        tn, fn = self.confusion["tn"], self.confusion["fn"]
        labeled_predictions = tp + fp + tn + fn
        face_rate = self.face_frames / self.total_frames if self.total_frames else 0.0
        avg_fps = self.fps_sum / self.fps_samples if self.fps_samples else 0.0
        accuracy = (tp + tn) / labeled_predictions if labeled_predictions else None
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None

        def percent(value):
            return f"{value * 100:.1f}%" if value is not None else "측정 불가"

        print("\n" + "=" * 72)
        print("📊 감지 진단 요약")
        print("=" * 72)
        print(f"수신 프레임              : {self.total_frames}")
        print(f"얼굴 검출률              : {percent(face_rate)}")
        print(f"모델 예측 가능 프레임    : {self.prediction_frames}")
        print(f"평균 처리 FPS            : {avg_fps:.2f}")
        print(f"최장 연속 얼굴 미검출    : {self.max_no_face_ms / 1000:.2f}초")
        print(f"정답 포함 예측 프레임    : {labeled_predictions}")
        print(f"원시 모델 정확도         : {percent(accuracy)}")
        print(f"원시 모델 정밀도         : {percent(precision)}")
        print(f"원시 모델 재현율         : {percent(recall)}")
        print(f"원시 모델 TP/FP/TN/FN    : {tp}/{fp}/{tn}/{fn}")
        ftp, ffp = self.final_confusion["tp"], self.final_confusion["fp"]
        ftn, ffn = self.final_confusion["tn"], self.final_confusion["fn"]
        final_total = ftp + ffp + ftn + ffn
        print(f"최종 판정 정확도         : {percent((ftp + ftn) / final_total if final_total else None)}")
        print(f"최종 판정 정밀도         : {percent(ftp / (ftp + ffp) if ftp + ffp else None)}")
        print(f"최종 판정 재현율         : {percent(ftp / (ftp + ffn) if ftp + ffn else None)}")
        print(f"최종 판정 TP/FP/TN/FN    : {ftp}/{ffp}/{ftn}/{ffn}")
        print(f"판정 불가 라벨 프레임    : {self.final_unavailable_frames}")
        print(f"정상 구간 새 경고 횟수   : {self.false_alarm_events}")
        print(f"경고 해제 대기 프레임    : {self.recovery_frames} (최종 판정 오류에 포함)")
        completed = [e for e in self.episodes if e["completed"] and not e["preexisting_alarm"]]
        successes = sum(e["detected"] for e in completed)
        preexisting = sum(e["preexisting_alarm"] for e in self.episodes)
        pending = sum(not e["completed"] for e in self.episodes)
        print(f"완료 눈 감김 시험        : {len(completed)}회 / 성공 {successes}회 / 누락 {len(completed) - successes}회")
        print(f"시작 전 경고 중인 시험   : {preexisting}회 (성공률·지연 평가 제외)")
        print(f"미완료 눈 감김 시험      : {pending}회")
        if self.alert_latencies:
            average_latency = sum(self.alert_latencies) / len(self.alert_latencies)
            print(f"평균 단계 경고 지연      : {average_latency:.2f}초")
        else:
            print("평균 단계 경고 지연      : 측정 불가")
        if self.recovery_delays:
            print(f"평균 눈 뜬 후 경고 해제  : {sum(self.recovery_delays) / len(self.recovery_delays):.2f}초 ({len(self.recovery_delays)}회)")
        else:
            print("평균 눈 뜬 후 경고 해제  : 측정 불가")
        if self._recovery_started_at is not None:
            print("※ 종료 시 경고 해제 대기 중: 해제 시간 평균에 포함되지 않았습니다.")
        print("※ 지연은 키 입력부터 수신까지의 시간이며, 최종 판정 지표에는 회복 구간도 포함됩니다.")
        print("※ v6 최종 단계는 위험 이력과 회복 대기를 포함합니다. 프레임 정확도는 모델 정확도와 구분하세요.")
        print("※ 2·3단계는 m(경보 중지 확인) 이후 정상 상태 10초로 회복합니다. 위 해제 시간은 경고음 중지 시간이 아닙니다.")
        print(f"CSV 저장 위치            : {self.path.resolve()}")
        if labeled_predictions == 0:
            print("※ 정확도 측정을 위해 테스트 중 0(정상) 또는 1(졸음)을 입력하세요.")
        print("=" * 72)

    def close(self):
        if not self._file.closed:
            self._file.flush()
            self._file.close()


def _format_number(value, decimals=3):
    if value is None or value == "":
        return "-"
    return f"{float(value):.{decimals}f}"


def print_table_header():
    print("\n" + "=" * 122)
    print("🚀 진단 스트리밍 시작 — 0: 정상 | 1: 졸음 | u: 라벨 해제 | m: 경보 중지 확인 | q: 종료")
    print("-" * 122)
    print(
        f"{'정답':^7} | {'상태':^15} | {'FPS':^6} | {'검출률':^7} | "
        f"{'좌EAR':^7} | {'우EAR':^7} | {'평균EAR':^8} | {'점수':^6} | "
        f"{'버퍼':^7} | {'모델초(1/2/3)':^14} | {'단계':^4}"
    )
    print("=" * 122)


def print_diagnostic_row(data, ground_truth):
    counters = data.get("level_durations_seconds") or {}
    counter_text = (
        f"{counters.get('level_1', 0)}/"
        f"{counters.get('level_2', 0)}/"
        f"{counters.get('level_3', 0)}"
    )
    buffer_text = f"{data.get('buffer_size', 0)}/{data.get('buffer_required', 0)}"
    detection_rate = float(data.get("face_detection_rate", 0.0) or 0.0) * 100
    score = _format_number(data.get("pred_score"), 2) if data.get("prediction_ready") else "-"
    average_ear = data.get("ear_value") if data.get("face_detected") else None
    print(
        f"{ground_truth:^7} | {data.get('state', 'UNKNOWN'):^15} | "
        f"{float(data.get('processing_fps', 0.0) or 0.0):^6.1f} | "
        f"{detection_rate:^6.1f}% | "
        f"{_format_number(data.get('left_ear')):^7} | "
        f"{_format_number(data.get('right_ear')):^7} | "
        f"{_format_number(average_ear):^8} | "
        f"{score:^6} | {buffer_text:^7} | {counter_text:^14} | "
        f"{int(data.get('drowsy_level', 0) or 0):^4}"
    )


def _handle_key(key, recorder, stop_event):
    key = key.lower().strip()
    if key == "0":
        recorder.set_ground_truth("awake")
        print("\n🏷️ 정답 라벨: 정상(awake)")
    elif key == "1":
        recorder.set_ground_truth("drowsy")
        print("\n🏷️ 정답 라벨: 졸음(drowsy) — 경고 지연 측정 시작")
    elif key == "u":
        recorder.set_ground_truth("unknown")
        print("\n🏷️ 정답 라벨을 해제했습니다.")
    elif key == "q":
        print("\n🛑 테스트 종료 요청")
        stop_event.set()


async def keyboard_control(recorder, stop_event, on_alarm_stop=None):
    async def dispatch(key):
        if key.lower().strip() == "m" and on_alarm_stop is not None:
            await on_alarm_stop()
        else:
            _handle_key(key, recorder, stop_event)
    """플랫폼에 맞게 테스트 라벨 키를 비동기로 처리한다."""
    if os.name == "nt":
        import msvcrt

        while not stop_event.is_set():
            if msvcrt.kbhit():
                await dispatch(msvcrt.getwch())
            await asyncio.sleep(0.05)
        return

    print("ℹ️ 이 환경에서는 명령 입력 후 Enter를 누르세요.")
    loop = asyncio.get_running_loop()
    key_queue = asyncio.Queue()

    def read_stdin():
        key_queue.put_nowait(sys.stdin.readline())

    loop.add_reader(sys.stdin, read_stdin)
    try:
        while not stop_event.is_set():
            key = await key_queue.get()
            await dispatch(key)
    finally:
        loop.remove_reader(sys.stdin)


async def listen_detection_websocket(token, session_id, recorder):
    """인증된 WebSocket 결과를 수신해 표시하고 기록한다."""
    ws_uri = f"{WS_URL}/detection/ws/{session_id}"
    print(f"🔄 4. 인증된 WebSocket 연결 시도: {ws_uri}")
    stop_event = asyncio.Event()
    latest_risk_id = None
    async def acknowledge_alarm():
        try:
            response = await asyncio.to_thread(
                requests.post, f"{BASE_URL}/detection/alarm/acknowledge",
                headers={"Authorization": f"Bearer {token}"},
                json={"session_id": session_id, "risk_id": latest_risk_id}, timeout=10,
            )
            if response.status_code == 202:
                print("\n✅ 경보 중지 확인 접수 — 눈을 뜨고 정상 상태를 10초 유지하세요.")
            else:
                print(f"\n❌ 경보 중지 확인 실패: {_response_detail(response)}")
        except requests.exceptions.RequestException as error:
            print(f"\n❌ 경보 중지 확인 요청 실패: {error}")

    keyboard_task = asyncio.create_task(keyboard_control(recorder, stop_event, acknowledge_alarm))
    last_display_at = 0.0
    last_state = None
    runtime_announced = False

    try:
        async with websockets.connect(
            ws_uri,
            additional_headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            print_table_header()
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                data = json.loads(message)
                if "ping" in data:
                    continue
                if "error" in data:
                    print(f"\n❌ 서버 감지 오류: {data['error']}")
                    stop_event.set()
                    break

                if not runtime_announced:
                    if data.get("detection_code_version") != EXPECTED_DETECTION_CODE_VERSION:
                        raise RuntimeError(
                            "서버가 이전 감지 코드로 실행 중입니다. 서버 터미널에서 Ctrl+C 후 재시작하세요."
                        )
                    print(f"\n사용 모델: {data.get('model_directory')}")
                    print(
                        f"입력 보정: {data.get('model_normalization')} | "
                        f"모델 임계값: {data.get('model_threshold')} | "
                        f"최소 경고 시간: {data.get('model_entry_seconds')}초 | "
                        f"코드: {data.get('detection_code_version')}\n"
                    )
                    runtime_announced = True

                latest_risk_id = data.get("risk_id")
                recorder.record(data)
                now = time.monotonic()
                state = data.get("state")
                if now - last_display_at >= DISPLAY_INTERVAL_SECONDS or state != last_state:
                    print_diagnostic_row(data, recorder.ground_truth)
                    last_display_at = now
                    last_state = state
    except websockets.exceptions.ConnectionClosed as exc:
        print(f"\nℹ️ WebSocket 연결 종료: code={exc.code}, reason={exc.reason}")
    except websockets.exceptions.WebSocketException as exc:
        print(f"\n❌ WebSocket 연결 또는 통신 오류: {exc}")
    except OSError as exc:
        print(f"\n❌ WebSocket 서버에 연결할 수 없습니다: {exc}")
    finally:
        stop_event.set()
        keyboard_task.cancel()
        with suppress(asyncio.CancelledError):
            await keyboard_task


async def main():
    token = register_and_login()
    session_id, session_created = get_or_create_session(token)
    recorder = DiagnosticRecorder(session_id)
    detection_started = False

    try:
        start_detection_api(token, session_id)
        detection_started = True
        await listen_detection_websocket(token, session_id, recorder)
    except RuntimeError as exc:
        print(f"❌ {exc}")
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 테스트가 중단되었습니다.")
    finally:
        if detection_started:
            stop_detection_api(token, session_id)
        if session_created or detection_started:
            end_session_api(token, session_id)
        else:
            print("ℹ️ 기존 운전 세션은 종료하지 않고 유지했습니다.")
        recorder.close()
        recorder.print_summary()
        print("👋 테스트 클라이언트를 종료합니다.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
