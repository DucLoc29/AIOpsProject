"""Corporate API (tầng ứng dụng) - Flask + PostgreSQL + Prometheus metrics.

Chạy trên app-server (192.168.10.20:5000). Nginx proxy /api/ tới đây.
"""
import os
import time

import psycopg2
from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

DB = dict(
    host=os.getenv("DB_HOST", "192.168.10.21"),
    port=int(os.getenv("DB_PORT", "5432")),
    dbname=os.getenv("DB_NAME", "corp"),
    user=os.getenv("DB_USER", "corp"),
    password=os.getenv("DB_PASSWORD", "corp_pass"),
    connect_timeout=3,
)

app = Flask(__name__)

REQS = Counter("api_requests_total", "Tổng số request", ["endpoint", "status"])
LAT = Histogram("api_request_duration_seconds", "Độ trễ request", ["endpoint"])
DB_ERR = Counter("api_db_errors_total", "Số lỗi kết nối/truy vấn DB")


def query(sql, params=(), fetch=True):
    conn = psycopg2.connect(**DB)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if fetch else None
    finally:
        conn.close()


def instrumented(name):
    def deco(fn):
        def wrapper(*a, **kw):
            start = time.time()
            status = 200
            try:
                resp = fn(*a, **kw)
                if isinstance(resp, tuple):
                    status = resp[1]
                return resp
            except psycopg2.Error:
                DB_ERR.inc()
                status = 503
                return jsonify(error="database unavailable"), 503
            finally:
                LAT.labels(name).observe(time.time() - start)
                REQS.labels(name, str(status)).inc()
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


@app.route("/api/health")
def health():
    return jsonify(status="ok")


@app.route("/api/employees")
@instrumented("employees")
def employees():
    rows = query("SELECT id, name, department FROM employees ORDER BY id LIMIT 50")
    return jsonify([dict(id=r[0], name=r[1], department=r[2]) for r in rows])


@app.route("/api/search")
@instrumented("search")
def search():
    q = request.args.get("q", "")
    rows = query(
        "SELECT id, name, department FROM employees WHERE name ILIKE %s LIMIT 20",
        (f"%{q}%",),
    )
    return jsonify([dict(id=r[0], name=r[1], department=r[2]) for r in rows])


@app.route("/api/login", methods=["POST"])
@instrumented("login")
def login():
    data = request.get_json(silent=True) or {}
    rows = query(
        "SELECT 1 FROM users WHERE username=%s AND password=%s",
        (data.get("username", ""), data.get("password", "")),
    )
    if rows:
        return jsonify(status="ok")
    return jsonify(error="invalid credentials"), 401


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
