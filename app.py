import os
import re
import requests
import logging
import traceback
import time
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")

# 推薦使用最新且高性價比的 gemini-2.5-flash
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

LANG_CONFIG = {
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "name": "德文"},
    "es": {"google": "es", "deepl": "ES", "name": "西班牙文"}
}

DOMAIN_PROMPTS = {
    "general": "你是一位精準的翻譯專家。",
    "travel": "你是一位專業旅遊翻譯員。請使用常用的旅遊、觀光、餐飲、交通等相關詞彙。",
    "baseball": """你是一位專業棒球轉播翻譯員。你必須嚴格遵守以下術語對照表，不得使用非棒球詞彙。
術語對照表：
- pitch = 投球 / 投出的球 | strike = 好球 | ball = 壞球 | home run = 全壘打 | RBI = 打點 | ERA = 防禦率 | bullpen = 牛棚 | closer = 終結者 | sinker = 伸卡球 | fastball = 快速球 | curveball = 曲球 | slider = 滑球 | changeup = 變速球 | walk = 保送 | strikeout = 三振 | double play = 雙殺 | batting average = 打擊率 | on-base percentage = 上壘率 | slugging percentage = 長打率 | pitcher = 投手 | batter = 打者 | catcher = 捕手 | infield = 內野 | outfield = 外野 | dugout = 休息區 | mound = 投手丘 | plate = 本壘板 | count = 球數 | full count = 滿球數 | swing = 揮棒 | miss = 揮空 | location = 進壘點 | put away = 解決（打者） | back him up = 掩護 / 支援 | payoff = 滿球數""",
    "basketball": "你是一位NBA專業翻譯員。翻譯規則：Rebound = 籃板、Assist = 助攻、Turnover = 失誤、Fast Break = 快攻、Paint = 禁區、Steal = 抄截、Block = 阻攻、Air Ball = 籃外空心。",
    "gaming": "你是一位遊戲翻譯專家。請使用遊戲圈常見術語：HP = 生命值、MP = 法力值、XP = 經驗值、NPC = 非玩家角色、PvP = 玩家對戰、PvE = 玩家對環境、Boss = 頭目、Respawn = 重生、Lag = 延遲。",
    "news": "你是一位新聞編譯。請使用客觀、中立、正式的新聞用語，避免口語。",
    "entertainment": "你是一位娛樂圈翻譯。請使用演藝圈、影視、綜藝等常用詞彙。",
    "mechanical": "你是機械工程翻譯專家。使用機械專業術語：CNC = CNC加工、Bearing = 軸承、Torque = 扭矩、Tolerance = 公差、Fixture = 治具、Gear = 齒輪、Shaft = 軸。",
    "semiconductor": "你是半導體工程師。翻譯時保持專業術語：Wafer = 晶圓、Yield = 良率、Packaging = 封裝、Fab = 晶圓廠、Process Node = 製程節點、Tape-out = 投片、Die = 晶粒、Etching = 蝕刻。",
    "medical": "你是一位醫療翻譯專家。使用標準醫學術語，確保準確性。",
    "legal": "你是一位法律翻譯員。使用法律專業辭彙，確保條文精確。",
    "it": "你是一位資訊科技翻譯專家。使用 IT 業界常用詞彙：API = 應用程式介面、Database = 資料庫、Server = 伺服器、Frontend = 前端、Backend = 後端。",
    "finance": "你是一位金融翻譯員。使用金融專業詞彙：Equity = 股權、Bond = 債券、Derivative = 衍生品、Hedge = 避險、Dividend = 股息。"
}

def get_domain_prompt(domain):
    return DOMAIN_PROMPTS.get(domain, "你是一位精準的翻譯專家。")

def translate_google_with_retry(text, src, tgt, retries=3, delay=1):
    for i in range(retries):
        try:
            src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
            tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                return "".join(part[0] for part in data[0] if part[0]) or text
        except Exception as e:
            app.logger.warning(f"Google 翻譯嘗試 {i+1} 異常: {e}")
        if i < retries - 1:
            time.sleep(delay)
    raise Exception("Google 翻譯重試失敗")

def translate_google(text, src, tgt):
    return translate_google_with_retry(text, src, tgt)

