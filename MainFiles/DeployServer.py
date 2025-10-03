import json
import time
import os
import requests
import threading
from pymongo import MongoClient
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ======================
# MongoDB Setup
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
pairings = {}       # code -> receiver WebSocket
sender_links = {}   # sender WebSocket -> receiver WebSocket

# ======================
# FastAPI App
# ======================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "FastAPI WebSocket Relay running"}

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
# WebSocket Endpoint
# ======================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    addr = ws.client.host
    print(f"✅ WebSocket connected: {addr}")

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            # Receiver registers
            if msg.get("role") == "receiver":
                code = msg["code"]
                pairings[code] = ws
                print(f"📌 Receiver registered with code {code}")

                pairings_col.update_one(
                    {"code": code},
                    {"$set": {
                        "receiver_addr": addr,
                        "active": True,
                        "last_updated": time.time()
                    }},
                    upsert=True
                )

            # Sender connects
            elif msg.get("role") == "sender":
                code = msg["code"]
                if code in pairings:
                    sender_links[ws] = pairings[code]
                    print(f"🔗 Sender linked to receiver {code}")
                    await ws.send_json({"status": "linked", "code": code})

                    pairings_col.update_one(
                        {"code": code},
                        {"$push": {"senders": {"addr": addr, "time": time.time()}}},
                        upsert=True
                    )
                else:
                    await ws.send_json({"error": "Invalid code"})

            # Relay messages
            else:
                if ws in sender_links:   # sender -> receiver
                    target = sender_links[ws]
                    await target.send_text(json.dumps(msg))
                    direction = "sender->receiver"

                elif ws in pairings.values():  # receiver -> sender(s)
                    for s, r in sender_links.items():
                        if r == ws:
                            await s.send_text(json.dumps(msg))
                    direction = "receiver->sender"

                else:
                    direction = "unknown"

                # Log message
                messages_col.insert_one({
                    "direction": direction,
                    "message": msg,
                    "timestamp": time.time(),
                    "from_addr": addr
                })

    except WebSocketDisconnect:
        print(f"❌ WebSocket disconnected: {addr}")
    finally:
        # cleanup
        if ws in sender_links:
            del sender_links[ws]
        else:
            for code, r in list(pairings.items()):
                if r == ws:
                    del pairings[code]
                    pairings_col.update_one({"code": code}, {"$set": {"active": False}})
        print(f"🧹 Cleaned up {addr}")

# ======================
# Keep-Alive Thread
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
    threading.Thread(target=keep_alive, daemon=True).start()
