"""Godzilla card detection and OBS audio-source control."""
import argparse
import base64
import getpass
import hashlib
import json
import msvcrt
import time
from pathlib import Path

import cv2
import numpy as np
import websocket

ROOT = Path(__file__).resolve().parent
ABSENCE_SECONDS = 3.0
MAX_SAMPLE_GAP = 0.7


class Detector:
    def __init__(self):
        self.reveal = self.load("reveal")
        self.hud = self.load("hud")
        board = self.load("board")
        self.board = [
            cv2.resize(board, None, fx=scale, fy=scale)
            for scale in (0.87, 1.0, 1.13)
        ]

    @staticmethod
    def load(name):
        data = (ROOT / (name + ".png")).read_bytes()
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)

    @staticmethod
    def score(area, template):
        return float(cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED).max())

    def scan(self, frame):
        height, width = frame.shape[:2]
        if abs(width / height - 16 / 9) > 0.03:
            raise ValueError(f"Expected uncropped 16:9 gameplay, got {width}x{height}")
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540))
        reveal = self.score(gray[110:380, 345:615], self.reveal)
        board = max(self.score(gray[95:394, 170:790], item) for item in self.board)
        hud = self.score(gray[237:304, 12:80], self.hud)
        present = reveal >= 0.72 or board >= 0.72
        # A weak card match is ambiguous, even if the HUD is visible.
        clear = max(reveal, board) < 0.61 and hud >= 0.75
        return present, clear, (reveal, board, hud)


class Gate:
    def __init__(self, resume):
        self.resume = resume
        self.muted = False
        self.manual = False
        self.last = None
        self.clear_since = None

    def sample(self, now, present, clear):
        if self.last is None or now - self.last > MAX_SAMPLE_GAP or not clear:
            self.clear_since = None
        self.last = now
        if present and not self.muted:
            self.muted = True
            return True
        if self.muted and self.resume and clear and not self.manual:
            if self.clear_since is None:
                self.clear_since = now
            if now - self.clear_since >= ABSENCE_SECONDS:
                self.muted = False
                self.clear_since = None
                return False
        return None


class Obs:
    def __init__(self, port, password):
        self.socket = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=1.5)
        self.serial = 0
        try:
            hello = self.receive(0)
            identify = {"rpcVersion": 1, "eventSubscriptions": 0}
            auth = hello.get("authentication")
            if auth:
                if password is None:
                    raise ValueError("OBSのWebSocketパスワードを入力してください。")
                secret = self.digest(password + auth["salt"])
                identify["authentication"] = self.digest(secret + auth["challenge"])
            self.send(1, identify)
            self.receive(2)
        except Exception:
            self.socket.shutdown()
            raise

    @staticmethod
    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()

    def send(self, op, data):
        self.socket.send(json.dumps({"op": op, "d": data}))

    def receive(self, expected):
        opcode, payload = self.socket.recv_data()
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
            if code == 4009:
                raise RuntimeError(
                    "OBSの認証に失敗しました。OBSの「ツール → WebSocketサーバー設定」にある"
                    "パスワードを確認してください。"
                )
            suffix = f"（コード {code}）" if code is not None else ""
            raise RuntimeError(f"OBSとの接続が閉じられました{suffix}。")
        if opcode != websocket.ABNF.OPCODE_TEXT or not payload:
            raise RuntimeError("OBSから応答を読み取れません。WebSocketのポートを確認してください。")
        try:
            packet = json.loads(payload)
        except (ValueError, UnicodeError):
            raise RuntimeError("OBSの応答を読み取れません。WebSocketのポートを確認してください。") from None
        if packet["op"] != expected:
            raise RuntimeError(f"Unexpected OBS message (op {packet['op']})")
        return packet["d"]

    def request(self, name, **fields):
        self.serial += 1
        request_id = str(self.serial)
        self.send(6, {"requestId": request_id, "requestType": name, "requestData": fields})
        data = self.receive(7)
        if data["requestId"] != request_id:
            raise RuntimeError("Unexpected OBS request ID")
        status = data["requestStatus"]
        if not status["result"]:
            raise RuntimeError(f"{name}: {status.get('comment', status['code'])}")
        return data.get("responseData", {})


def report(now, muted, scores, log):
    reveal, board, hud = scores
    log(f"[{now:7.2f}s] {'MUTE' if muted else 'RESTORE'} "
        f"(reveal={reveal:.2f}, board={board:.2f}, HUD={hud:.2f})")


