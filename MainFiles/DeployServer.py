import json
import time
import os
import requests
import threading
from pymongo import MongoClient, errors
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ======================
# MongoDB Setup (Safe)
# ======================
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")
MONGO_HOST = os.getenv("MONGO_HOST")
MONGO_DBNAME = os.getenv("MONGO_DBNAME")

MONGO_URI = f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}/{MONGO_DBNAME}?retryWrites=true&w=majority&tls=true"

pairings_col = None
messages_col = None

try:
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,  # fail fast
        tls=True,
        tlsAllowInvalidCertificates=False
    )
    db = client[MONGO_DBNAME]
    # Test connection
    client.admin.command("ping")
    pairings_col = db["pairings"]
    messages_col = db["messages"]
    print("✅ Connected to MongoDB Atlas")
except errors.ServerSelectionTimeoutError as e:
    print("⚠️ MongoDB connection failed:", e)

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

                if pairings_col:
                    try:
                        pairings_col.update_one(
                            {"code": code},
                            {"$set": {
                                "receiver_addr": addr,
                                "active": True,
                                "last_updated": time.time()
                            }},
                            upsert=True
                        )
                    except Exception as e:
                        print("⚠️ Mongo update failed:", e)

            # Sender connects
            elif msg.get("role") == "sender":
                code = msg["code"]
                if code in pairings:
                    sender_links[ws] = pairings[code]
                    print(f"🔗 Sender linked to receiver {code}")
                    await ws.send_json({"status": "linked", "code": code})

                    if pairings_col:
                        try:
                            pairings_col.update_one(
                                {"code": code},
                                {"$push": {"senders": {"addr": addr, "time": time.time()}}},
                                upsert=True
                            )
                        except Exception as e:
                            print("⚠️ Mongo update failed:", e)
                else:
                    await ws.send_json({"error": "Invalid code"})

            # Relay messages
            else:
                direction = "unknown"
                if ws in sender_links:   # sender -> receiver
                    target = sender_links[ws]
                    await target.send_text(json.dumps(msg))
                    direction = "sender->receiver"

                elif ws in pairings.values():  # receiver -> sender(s)
                    for s, r in sender_links.items():
                        if r == ws:
                            await s.send_text(json.dumps(msg))
                    direction = "receiver->sender"

                if messages_col:
                    try:
                        messages_col.insert_one({
                            "direction": direction,
                            "message": msg,
                            "timestamp": time.time(),
                            "from_addr": addr
                        })
                    except Exception as e:
                        print("⚠️ Mongo insert failed:", e)

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
                    if pairings_col:
                        try:
                            pairings_col.update_one({"code": code}, {"$set": {"active": False}})
                        except Exception as e:
                            print("⚠️ Mongo update failed:", e)
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

# ======================
# Run
# ======================
if __name__ == "__main__":
    uvicorn.run("DeployServer:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
