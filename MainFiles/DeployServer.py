import socket
import threading
import json
import time
import os
import requests
from pymongo import MongoClient
from fastapi import FastAPI
import uvicorn

# ======================
# MongoDB Setup (from env vars)
# ======================
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")
MONGO_HOST = os.getenv("MONGO_HOST")
MONGO_DBNAME = os.getenv("MONGO_DBNAME")

MONGO_URI = f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}/?retryWrites=true&w=majority&appName=StemDeck"
client = MongoClient(MONGO_URI)
db = client[MONGO_DBNAME]

pairings_col = db["pairings"]
messages_col = db["messages"]

# ======================
# In-Memory Structures
# ======================
pairings = {}       # pairing_code -> receiver socket
sender_links = {}   # sender socket -> receiver socket

# ======================
# TCP Client Handler
# ======================
def client_handler(conn, addr):
    print(f"✅ New connection from {addr}")
    try:
        while True:
            data = conn.recv(4096).decode()

            if not data:
                # client closed connection
                print(f"👋 Client {addr} disconnected")
                break

            # ✅ Validate JSON safely
            try:
                if not data.strip():
                    continue  # ignore empty packets
                msg = json.loads(data)
            except json.JSONDecodeError:
                print(f"⚠️ Ignored invalid JSON from {addr}: {data!r}")
                continue

            # =====================
            # Handle roles/messages
            # =====================
            if msg.get("role") == "receiver":
                code = msg["code"]
                pairings[code] = conn
                print(f"📌 Receiver registered with code {code}")

                pairings_col.update_one(
                    {"code": code},
                    {"$set": {
                        "receiver_addr": str(addr),
                        "active": True,
                        "last_updated": time.time()
                    }},
                    upsert=True
                )

            elif msg.get("role") == "sender":
                code = msg["code"]
                if code in pairings:
                    sender_links[conn] = pairings[code]
                    print(f"🔗 Sender linked to receiver {code}")
                    conn.send(json.dumps({"status": "linked", "code": code}).encode())

                    pairings_col.update_one(
                        {"code": code},
                        {"$push": {"senders": {"addr": str(addr), "time": time.time()}}},
                        upsert=True
                    )
                else:
                    conn.send(json.dumps({"error": "Invalid code"}).encode())

            else:  # Relay
                if conn in sender_links:
                    target = sender_links[conn]
                    direction = "sender->receiver"
                elif conn in pairings.values():
                    target = [s for s, r in sender_links.items() if r == conn]
                    direction = "receiver->sender"
                else:
                    target = None
                    direction = "unknown"

                if target:
                    try:
                        encoded = json.dumps(msg).encode()
                        if isinstance(target, list):
                            for t in target:
                                t.send(encoded)
                        else:
                            target.send(encoded)

                        # log to DB quietly
                        messages_col.insert_one({
                            "direction": direction,
                            "message": msg,
                            "timestamp": time.time(),
                            "from_addr": str(addr)
                        })
                    except Exception as e:
                        print(f"⚠️ Relay failed for {addr}: {e}")

    except Exception as e:
        print(f"❌ Unexpected error for {addr}: {e}")

    finally:
        conn.close()
        # cleanup
        if conn in sender_links:
            del sender_links[conn]
        else:
            for code, r in list(pairings.items()):
                if r == conn:
                    del pairings[code]
                    pairings_col.update_one({"code": code}, {"$set": {"active": False}})
        print(f"🧹 Cleaned up {addr}")

# ======================
# TCP Server
# ======================
def start_tcp_server(host="0.0.0.0", port=9000, max_clients=50):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(max_clients)
    print(f"🚀 TCP Server listening on {host}:{port} (max {max_clients} clients)")

    while True:
        conn, addr = server.accept()
        threading.Thread(target=client_handler, args=(conn, addr), daemon=True).start()

# ======================
# FastAPI App
# ======================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "FastAPI TCP Relay running"}

@app.get("/ping")
def ping():
    return {"alive": True, "timestamp": time.time()}

@app.get("/status")
def status():
    return {
        "active_pairings": list(pairings.keys()),
        "senders_count": len(sender_links),
        "receivers_count": len(pairings),
    }

# ======================
# Startup Background Tasks
# ======================
def keep_alive():
    url = os.getenv("KEEPALIVE_URL", "https://steamdeck.onrender.com/ping")
    while True:
        try:
            requests.get(url, timeout=5)
            print("🔄 Self-ping successful")
        except Exception as e:
            print("⚠️ Keep-alive ping failed:", e)
        time.sleep(300)  # 5 min

@app.on_event("startup")
def startup_event():
    # Start TCP server thread
    threading.Thread(target=start_tcp_server, daemon=True).start()
    # Start keep-alive thread
    threading.Thread(target=keep_alive, daemon=True).start()