def replay(path, detector, gate, trace=False, *, log=print, command=lambda: None):
    video = cv2.VideoCapture(str(path))
    if not video.isOpened():
        raise RuntimeError("Could not open the video file")
    next_sample = 0.0
    try:
        while True:
            if command() == "q":
                return
            ok = video.grab()
            if not ok:
                break
            now = video.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if now < next_sample:
                continue
            next_sample = now + 0.095
            ok, frame = video.retrieve()
            if not ok:
                raise RuntimeError("Video frame could not be decoded")
            present, clear, scores = detector.scan(frame)
            action = gate.sample(now, present, clear)
            if trace:
                print(f"{now:.3f},{int(present)},{int(clear)}," +
                      ",".join(f"{score:.3f}" for score in scores))
            if action is not None:
                report(now, action, scores, log)
    finally:
        video.release()
    log(f"End of recording: {'would remain muted' if gate.muted else 'not muted'}")


def console_command():
    return msvcrt.getwch().lower() if msvcrt.kbhit() else None


def live(args, detector, gate, password, *, log=print, command=console_command):
    obs = Obs(args.port, password)
    try:
        initial_mute = obs.request("GetInputMute", inputName=args.audio)["inputMuted"]
        obs.request("GetSourceActive", sourceName=args.video)
        start = time.monotonic()

        def apply(muted):
            if args.apply:
                obs.request("SetInputMute", inputName=args.audio, inputMuted=muted or initial_mute)

        log("LIVE" if args.apply else "DRY RUN")
        log(f"Video: {args.video} / Audio: {args.audio} / Initially muted: {initial_mute}")
        last_heartbeat = -5.0
        while True:
            tick = time.monotonic()
            key = command()
            if key == "q":
                break
            if key in ("m", "r"):
                gate.manual = gate.muted = key == "m"
                gate.clear_since = None
                apply(gate.muted)
                log("Manual mute hold" if gate.muted else "Manual restore")
            if not obs.request("GetSourceActive", sourceName=args.video)["videoActive"]:
                raise RuntimeError("The game capture is no longer active in the OBS output")
            shot = obs.request("GetSourceScreenshot", sourceName=args.video, imageFormat="png",
                               imageWidth=960)["imageData"]
            data = np.frombuffer(base64.b64decode(shot.split(",", 1)[1]), dtype=np.uint8)
            frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if frame is None or frame.std() < 5:
                raise RuntimeError("Game capture is blank or unreadable")
            present, clear, scores = detector.scan(frame)
            now = time.monotonic() - start
            action = gate.sample(now, present, clear)
            if action is not None:
                apply(action)
                report(now, action, scores, log)
            if now - last_heartbeat >= 5:
                log(f"[{now:7.2f}s] {'holding mute' if gate.muted else 'watching'} "
                    f"(reveal={scores[0]:.2f}, board={scores[1]:.2f}, HUD={scores[2]:.2f})")
                last_heartbeat = now
            time.sleep(max(0.0, 0.10 - (time.monotonic() - tick)))
    except (Exception, KeyboardInterrupt):
        if args.apply:
            try:
                obs.request("SetInputMute", inputName=args.audio, inputMuted=True)
                log("Stopped with OBS game audio muted.")
            except Exception:
                log("Could not confirm OBS mute. Check the audio mixer immediately.")
        raise
    finally:
        obs.socket.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, help="Detect from a video without connecting to OBS")
    parser.add_argument("--trace", action="store_true", help="Print per-frame scores in replay mode")
    parser.add_argument("--video", help="OBS game-capture source name")
    parser.add_argument("--audio", help="OBS game-only audio source name")
    parser.add_argument("--port", type=int, default=4455)
    parser.add_argument("--auth", action="store_true", help="Prompt for the OBS WebSocket password")
    parser.add_argument("--apply", action="store_true", help="Change the OBS mute state")
    parser.add_argument("--auto-resume", action="store_true", help="Experimental automatic restore")
    args = parser.parse_args()
    if not args.replay and not (args.video and args.audio):
        parser.error("Supply --replay or both --video and --audio")
    detector = Detector()
    gate = Gate(args.auto_resume)
    if args.replay:
        replay(args.replay, detector, gate, args.trace)
    else:
        password = getpass.getpass("OBS WebSocket password: ") if args.auth else None
        print("With this console focused: M = hold mute, R = restore, Q = quit.")
        live(args, detector, gate, password, log=lambda message: print(message, flush=True))


if __name__ == "__main__":
    main()

