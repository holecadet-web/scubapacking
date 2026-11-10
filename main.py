from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
WWW_DIR = BASE_DIR / "www"
PONGDANG_JSON_PATH = BASE_DIR / "pongdang_scuba_db.json"
CUSTOM_DB_PATH = BASE_DIR / "custom_pongdang_db.json"
TOURS_DATA_PATH = BASE_DIR / "tours_data.json"

app = FastAPI(
    title="Scuba Diving Tour Packing Manager (FastAPI)",
    description="스쿠버 다이빙 투어 패킹 매니저 & 퐁당닷컴 6,448종 장비 DB + Gemini AI 해황/이동편 분석 API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory cache of Pongdang DB
_PONGDANG_ITEMS: List[Dict[str, Any]] = []


def load_pongdang_db() -> List[Dict[str, Any]]:
    global _PONGDANG_ITEMS
    if not _PONGDANG_ITEMS and PONGDANG_JSON_PATH.exists():
        with open(PONGDANG_JSON_PATH, "r", encoding="utf-8-sig") as f:
            _PONGDANG_ITEMS = json.load(f)
    custom_items: List[Dict[str, Any]] = []
    if CUSTOM_DB_PATH.exists():
        try:
            with open(CUSTOM_DB_PATH, "r", encoding="utf-8") as f:
                custom_items = json.load(f)
        except Exception:
            custom_items = []
    return custom_items + _PONGDANG_ITEMS


class NewDbEquipmentRequest(BaseModel):
    brand: str
    mainCat: str = "스쿠버"
    subCat: str = "신규 장비"
    appCat: str = "핵심 장비"
    name: str
    weight: float = 0.5
    price: int = 0
    bag: str = "위탁"
    fixed: bool = False
    thumb: str = ""


class TourSaveRequest(BaseModel):
    tours: List[Dict[str, Any]]


class AiBriefingRequest(BaseModel):
    place: str
    dates: str
    apiKey: Optional[str] = None


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    db = load_pongdang_db()
    return {
        "status": "ok",
        "engine": "FastAPI",
        "version": "2.0.0",
        "db_total": len(db),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/pongdang/search")
async def search_pongdang(
    q: str = Query("", description="검색어 (브랜드, 장비명, 분류)"),
    main_cat: str = Query("전체", description="전체 | 스쿠버 | 스쿠버 acc | 직접등록"),
    sub_cat: str = Query("전체", description="장비 종류 필터"),
    brand: str = Query("전체", description="브랜드 필터"),
    sort: str = Query("default", description="default | brand | subcat | price_asc | price_desc | weight_asc | weight_desc"),
    limit: int = Query(80, ge=1, le=500),
) -> Dict[str, Any]:
    db = load_pongdang_db()
    keywords = [k.lower() for k in q.strip().split() if k.strip()]

    filtered: List[Dict[str, Any]] = []
    for item in db:
        is_custom = bool(item.get("isCustom"))
        if main_cat == "직접등록" and not is_custom:
            continue
        elif main_cat not in ("전체", "직접등록") and item.get("pongCat1") != main_cat:
            continue

        if sub_cat != "전체" and (item.get("pongCat2") or "기타") != sub_cat:
            continue
        if brand != "전체" and (item.get("brand") or "기타") != brand:
            continue

        if keywords:
            hay = f"{item.get('fullName', '')} {item.get('brand', '')} {item.get('pongCat2', '')} {item.get('appCat', '')}".lower()
            if not all(k in hay for k in keywords):
                continue

        filtered.append(item)

    if sort == "brand":
        filtered.sort(key=lambda x: (x.get("brand") or "", x.get("fullName") or ""))
    elif sort == "subcat":
        filtered.sort(key=lambda x: (x.get("pongCat2") or "", x.get("fullName") or ""))
    elif sort == "price_asc":
        filtered.sort(key=lambda x: int(x.get("price") or 0))
    elif sort == "price_desc":
        filtered.sort(key=lambda x: int(x.get("price") or 0), reverse=True)
    elif sort == "weight_asc":
        filtered.sort(key=lambda x: float(x.get("weight") or 0.0))
    elif sort == "weight_desc":
        filtered.sort(key=lambda x: float(x.get("weight") or 0.0), reverse=True)

    return {
        "total": len(filtered),
        "items": filtered[:limit],
    }


@app.post("/api/pongdang/items")
async def add_custom_pongdang_item(req: NewDbEquipmentRequest) -> Dict[str, Any]:
    custom_items: List[Dict[str, Any]] = []
    if CUSTOM_DB_PATH.exists():
        try:
            with open(CUSTOM_DB_PATH, "r", encoding="utf-8") as f:
                custom_items = json.load(f)
        except Exception:
            custom_items = []

    brand = req.brand.strip() or "기타"
    clean_name = req.name.strip()
    full_name = clean_name if clean_name.startswith("[") else f"[{brand}] {clean_name}"

    new_item = {
        "id": f"custom_{int(datetime.now().timestamp() * 1000)}",
        "brand": brand,
        "name": clean_name,
        "fullName": full_name,
        "pongCat1": req.mainCat,
        "pongCat2": req.subCat.strip() or "신규 장비",
        "pongCat3": "",
        "appCat": req.appCat,
        "weight": round(float(req.weight), 2),
        "bag": req.bag,
        "fixed": req.fixed,
        "price": int(req.price),
        "thumb": req.thumb.strip(),
        "isCustom": True,
    }
    custom_items.insert(0, new_item)
    with open(CUSTOM_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(custom_items, f, ensure_ascii=False, indent=2)

    return {"status": "ok", "item": new_item}


@app.get("/api/tours")
async def get_saved_tours() -> Dict[str, Any]:
    if TOURS_DATA_PATH.exists():
        try:
            with open(TOURS_DATA_PATH, "r", encoding="utf-8") as f:
                return {"tours": json.load(f)}
        except Exception:
            pass
    return {"tours": []}


@app.post("/api/tours")
async def save_tours(req: TourSaveRequest) -> Dict[str, Any]:
    with open(TOURS_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(req.tours, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "count": len(req.tours)}


@app.post("/api/ai/briefing")
async def generate_ai_briefing(req: AiBriefingRequest) -> Dict[str, Any]:
    api_key = (req.apiKey or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API Key가 필요합니다.")

    today_str = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""당신은 스쿠버 다이빙 투어 전문 플래너이자 해양 기상 분석가입니다.
현재 날짜: {today_str}
사용자가 입력한 다이빙 투어 장소: "{req.place}"
사용자가 입력한 투어 일정(날짜): "{req.dates}"

입력된 일정이 현재 날짜로부터 가까운 시일(약 10일 이내)이면 해당 지역의 최근 기상/해황 경향을 반영하고, 먼 날짜이거나 연도가 불명확하면 해당 월/시기의 해당 지역 평균 바다 컨디션(평년 해황 데이터)을 기준으로 다이빙에 꼭 필요한 정보를 한국어 JSON 객체로만 응답하세요.

JSON 응답 형식:
{{
  "basisLabel": "예: 11월 중순 오키나와 평년 해황 및 항공편 분석 (또는 단기 기상 경향 반영)",
  "shortWaterTemp": "상단 요약바에 넣을 짧은 수온 표기 (예: 25 ~ 26°C)",
  "shortFlight": "상단 요약바에 넣을 짧은 추천 항공/이동편 요약 (예: 인천-나하 직항 약 2시간 20분)",
  "waterTemp": "표층 및 수심별 예상 수온 (예: 25~26°C / 수심 20m 약 24°C)",
  "airWeather": "기온 범위 및 날씨 특징 (예: 21~26°C, 선선한 바람·출수 후 체온 주의)",
  "waveWind": "예상 파고(m) 및 풍향/풍속 (예: 파고 1.0~1.5m, 북동풍 영향 포인트 선정 중요)",
  "visCurrent": "평균 시야(m) 및 조류 특징 (예: 시야 20~30m, 케라마 제도 약~중조류)",
  "suitGearTip": "수온/파고 맞춤 슈트 두께(웻/드라이/후드베스트/보트코트) 및 필수 안전장비(SMB 등) 추천 2~3문장",
  "transportTip": "한국 출발 추천 항공편/이동 루트(직항 항공사·소요시간·현지 공항에서 다이빙 지역 이동법) 및 마지막 날 비행 전 No-Fly(18~24시간 수면휴식) 스케줄 팁 2~3문장"
}}
반드시 순수 JSON 객체만 출력하세요."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
        raw_text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        return json.loads(raw_text)


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(WWW_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(WWW_DIR), html=True), name="www")
