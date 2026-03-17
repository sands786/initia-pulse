from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import openai, httpx, json, os, time, hashlib
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="INITIA PULSE API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
INITIA_API = os.getenv("INITIA_API", "https://lcd.testnet.initia.xyz")

PROFILES: Dict[str, dict] = {}
POSTS: List[dict] = []
FOLLOWS: Dict[str, List[str]] = {}
REP_CACHE: Dict[str, dict] = {}

class ProfileIn(BaseModel):
    address: str
    username: str
    display_name: str
    bio: str = ""

class PostIn(BaseModel):
    author: str
    content: str
    post_type: str = "text"
    app_context: str = ""

class FollowIn(BaseModel):
    follower: str
    followee: str

class ReputationRequest(BaseModel):
    address: str
    force_refresh: bool = False

REPUTATION_SYSTEM = """You are the INITIA PULSE reputation engine.
Given a wallet address on-chain activity, return ONLY valid JSON:
{
  "score": <integer 0-1000>,
  "rank": "<Lurker|Newcomer|Active|Builder|Veteran|Legend>",
  "summary": "<2 sentence description>",
  "traits": ["<trait1>", "<trait2>"],
  "weekly_digest": "<1 sentence>",
  "most_active_app": "<app name>"
}"""

@app.get("/health")
async def health():
    return {"status": "ok", "service": "initia-pulse", "posts": len(POSTS), "profiles": len(PROFILES)}

@app.get("/feed")
async def get_feed(limit: int = Query(20, le=100), offset: int = Query(0), filter: str = Query("all")):
    filtered = [p for p in POSTS if filter == "all" or p.get("post_type") == filter]
    filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"posts": filtered[offset:offset+limit], "total": len(filtered), "has_more": offset+limit < len(filtered)}

@app.get("/feed/following/{address}")
async def get_following_feed(address: str, limit: int = 20):
    following = FOLLOWS.get(address, [])
    posts = sorted([p for p in POSTS if p["author"] in following], key=lambda x: x.get("timestamp",""), reverse=True)
    return {"posts": posts[:limit], "following_count": len(following)}

@app.post("/profile")
async def create_profile(data: ProfileIn):
    PROFILES[data.address] = {"address": data.address, "username": data.username, "display_name": data.display_name, "bio": data.bio, "joined_at": datetime.now(timezone.utc).isoformat(), "post_count": 0}
    return {"success": True, "profile": PROFILES[data.address]}

@app.get("/profile/{address}")
async def get_profile(address: str):
    if address not in PROFILES:
        raise HTTPException(404, "Profile not found")
    profile = PROFILES[address].copy()
    profile["reputation"] = REP_CACHE.get(address, {})
    profile["follower_count"] = len([v for v in FOLLOWS.values() if address in v])
    profile["following_count"] = len(FOLLOWS.get(address, []))
    return profile

@app.get("/resolve/{username}")
async def resolve(username: str):
    try:
        name = username.replace(".init", "")
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.get(f"{INITIA_API}/initia/usernames/v1/address/{name}")
            return r.json()
    except:
        raise HTTPException(404, f"Could not resolve {username}")

@app.post("/post")
async def create_post(data: PostIn):
    if len(data.content) > 280:
        raise HTTPException(400, "Post too long")
    post = {"id": hashlib.md5(f"{data.author}{time.time()}".encode()).hexdigest()[:12], "author": data.author, "content": data.content, "post_type": data.post_type, "app_context": data.app_context, "timestamp": datetime.now(timezone.utc).isoformat(), "likes": 0}
    POSTS.append(post)
    if data.author in PROFILES:
        PROFILES[data.author]["post_count"] = PROFILES[data.author].get("post_count", 0) + 1
    return {"success": True, "post": post}

@app.post("/follow")
async def follow(data: FollowIn):
    if data.follower not in FOLLOWS:
        FOLLOWS[data.follower] = []
    if data.followee not in FOLLOWS[data.follower]:
        FOLLOWS[data.follower].append(data.followee)
    return {"success": True}

@app.delete("/follow")
async def unfollow(data: FollowIn):
    if data.follower in FOLLOWS and data.followee in FOLLOWS[data.follower]:
        FOLLOWS[data.follower].remove(data.followee)
    return {"success": True}

@app.get("/following/{address}")
async def get_following(address: str):
    return {"following": FOLLOWS.get(address, []), "count": len(FOLLOWS.get(address, []))}

@app.post("/reputation")
async def get_reputation(req: ReputationRequest):
    addr = req.address
    cached = REP_CACHE.get(addr)
    if cached and not req.force_refresh and time.time() - cached.get("_cached_at", 0) < 3600:
        return cached
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            r = await http.get(f"{INITIA_API}/cosmos/tx/v1beta1/txs", params={"events": f"message.sender='{addr}'", "pagination.limit": 20, "order_by": "ORDER_BY_DESC"})
            txs = r.json().get("txs", [])
        balances = []
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r2 = await http.get(f"{INITIA_API}/cosmos/bank/v1beta1/balances/{addr}")
                balances = r2.json().get("balances", [])
        except: pass
        activity = {"address": addr, "total_transactions": len(txs), "transaction_types": list({m.get("@type","").split(".")[-1] for tx in txs for m in tx.get("body",{}).get("messages",[])}), "token_holdings": balances}
        resp = await client.chat.completions.create(model="gpt-4o", messages=[{"role":"system","content":REPUTATION_SYSTEM},{"role":"user","content":f"Analyse:\n{json.dumps(activity)}"}], max_tokens=400, temperature=0.2)
        raw = resp.choices[0].message.content or "{}"
        rep = json.loads(raw.replace("```json","").replace("```","").strip())
    except Exception as e:
        rep = {"score": min(50, len(POSTS)*5), "rank": "Newcomer", "summary": f"Wallet on Initia Pulse.", "traits": ["On-Chain User"], "weekly_digest": "Active on Initia.", "most_active_app": "Initia L1"}
    rep["address"] = addr
    rep["computed_at"] = datetime.now(timezone.utc).isoformat()
    rep["_cached_at"] = time.time()
    REP_CACHE[addr] = rep
    return rep

@app.get("/reputation/{address}")
async def get_reputation_get(address: str):
    cached = REP_CACHE.get(address)
    if cached: return cached
    raise HTTPException(404, "Not computed yet. POST /reputation first.")

@app.get("/balance/{address}")
async def balance(address: str):
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"{INITIA_API}/cosmos/bank/v1beta1/balances/{address}")
            return {"address": address, "balances": r.json().get("balances", [])}
    except: return {"address": address, "balances": []}

@app.get("/activity/{address}")
async def activity(address: str, limit: int = 10):
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            r = await http.get(f"{INITIA_API}/cosmos/tx/v1beta1/txs", params={"events": f"message.sender='{address}'", "pagination.limit": limit, "order_by": "ORDER_BY_DESC"})
            txs = r.json().get("txs", [])
        return {"address": address, "transactions": [{"type": m.get("@type","").split(".")[-1], "hash": tx.get("txhash",""), "timestamp": tx.get("timestamp","")} for tx in txs for m in tx.get("body",{}).get("messages",[])], "count": len(txs)}
    except: return {"address": address, "transactions": [], "count": 0}

@app.get("/leaderboard")
async def leaderboard(limit: int = 10):
    scored = sorted([{"address": a, **r} for a, r in REP_CACHE.items() if "score" in r], key=lambda x: x.get("score",0), reverse=True)
    return {"leaderboard": scored[:limit]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