def translate_deepl(text, src, tgt, api_key):
    if not api_key:
        raise ValueError("DeepL API Key 未設定")
    tgt_code = LANG_CONFIG[tgt]["deepl"]
    src_code = LANG_CONFIG[src]["deepl"]
    if src_code == "ZH-HANT":
        src_code = "ZH"
    params = {"text": text, "target_lang": tgt_code}
    if src_code and src_code != "auto":
        params["source_lang"] = src_code
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    resp = requests.post("https://api-free.deepl.com/v2/translate", data=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"]


# ================== 最佳化後的 Gemini 單句翻譯 ==================
def translate_gemini(text, src, tgt, api_key, domain="general"):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
        
    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    domain_prompt = get_domain_prompt(domain)
    
    # 移除動態獲取清單，直接調用終端點，並引入 systemInstruction 規範模型行為
    url = f"https://generativelanguage.googleapis.com/v1/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    
    system_instruction = f"{domain_prompt}\n你唯一的任務是將輸入的 {src_name} 精準翻譯成 {tgt_name}。請保持口吻自然。絕對不要輸出任何解釋、說明或引號，只輸出翻譯後的文字結果。"
    
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "temperature": 0.3, 
            "maxOutputTokens": 1000
        }
    }
    
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 錯誤 {resp.status_code}: {resp.text}")
        
    data = resp.json()
    try:
        result = data["candidates"][0]["content"]["parts"][0]["text"]
        return result.strip()
    except (KeyError, IndexError):
        raise Exception("無法解析 Gemini 回應，結構可能被拒絕或封鎖")


