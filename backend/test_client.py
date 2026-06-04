"""
DrowsyGuard 백엔드 API & 실시간 웹소켓 감지 테스트 클라이언트 스크립트

사용법:
    1. 백엔드 서버 구동 (uvicorn app.main:app)
    2. 다른 터미널에서 가상환경 활성화 후 실행:
       python test_client.py
"""

import asyncio
import sys
import json
import requests
import websockets

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

# 테스트용 계정 정보
TEST_USER = {
    "username": "testdriver",
    "name": "홍길동",
    "password": "password123",
    "emergency_contact": "010-1234-5678"
}

def register_and_login():
    """회원가입 및 로그인하여 JWT 토큰 발급"""
    print("🔄 1. 테스트 사용자 인증 진행 중...")
    
    # 회원가입 시도 (이미 있으면 409 리턴됨)
    try:
        reg_resp = requests.post(f"{BASE_URL}/auth/register", json=TEST_USER)
        if reg_resp.status_code == 201:
            print("  - 테스트 계정이 신규 생성되었습니다.")
        elif reg_resp.status_code == 409:
            print("  - 기존 테스트 계정을 사용합니다.")
        else:
            print(f"  - 회원가입 확인 중 예외 응답: {reg_resp.status_code}")
    except Exception as e:
        print(f"❌ 서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요. ({e})")
        sys.exit(1)

    # 로그인 시도
    login_resp = requests.post(f"{BASE_URL}/auth/login", json={
        "username": TEST_USER["username"],
        "password": TEST_USER["password"]
    })
    
    if login_resp.status_code != 200:
        print("❌ 로그인에 실패했습니다:", login_resp.json())
        sys.exit(1)
        
    token = login_resp.json()["access_token"]
    print("✅ 인증 성공! JWT 토큰을 획득했습니다.")
    return token

def get_or_create_session(token):
    """현재 활성화된 운전 세션을 가져오거나 새로 생성"""
    headers = {"Authorization": f"Bearer {token}"}
    
    # 활성 세션 조회
    resp = requests.get(f"{BASE_URL}/sessions/current", headers=headers)
    if resp.status_code == 200:
        session_id = resp.json()["id"]
        print(f"ℹ️ 기존 활성 세션을 사용합니다. (Session ID: {session_id})")
        return session_id
        
    # 새 세션 시작
    print("🔄 2. 새로운 운전 세션 시작 중...")
    start_resp = requests.post(f"{BASE_URL}/sessions/start", headers=headers)
    if start_resp.status_code == 200:
        session_id = start_resp.json()["id"]
        print(f"✅ 새 세션이 시작되었습니다. (Session ID: {session_id})")
        return session_id
    else:
        print("❌ 세션 생성 실패:", start_resp.json())
        sys.exit(1)

def start_detection_api(token, session_id):
    """서버 카메라 졸음 감지 루프 시작 API 호출"""
    print("🔄 3. 서버 졸음 감지 시작 요청 (카메라 켜기)...")
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{BASE_URL}/detection/start", 
        json={"session_id": session_id},
        headers=headers
    )
    if resp.status_code == 200:
        print("✅ 서버 감지 루프가 실행되었습니다! (카메라 켜짐)")
    else:
        # 이미 실행 중인 경우 409 에러 발생 가능
        if resp.status_code == 409:
            print("ℹ️ 서버 감지 루프가 이미 작동 중입니다.")
        else:
            print("❌ 감지 시작 요청 실패:", resp.json())
            sys.exit(1)

def stop_detection_api(token, session_id):
    """서버 카메라 졸음 감지 루프 종료 API 호출"""
    headers = {"Authorization": f"Bearer {token}"}
    requests.post(
        f"{BASE_URL}/detection/stop", 
        json={"session_id": session_id},
        headers=headers
    )
    print("\n🛑 서버 감지 루프(카메라)를 종료했습니다.")

def end_session_api(token, session_id):
    """운전 세션 종료 API 호출"""
    headers = {"Authorization": f"Bearer {token}"}
    requests.post(
        f"{BASE_URL}/sessions/end", 
        json={"session_id": session_id},
        headers=headers
    )
    print("🛑 운전 세션을 클리어했습니다.")

async def listen_detection_websocket(session_id):
    """웹소켓을 연결하여 실시간 졸음 감지 데이터를 수신 및 출력"""
    ws_uri = f"{WS_URL}/detection/ws/{session_id}"
    print(f"🔄 4. 웹소켓 연결 시도: {ws_uri}")
    
    try:
        async with websockets.connect(ws_uri) as websocket:
            print("\n==========================================================================")
            print("🚀 웹소켓 스트리밍 시작! (Ctrl+C를 누르면 안전하게 종료됩니다.)")
            print("--------------------------------------------------------------------------")
            print(f"{'얼굴감지':^6} | {'EAR (눈비율)':^11} | {'졸음확률':^8} | {'졸음단계':^6} | {'서버 메시지'}")
            print("==========================================================================")
            
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                
                # 핑(연결 유지용) 메시지 스킵
                if "ping" in data:
                    continue
                    
                face_status = "감지됨" if data.get("face_detected", False) else "미감지"
                ear = data.get("ear_value", 0.0)
                pred = data.get("pred_score", 0.0)
                level = data.get("drowsy_level", 0)
                msg = data.get("message", "정상")
                
                # 졸음 단계에 따라 시각적인 표시 구분
                level_str = f"LV {level}"
                if level == 1:
                    level_str = "⚠️ LV 1"
                elif level == 2:
                    level_str = "🚨 LV 2"
                elif level == 3:
                    level_str = "🔥 LV 3"
                
                print(f"{face_status:^8} | {ear:^12.4f} | {pred:^10.4f} | {level_str:^8} | {msg}")
                
    except websockets.exceptions.ConnectionClosed:
        print("\nℹ️ 웹소켓 연결이 서버에 의해 종료되었습니다.")
    except Exception as e:
        print(f"\n❌ 웹소켓 통신 중 에러 발생: {e}")

async def main():
    token = register_and_login()
    session_id = get_or_create_session(token)
    
    try:
        # 카메라 켜기
        start_detection_api(token, session_id)
        
        # 웹소켓 데이터 대기 및 수신
        await listen_detection_websocket(session_id)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 테스트가 중단되었습니다.")
    finally:
        # 자원 반납
        stop_detection_api(token, session_id)
        end_session_api(token, session_id)
        print("👋 테스트 클라이언트를 종료합니다.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
