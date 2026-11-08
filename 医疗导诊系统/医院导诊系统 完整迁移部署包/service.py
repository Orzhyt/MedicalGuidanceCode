"""医院导诊持久化接口，预约写操作由SQLite事务串行处理。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request

SHANGHAI = timezone(timedelta(hours=8))
HOLD_SECONDS = 180
ACTIONS = {"query", "hold", "confirm", "cancel", "status", "list"}

# 沿用医院和医生目录，补充导诊所需的科室。
DOCTORS = [
    (1, 1, "云岭省星海市第一人民医院", "内科", "张建国", "主任医师", "心血管内科", "cardiac"),
    (2, 1, "云岭省星海市第一人民医院", "内科", "李慧敏", "主治医师", "消化内科", "digestive"),
    (3, 1, "云岭省星海市第一人民医院", "外科", "王志强", "主任医师", "普外科", "surgery"),
    (4, 1, "云岭省星海市第一人民医院", "妇产科", "赵丽华", "副主任医师", "妇产科", "gynecology"),
    (5, 2, "云岭省星海市中心医院", "内科", "陈伟民", "主任医师", "内分泌科", "endocrine"),
    (6, 2, "云岭省星海市中心医院", "骨科", "刘海洋", "主治医师", "关节外科", "orthopedic"),
    (7, 2, "云岭省星海市中心医院", "神经内科", "周明", "主任医师", "头痛与脑血管病", "neurology"),
    (8, 3, "云岭省星海市第二人民医院", "内科", "吴小燕", "副主任医师", "呼吸内科", "respiratory"),
    (9, 3, "云岭省星海市第二人民医院", "儿科", "孙建华", "主任医师", "儿童常见病与保健", "pediatrics"),
    (10, 1, "云岭省星海市第一人民医院", "全科门诊", "郑安宁", "主治医师", "综合症状与初诊评估", "general"),
    (11, 2, "云岭省星海市中心医院", "耳鼻喉科", "林晓", "主治医师", "耳鼻喉常见病", "ent"),
    (12, 3, "云岭省星海市第二人民医院", "皮肤科", "许晴", "主治医师", "皮疹与皮肤过敏", "dermatology"),
    (13, 2, "云岭省星海市中心医院", "内科", "马宁", "主治医师", "消化内科", "digestive"),
    (14, 1, "云岭省星海市第一人民医院", "内科", "梁文清", "副主任医师", "消化内科", "digestive"),
]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def failure(code: str, message: str, **extra: object) -> dict[str, object]:
    return {"ok": False, "code": code, "message": message, **extra}


class BookingService:
    def __init__(self, path: Path, signing_key: str, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.signing_key = signing_key
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS doctors (
                  id INTEGER PRIMARY KEY,hospital_id INTEGER,hospital_name TEXT,department TEXT,
                  name TEXT,title TEXT,specialty TEXT,specialty_code TEXT);
                CREATE TABLE IF NOT EXISTS slots (
                  slot_id TEXT PRIMARY KEY,doctor_id INTEGER NOT NULL REFERENCES doctors(id),
                  starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,blocked INTEGER NOT NULL DEFAULT 0,
                  UNIQUE(doctor_id,starts_at));
                CREATE TABLE IF NOT EXISTS appointments (
                  appointment_id TEXT PRIMARY KEY,slot_id TEXT NOT NULL REFERENCES slots(slot_id),
                  patient_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('held','confirmed','cancelled','expired')),
                  hold_expires_at REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_slot ON appointments(slot_id)
                  WHERE status IN ('held','confirmed');
                CREATE TABLE IF NOT EXISTS requests (
                  patient_id TEXT NOT NULL,request_id TEXT NOT NULL,action TEXT NOT NULL,
                  payload_hash TEXT NOT NULL,response TEXT NOT NULL,created_at REAL NOT NULL,
                  PRIMARY KEY(patient_id,request_id));
                CREATE TABLE IF NOT EXISTS consultation_turns (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,
                  conversation_id TEXT NOT NULL,user_id TEXT NOT NULL,record TEXT NOT NULL,created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS consultation_history ON consultation_turns(conversation_id,sequence);
            """)
            connection.executemany("INSERT OR IGNORE INTO doctors VALUES(?,?,?,?,?,?,?,?)", DOCTORS)
        self.refresh_slots()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def __repr__(self) -> str:
        return f"BookingService(path={self.path!s})"

    def refresh_slots(self) -> None:
        tomorrow = datetime.fromtimestamp(self.clock(), SHANGHAI).date() + timedelta(days=1)
        records = []
        for offset in range(14):
            day = tomorrow + timedelta(days=offset)
            if day.weekday() == 6:
                continue
            for doctor in DOCTORS:
                for index, (hour, minute) in enumerate([(9, 0), (9, 20), (9, 40), (14, 0), (14, 20)]):
                    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)
                    blocked = int(offset == 0 and (index == 2 or (doctor[0] == 14 and hour < 12)))
                    records.append((f"S-{doctor[0]:02d}-{day:%Y%m%d}-{hour:02d}{minute:02d}", doctor[0], start.isoformat(), (start + timedelta(minutes=20)).isoformat(), blocked))
        with self.transaction() as connection:
            connection.executemany("INSERT OR IGNORE INTO slots VALUES(?,?,?,?,?)", records)

    def issue_token(self, patient_id: str, ttl: int = 3600) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", patient_id):
            raise ValueError("患者编号格式不正确")
        payload = b64(json.dumps({"sub": patient_id, "exp": int(self.clock()) + ttl, "aud": "medical-guidance"}, separators=(",", ":")).encode())
        signature = b64(hmac.new(self.signing_key.encode(), payload.encode(), hashlib.sha256).digest())
        return payload + "." + signature

    def patient(self, token: str) -> str:
        try:
            payload, signature = token.split(".")
            expected = b64(hmac.new(self.signing_key.encode(), payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Invalid signature")
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if claims["aud"] != "medical-guidance" or float(claims["exp"]) <= self.clock():
                raise ValueError("Expired token")
            patient_id = claims["sub"]
            if not isinstance(patient_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", patient_id):
                raise ValueError("Invalid subject")
            return patient_id
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
            raise ValueError("患者凭证无效或已过期，请重新获取后再试。") from error

    def catalog(self) -> list[dict[str, object]]:
        with self.transaction() as connection:
            return [{**dict(row), "registration_type": "expert" if "主任医师" in row["title"] else "ordinary"} for row in connection.execute("SELECT * FROM doctors ORDER BY id")]

    def booking_options(self, specialty_code: str) -> dict[str, object]:
        self.refresh_slots()
        doctors = [doctor for doctor in self.catalog() if doctor["specialty_code"] == specialty_code]
        with self.transaction() as connection:
            self._expire(connection)
            slots = [slot for slot in self._slots(connection) if slot["specialty_code"] == specialty_code]
        return {"ok": True, "doctors": doctors, "slots": slots, "server_time": datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat(), "timezone": "Asia/Shanghai"}

    def save_consultation(self, record: dict[str, object]) -> dict[str, object]:
        required = ["event_id", "conversation_id", "user_id", "patient_query", "patient_answer"]
        if any(not isinstance(record.get(key), str) or not record[key] for key in required):
            return failure("INVALID_RECORD", "咨询记录缺少必要信息。")

        def has_credentials(value: object) -> bool:
            if isinstance(value, dict):
                return any((key in {"patient_token", "api_key", "signing_key", "authorization", "service_api_key"} and bool(item)) or has_credentials(item) for key, item in value.items())
            if isinstance(value, list):
                return any(has_credentials(item) for item in value)
            return False

        if has_credentials(record):
            return failure("INVALID_RECORD", "咨询记录不能包含患者凭证或接口密钥。")
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self.transaction() as connection:
            existing = connection.execute("SELECT record FROM consultation_turns WHERE event_id=?", (record["event_id"],)).fetchone()
            if existing:
                if existing[0] != serialized:
                    return failure("RECORD_CONFLICT", "同一次咨询记录的内容不一致。")
                return {"ok": True, "replayed": True, "event_id": record["event_id"]}
            owner = connection.execute("SELECT user_id FROM consultation_turns WHERE conversation_id=? LIMIT 1", (record["conversation_id"],)).fetchone()
            if owner and owner[0] != record["user_id"]:
                return failure("RECORD_CONFLICT", "会话不属于当前患者。")
            connection.execute("INSERT INTO consultation_turns(event_id,conversation_id,user_id,record,created_at) VALUES(?,?,?,?,?)", (record["event_id"], record["conversation_id"], record["user_id"], serialized, self.clock()))
        return {"ok": True, "event_id": record["event_id"]}

    def consultation(self, conversation_id: str, latest: bool = False) -> dict[str, object]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT record,created_at FROM consultation_turns WHERE conversation_id=? ORDER BY sequence " + ("DESC LIMIT 1" if latest else "ASC"), (conversation_id,)).fetchall()
        if not rows:
            return failure("NOT_FOUND", "没有找到这次咨询记录。")
        turns = [{**json.loads(row[0]), "recorded_at": datetime.fromtimestamp(row[1], SHANGHAI).isoformat()} for row in rows]
        if latest:
            return {"ok": True, "conversation_id": conversation_id, "latest": turns[0]}
        messages = [{"role": role, "content": turn[key], "event_id": turn["event_id"]} for turn in turns for role, key in [("user", "patient_query"), ("assistant", "patient_answer")]]
        return {"ok": True, "conversation_id": conversation_id, "turns": turns, "messages": messages}

    def _expire(self, connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE appointments SET status='expired',updated_at=? WHERE status='held' AND hold_expires_at<=?", (self.clock(), self.clock()))

    def _window(self) -> tuple[str, str]:
        tomorrow = datetime.fromtimestamp(self.clock(), SHANGHAI).date() + timedelta(days=1)
        return tomorrow.isoformat(), (tomorrow + timedelta(days=14)).isoformat()

    def _slots(self, connection: sqlite3.Connection, doctor_id: int = 0, available_only: bool = True) -> list[dict[str, object]]:
        start, end = self._window()
        rows = connection.execute("""
          SELECT s.*,d.hospital_id,d.hospital_name,d.department,d.name AS doctor_name,d.specialty,d.specialty_code,
            a.status AS active_status FROM slots s JOIN doctors d ON d.id=s.doctor_id
          LEFT JOIN appointments a ON a.slot_id=s.slot_id AND a.status IN ('held','confirmed')
          WHERE substr(s.starts_at,1,10)>=? AND substr(s.starts_at,1,10)<? AND (?=0 OR s.doctor_id=?)
          ORDER BY s.starts_at,s.doctor_id
        """, (start, end, doctor_id, doctor_id))
        result = []
        for row in rows:
            record = dict(row)
            available = not row["blocked"] and row["active_status"] is None
            if available_only and not available:
                continue
            title = next(doctor[5] for doctor in DOCTORS if doctor[0] == record["doctor_id"])
            record.update(available=available, status="available" if available else "unavailable", capacity=1, registration_type="expert" if "主任医师" in title else "ordinary")
            record.pop("blocked")
            record.pop("active_status")
            result.append(record)
        return result

    def _appointment(self, connection: sqlite3.Connection, appointment_id: str, patient_id: str) -> dict[str, object] | None:
        row = connection.execute("""
          SELECT a.*,s.starts_at,s.ends_at,s.doctor_id,d.hospital_name,d.hospital_id,d.department,
            d.name AS doctor_name,d.specialty FROM appointments a JOIN slots s ON s.slot_id=a.slot_id
          JOIN doctors d ON d.id=s.doctor_id WHERE a.appointment_id=? AND a.patient_id=?
        """, (appointment_id, patient_id)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["expires_at"] = datetime.fromtimestamp(float(row["hold_expires_at"]), SHANGHAI).isoformat()
        return result

    def _alternatives(self, connection: sqlite3.Connection, slot_id: str, patient_id: str) -> list[dict[str, object]]:
        target = connection.execute("SELECT d.* FROM slots s JOIN doctors d ON d.id=s.doctor_id WHERE s.slot_id=?", (slot_id,)).fetchone()
        if target is None:
            return []
        records = self._slots(connection)
        # Offer only same clinical specialty. Never substitute an unrelated physician.
        records = [r for r in records if r["specialty_code"] == target["specialty_code"] and not self._overlap(connection, patient_id, str(r["starts_at"]), str(r["ends_at"]))]
        records.sort(key=lambda r: (0 if r["doctor_id"] == target["id"] else 1 if r["hospital_id"] == target["hospital_id"] else 2, str(r["starts_at"])))
        return records[:5]

    def _overlap(self, connection: sqlite3.Connection, patient_id: str, start: str, end: str) -> bool:
        return connection.execute("""SELECT 1 FROM appointments a JOIN slots s ON s.slot_id=a.slot_id
          WHERE a.patient_id=? AND a.status IN ('held','confirmed') AND s.starts_at<? AND s.ends_at>? LIMIT 1""", (patient_id, end, start)).fetchone() is not None

    def run(self, payload: dict[str, object]) -> dict[str, object]:
        action = str(payload.get("action", ""))
        if action not in ACTIONS:
            return failure("INVALID_ACTION", "请选择查询档期、申请占位、确认、取消、查询状态或查看我的预约。")
        if action == "query":
            self.refresh_slots()
            try:
                doctor_id = int(str(payload.get("doctor_id") or 0))
            except ValueError:
                return failure("INVALID_DOCTOR", "医生编号请只填数字。")
            with self.transaction() as connection:
                self._expire(connection)
                if doctor_id and not connection.execute("SELECT 1 FROM doctors WHERE id=?", (doctor_id,)).fetchone():
                    return failure("INVALID_DOCTOR", "没有找到这位医生，请检查医生编号。")
                records = self._slots(connection, doctor_id)
                return {"ok": True, "code": "SLOTS", "message": "这是当前可预约的时段。查看档期不会占号，提交预约后才会为您临时保留。", "slots": records[:100], "total": len(records), "has_more": len(records) > 100, "server_time": datetime.fromtimestamp(self.clock(), SHANGHAI).isoformat(), "hold_seconds": HOLD_SECONDS, "timezone": "Asia/Shanghai"}
        try:
            patient_id = self.patient(str(payload.get("patient_token", "")))
        except ValueError as error:
            return failure("UNAUTHORIZED", str(error))
        request_id = str(payload.get("request_id", ""))
        if action in {"hold", "confirm", "cancel"} and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
            return failure("INVALID_REQUEST_ID", "请填写请求编号；同一次操作重试时，请使用原来的编号。")
        clean = {"action": action, "slot_id": str(payload.get("slot_id", "")), "appointment_id": str(payload.get("appointment_id", ""))}
        digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
        self.refresh_slots()
        with self.transaction() as connection:
            self._expire(connection)
            if action in {"hold", "confirm", "cancel"}:
                previous = connection.execute("SELECT * FROM requests WHERE patient_id=? AND request_id=?", (patient_id, request_id)).fetchone()
                if previous:
                    if previous["payload_hash"] != digest:
                        return failure("IDEMPOTENCY_CONFLICT", "这个请求编号已经用于另一项操作，请为新的操作换一个编号。")
                    result = json.loads(previous["response"])
                    result["replayed"] = True
                    if result.get("appointment"):
                        current = self._appointment(connection, result["appointment"]["appointment_id"], patient_id)
                        result["appointment"] = current
                        if current and current["status"] == "expired" and action == "hold":
                            result.update(ok=False, code="HOLD_EXPIRED", message="之前保留的时段已过期，请重新选择时段，并使用新的请求编号。", alternatives=self._alternatives(connection, str(current["slot_id"]), patient_id))
                    if result.get("alternatives"):
                        result["alternatives"] = self._alternatives(connection, clean["slot_id"], patient_id)
                    return result
            result = self._perform(connection, action, patient_id, request_id, clean)
            if action in {"hold", "confirm", "cancel"}:
                connection.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)", (patient_id, request_id, action, digest, json.dumps(result, ensure_ascii=False), self.clock()))
            return result

    def _perform(self, connection: sqlite3.Connection, action: str, patient_id: str, request_id: str, clean: dict[str, str]) -> dict[str, object]:
        if action == "list":
            ids = connection.execute("SELECT appointment_id FROM appointments WHERE patient_id=? ORDER BY created_at DESC", (patient_id,)).fetchall()
            return {"ok": True, "code": "APPOINTMENTS", "message": "已查到您的预约记录。", "appointments": [self._appointment(connection, row[0], patient_id) for row in ids]}
        if action == "hold":
            target = connection.execute("SELECT * FROM slots WHERE slot_id=?", (clean["slot_id"],)).fetchone()
            start, end = self._window()
            if target is None or not start <= target["starts_at"][:10] < end:
                return failure("INVALID_SLOT", "这个时段目前不能预约，请重新查询医生档期。")
            active = connection.execute("SELECT * FROM appointments WHERE slot_id=? AND status IN ('held','confirmed')", (clean["slot_id"],)).fetchone()
            if active and active["patient_id"] == patient_id:
                return {"ok": True, "code": "EXISTING_APPOINTMENT", "message": "您已经预约或保留了这个时段，不需要重复提交。", "appointment": self._appointment(connection, active["appointment_id"], patient_id)}
            if target["blocked"] or active:
                return failure("SLOT_TAKEN", "这个时段已被其他患者占用或约满，请选择其他时段。", alternatives=self._alternatives(connection, clean["slot_id"], patient_id))
            if self._overlap(connection, patient_id, target["starts_at"], target["ends_at"]):
                return failure("PATIENT_TIME_CONFLICT", "您在这个时间已经有其他预约或保留的时段，请换一个时间。", alternatives=self._alternatives(connection, clean["slot_id"], patient_id))
            ident = "APT-" + secrets.token_hex(8)
            now = self.clock()
            connection.execute("INSERT INTO appointments VALUES(?,?,?,?,?,?,?)", (ident, clean["slot_id"], patient_id, "held", now + HOLD_SECONDS, now, now))
            return {"ok": True, "code": "HELD", "message": "已为您临时保留这个时段，请在3分钟内确认预约。", "appointment": self._appointment(connection, ident, patient_id)}
        appointment_id = clean["appointment_id"]
        if action == "status" and not appointment_id and request_id:
            saved = connection.execute("SELECT response FROM requests WHERE patient_id=? AND request_id=?", (patient_id, request_id)).fetchone()
            if saved:
                previous = json.loads(saved[0])
                if not previous.get("appointment"):
                    previous["replayed"] = True
                    return previous
                appointment_id = previous["appointment"]["appointment_id"]
        current = self._appointment(connection, appointment_id, patient_id)
        if current is None:
            return failure("NOT_FOUND", "没有找到您的这条预约记录，请检查预约编号。")
        status = current["status"]
        if action == "status":
            return {"ok": True, "code": "APPOINTMENT_STATUS", "message": "已查到这条预约的当前状态。", "appointment": current}
        if action == "confirm":
            if status == "expired":
                return failure("HOLD_EXPIRED", "临时保留已过期，预约还没有成功，请重新选择时段。", appointment=current, alternatives=self._alternatives(connection, str(current["slot_id"]), patient_id))
            if status == "cancelled":
                return failure("INVALID_STATE", "这条预约已经取消，不能再确认；需要的话，请重新选择时段。", appointment=current)
            connection.execute("UPDATE appointments SET status='confirmed',updated_at=? WHERE appointment_id=? AND status='held'", (self.clock(), appointment_id))
            return {"ok": True, "code": "CONFIRMED", "message": "预约成功，请按预约时间到院就诊。", "appointment": self._appointment(connection, appointment_id, patient_id)}
        if action == "cancel":
            connection.execute("UPDATE appointments SET status='cancelled',updated_at=? WHERE appointment_id=? AND status IN ('held','confirmed')", (self.clock(), appointment_id))
            return {"ok": True, "code": "CANCELLED" if status != "expired" else "ALREADY_RELEASED", "message": "这个时段已释放，其他患者可以预约。", "appointment": self._appointment(connection, appointment_id, patient_id)}
        return failure("INVALID_ACTION", "不支持该操作")


def create_app(service: BookingService, api_key: str) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 524288

    @app.before_request
    def authenticate() -> tuple[object, int] | None:
        if request.path != "/health" and not hmac.compare_digest(request.headers.get("X-Service-Key", ""), api_key):
            return jsonify(failure("SERVICE_UNAUTHORIZED", "医院接口密钥不正确，请联系维护人员检查配置。")), 401
        return None

    @app.get("/health")
    def health() -> object:
        return jsonify({"ok": True, "service": "medical-guidance", "version": "1.0.0"})

    @app.get("/api/doctors")
    def doctors() -> object:
        return jsonify({"ok": True, "doctors": service.catalog()})

    @app.get("/api/booking/options")
    def options() -> object:
        return jsonify(service.booking_options(request.args.get("specialty_code", "general")))

    @app.post("/api/consultations/record")
    def record_consultation() -> object:
        record = request.get_json(silent=True)
        if not isinstance(record, dict):
            return jsonify(failure("INVALID_RECORD", "记录格式不正确。")), 400
        return jsonify(service.save_consultation(record))

    @app.get("/api/consultations/<conversation_id>")
    def consultation_history(conversation_id: str) -> object:
        return jsonify(service.consultation(conversation_id))

    @app.get("/api/consultations/<conversation_id>/latest")
    def consultation_latest(conversation_id: str) -> object:
        return jsonify(service.consultation(conversation_id, latest=True))

    @app.get("/api/hospitals")
    def hospitals() -> object:
        result: dict[int, dict[str, object]] = {}
        for doctor in service.catalog():
            hospital_id = int(str(doctor["hospital_id"]))
            hospital = result.setdefault(hospital_id, {"id": hospital_id, "name": doctor["hospital_name"], "doctors": []})
            hospital["doctors"].append(doctor)
        return jsonify({"ok": True, "hospitals": list(result.values())})

    @app.get("/api/doctors/<int:doctor_id>/slots")
    def slots(doctor_id: int) -> object:
        return jsonify(service.run({"action": "query", "doctor_id": doctor_id}))

    @app.post("/api/booking/run")
    def booking() -> object:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(failure("INVALID_PAYLOAD", "请求格式不正确，需要提供JSON对象。")), 400
        try:
            return jsonify(service.run(payload))
        except sqlite3.OperationalError:
            return jsonify(failure("RETRYABLE_DB_BUSY", "医院服务暂时繁忙，办理结果还需要核实。请先查询状态；重试时请使用原请求编号。")), 503

    @app.post("/api/patients/session")
    @app.post("/api/demo/session")
    def demo_session() -> object:
        payload = request.get_json(silent=True) or {}
        patient_id = str(payload.get("patient_id", ""))
        if not re.fullmatch(r"(?:PATIENT|DEMO)-[A-Za-z0-9_-]{1,40}", patient_id):
            return jsonify(failure("INVALID_PATIENT", "请提供有效的患者编号，例如PATIENT-A。")), 400
        return jsonify({"ok": True, "patient_token": service.issue_token(patient_id), "expires_in": 3600})

    return app


if __name__ == "__main__":
    state = Path(os.environ.get("MEDICAL_STATE", "/state"))
    state.mkdir(parents=True, exist_ok=True)
    config_path = state / "credentials.json"
    if not config_path.exists():
        config_path.write_text(json.dumps({"api_key": os.environ.get("MEDICAL_SERVICE_API_KEY") or secrets.token_urlsafe(32), "signing_key": secrets.token_urlsafe(32)}), encoding="utf-8")
        config_path.chmod(0o600)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    application = create_app(BookingService(state / "medical.db", config["signing_key"]), config["api_key"])
    # 多进程部署时应使用WSGI服务承载请求。
    from werkzeug.serving import run_simple
    run_simple("0.0.0.0", 8095, application, threaded=True, use_reloader=False)
