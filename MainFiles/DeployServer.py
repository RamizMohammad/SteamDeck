# tcp_server.py
import socket
import threading
import json
import time
import os
import requests
from pymongo import MongoClient
from flask import Flask

# ======================
# MongoDB Setup (from environment variables)
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
# Client Handler
# ======================
def client_handler(conn, addr):
    print(f"✅ Connection from {addr}")

    try:
        while True:
            data = conn.recv(4096).decode()
            if not data:
                break
            msg = json.loads(data)

            # Receiver registers with code
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

            # Sender links to receiver
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

            # Relay
            else:
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
                    if isinstance(target, list):
                        for t in target:
                            t.send(json.dumps(msg).encode())
                    else:
                        target.send(json.dumps(msg).encode())

                    messages_col.insert_one({
                        "direction": direction,
                        "message": msg,
                        "timestamp": time.time(),
                        "from_addr": str(addr)
                    })

    except Exception as e:
        print("❌ Error:", e)
    finally:
        conn.close()
        if conn in sender_links:
            del sender_links[conn]
        else:
            for code, r in list(pairings.items()):
                if r == conn:
                    del pairings[code]
                    pairings_col.update_one({"code": code}, {"$set": {"active": False}})

# ======================
# TCP Server Start
# ======================
def start_tcp_server(host="0.0.0.0", port=10000, max_clients=50):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(max_clients)
    print(f"🚀 TCP Server listening on {host}:{port} (max {max_clients} clients)")

    while True:
        conn, addr = server.accept()
        threading.Thread(target=client_handler, args=(conn, addr), daemon=True).start()

# ======================
# Flask Keep-Alive Web Server
# ======================
app = Flask(__name__)

@app.route("/")
def home():
    return {"status": "ok", "message": "TCP server is running"}

@app.route("/ping")
def ping():
    return {"alive": True, "timestamp": time.time()}

# ======================
# Self-Caller Thread
# ======================
def keep_alive():
    url = os.getenv("KEEPALIVE_URL", "https://steamdeck.onrender.com/ping")
    while True:
        try:
            requests.get(url, timeout=5)
            print("🔄 Self-ping successful")
        except Exception as e:
            print("⚠️ Keep-alive ping failed:", e)
        time.sleep(300)  # every 5 minutes

# ======================
# Run Everything
# ======================
if __name__ == "__main__":
    # Start TCP server in background
    threading.Thread(target=start_tcp_server, daemon=True).start()

    # Start keep-alive thread
    threading.Thread(target=keep_alive, daemon=True).start()

    # Run Flask (Render exposes this HTTP server)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))