# ================== 全新打造：Gemini SRT 批次翻譯 ==================
def translate_gemini_batch(subtitles, src, tgt, api_key, domain="general"):
    """
    將所有字幕打包成 JSON 一次性交給 Gemini 翻譯，大幅提高效率並保留上下文。
    """
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
        
    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    domain_prompt = get_domain_prompt(domain)
    
    url = f"https://generativelanguage.googleapis.com/v1/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    
    # 利用 systemInstruction 強制要求回傳純 JSON
    system_instruction = (
        f"{domain_prompt}\n"
        f"你是一位專業影片字幕翻譯師。請將使用者提供的 JSON 陣列中的 'text' 字幕從 {src_name} 翻譯成 {tgt_name}。\n"
        f"【嚴格規則】\n"
        f"1. 請結合前後文脈絡，確保語意連貫、流暢且符合目標語言習慣。\n"
        f"2. 必須保持回傳的 JSON 陣列長度與順序和輸入完全一致。\n"
        f"3. 輸出格式必須是純 JSON 陣列，例如: [{{'id': 1, 'translated': '...'}}, {{'id': 2, 'translated': '...'}}]\n"
        f"4. 不要包含任何 markdown 標籤（如 ```json），不要包含任何前言或後記。"
    )
    
    # 準備包裝給大模型的 input 資料
    input_list = [{"id": idx, "text": sub["text"]} for idx, sub in enumerate(subtitles)]
    user_content = json.dumps(input_list, ensure_ascii=False)
    
    payload = {
        "contents": [{"parts": [{"text": user_content}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"  # 強制模型回應 JSON 格式
        }
    }
    
    resp = requests.post(url, json=payload, timeout=60) # 批次處理給予較長超時
    if resp.status_code != 200:
        raise Exception(f"Gemini Batch API 錯誤 {resp.status_code}")
        
    data = resp.json()
    try:
        raw_reply = data["candidates"][0]["content"]["parts"][0]["text"]
        translated_results = json.loads(raw_reply)
        
        # 將翻譯好的結果對應回原本的 subtitles 結構
        # 預防模型回傳的 id 順序錯亂，建立字典比對
        reply_dict = {item["id"]: item["translated"] for item in translated_results if "id" in item and "translated" in item}
        
        for idx, sub in enumerate(subtitles):
            sub["translated"] = reply_dict.get(idx, "")
            
        return subtitles
    except Exception as e:
        raise Exception(f"批次解析 Gemini JSON 失敗: {e}")


def parse_srt(content):
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\d+\n|\n*$)'
    blocks = re.findall(pattern, content, re.DOTALL)
    subtitles = []
    for block in blocks:
        _, start_str, end_str, text = block
        def to_seconds(t):
            h, m, s = t.split(':')
            s, ms = s.split(',')
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
        start = to_seconds(start_str)
        end = to_seconds(end_str)
        text = text.replace('\n', ' ').strip()
        if text:
            subtitles.append({'start': start, 'end': end, 'text': text})
    return subtitles

def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

@app.route("/translate", methods=["POST"])
def translate():
    try:
        data = request.get_json()
        text = data.get("text", "").strip()
        src = data.get("source_lang", "zh-TW")
        tgt = data.get("target_lang", "en")
        gemini_key = data.get("gemini_key", "") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key", "") or ENV_DEEPL_KEY
        domain = data.get("domain", "general")

        if not text:
            return jsonify({"error": "請輸入文字"}), 400
        if src == tgt:
            return jsonify({"result": text, "engine": "相同語言"})

        if gemini_key:
            try:
                result = translate_gemini(text, src, tgt, gemini_key, domain)
                return jsonify({"result": result, "engine": "Gemini AI"})
            except Exception as e:
                app.logger.error(f"Gemini 失败: {e}")

        if tgt in ["en", "fr", "de", "es"]:
            result = translate_google(text, src, tgt)
            engine = "Google 翻譯"
        elif tgt in ["ja", "ko"] and deepl_key:
            try:
                result = translate_deepl(text, src, tgt, deepl_key)
                engine = "DeepL 翻譯"
            except:
                result = translate_google(text, src, tgt)
                engine = "Google 翻譯 (DeepL降級)"
        else:
            result = translate_google(text, src, tgt)
            engine = "Google 翻譯"
        return jsonify({"result": result, "engine": engine})
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ================== 優化後的 SRT 批次翻譯路由 ==================
@app.route('/translate_srt', methods=['POST'])
def translate_srt():
    try:
        data = request.get_json()
        srt_content = data.get('srt_content', '').strip()
        gemini_key = data.get('gemini_key', '') or ENV_GEMINI_KEY
        target_lang = data.get('target_lang', 'zh-TW')
        original_first = data.get('original_first', True)
        source_lang = data.get('source_lang', 'auto')
        domain = data.get('domain', 'general')

        if not srt_content:
            return jsonify({"error": "请贴上 SRT 内容"}), 400

        subtitles = parse_srt(srt_content)
        if not subtitles:
            return jsonify({"error": "无法解析 SRT 格式"}), 400

        translated_subs = []
        engine = "Google 翻譯"

        # 核心改動：如果啟用了 Gemini，直接走批次翻譯
        if gemini_key:
            try:
                # 呼叫批次翻譯，1次請求搞定所有字幕
                processed_subs = translate_gemini_batch(subtitles, source_lang, target_lang, gemini_key, domain)
                
                for sub in processed_subs:
                    translated_subs.append({
                        'start': sub['start'],
                        'end': sub['end'],
                        'original': sub['text'],
                        'translated': sub.get('translated', f"[翻譯失敗] {sub['text']}")
                    })
                engine = "Gemini AI (批次高速)"
            except Exception as e:
                app.logger.error(f"Gemini 批次翻譯失敗，降級至 Google 逐條翻譯: {e}")
                gemini_key = None # 觸發下方降級邏輯

        # 降級或備用方案（Google 逐條翻譯維持原樣）
        if not gemini_key:
            for idx, sub in enumerate(subtitles, 1):
                original = sub['text']
                if not original.strip():
                    translated = ""
                else:
                    try:
                        translated = translate_google(original, source_lang, target_lang)
                    except Exception as e:
                        app.logger.error(f"Google 第{idx}条失败: {e}")
                        translated = f"[未翻译] {original}"
                        
                translated_subs.append({
                    'start': sub['start'],
                    'end': sub['end'],
                    'original': original,
                    'translated': translated
                })
            engine = "Google 翻譯 (Gemini 失敗降級)"

        # 重建 SRT 文字輸出
        srt_output = ""
        for i, sub in enumerate(translated_subs, 1):
            start_str = format_srt_time(sub['start'])
            end_str = format_srt_time(sub['end'])
            if original_first:
                text = f"{sub['original']}\n{sub['translated']}"
            else:
                text = f"{sub['translated']}\n{sub['original']}"
            srt_output += f"{i}\n{start_str} --> {end_str}\n{text}\n\n"

        return jsonify({
            "srt_output": srt_output,
            "count": len(translated_subs),
            "engine": engine
        })
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"error": f"SRT 处理失败: {str(e)}"}), 500

@app.route("/")
def index():
    return send_from_directory('.', 'index.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

