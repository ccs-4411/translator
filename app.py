import os
import re
import requests
import logging
import traceback
import time
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# 🔥 自動取得目前 app.py 所在的絕對路徑資料夾
# 保證在 Render 伺服器上正確讀取同目錄下的 index.html
base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=base_dir, static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

# 從環境變數讀取金鑰（若前端無帶入則以此為備用）
ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")

# 預設使用通用模型
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# 語系對照配置
LANG_CONFIG = {
    "auto": {"google": "auto", "deepl": "auto", "name": "自動偵測的語言"},
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "name": "德文"},
    "es": {"google": "es", "deepl": "ES", "name": "西班牙文"}
}

# ========== 行業領域設定 ==========
DOMAIN_CONFIG = {
    "general": {
        "role": "你是一位精準、專業的母語級翻譯專家。",
        "rules": ["保持語氣自然流暢，符合目標語言的日常表達與文本習慣。"]
    },
    "travel": {
        "role": "你是一位擁有多年經驗的專業旅遊與觀光雜誌編譯。",
        "rules": ["請使用常用的旅遊、觀光、餐飲、航空及交通等相關專業詞彙。", "口吻要生動且吸引人。"]
    },
    "baseball": {
        "role": "你是一位專業的體育記者與棒球轉播翻譯員。你精通棒球各項戰術、紀錄與術語。",
        "rules": ["必須嚴格遵守下方的【術語對照表】，不得使用非棒球圈的通用詞彙。", "語氣要帶有體育賽事的動態感與現場感。"],
        "glossary": """
| 英文術語 | 翻譯規範（繁體中文） |
| :--- | :--- |
| pitch | 投球 / 投出的球 |
| strike / strike two | 好球 / 兩好球 |
| ball | 壞球 |
| home run | 全壘打 |
| RBI / ERA | 打點 / 防禦率 |
| bullpen / closer | 牛棚 / 終結者 |
| sinker / fastball / curveball | 伸卡球 / 快速球 / 曲球 |
| slider / changeup | 滑球 / 變速球 |
| walk / strikeout / double play | 保送 / 三振 / 雙殺 |
| batting average / on-base percentage | 打擊率 / 上壘率 |
| slugging percentage | 長打率 |
| pitcher / batter / catcher | 投手 / 打者 / 捕手 |
| infield / outfield | 內野 / 外野 |
| dugout / mound / plate | 休息區 / 投手丘 / 本壘板 |
| count / full count / payoff | 球數 / 滿球數 / 滿球數對決 |
| swing / miss / location | 揮棒 / 揮空 / 進壘點 |
| put away | 解決（打者） |
| back him up | 掩護 / 支援 |
""",
        "examples": """
【雙語對齊範例】
- "backed him up with strike two" ➔ "用兩好球掩護他"
- "put him away with the sinker" ➔ "用伸卡球解決他"
- "full payoff got him swinging" ➔ "滿球數對決使他揮空"
"""
    },
    "basketball": {
        "role": "你是一位專業的籃球評述員與 NBA 賽事專職翻譯。",
        "rules": ["嚴格遵守以下籃球核心術語：Rebound=籃板、Assist=助攻、Turnover=失誤、Fast Break=快攻、Paint=禁區、Steal=抄截、Block=阻攻、Air Ball=籃外空心。"]
    },
    "gaming": {
        "role": "你是一位資深的遊戲在地化（Localization）專家，精通各大 3A 遊戲、RPG 與電競術語。",
        "rules": ["必須嚴格使用玩家遊戲圈的常用習慣詞彙，避免生硬的字面直譯。"],
        "glossary": """
| 術語 | 翻譯規範 |
| :--- | :--- |
| HP / MP / XP | 生命值 / 法力值 / 經驗值 |
| NPC / Boss | 非玩家角色 / 頭目（或王） |
| PvP / PvE | 玩家對戰 / 玩家對環境 |
| Respawn / Lag | 重生 / 延遲（或卡頓） |
"""
    },
    "news": {
        "role": "你是一位國際新聞社的資深編譯。",
        "rules": ["請使用客觀、中立、正式的新聞報導用語，結構嚴謹，完全避免口語化表達。"]
    },
    "entertainment": {
        "role": "你是一位綜藝節目、影視娛樂與演藝圈的資深追星族兼專業翻譯。",
        "rules": ["請使用演藝圈、影視特效、流行網絡梗與綜藝常見的生動詞彙。"]
    },
    "mechanical": {
        "role": "你是一位精密機械工程翻譯專家，擁有工廠實務與論文編譯背景。",
        "rules": ["請使用精確的機械專業工程術語：CNC=CNC加工、Bearing=軸承、Torque=扭矩、Tolerance=公差、Fixture=治具、Gear=齒輪、Shaft=軸。"]
    },
    "semiconductor": {
        "role": "你是一位在晶圓代工大廠工作多年的資深製程工程師與半導體產業編譯。",
        "rules": ["保持業界高度專業口吻。", "當業界習慣直接使用英文簡稱或專有名詞時，請直接保留（例如 Fab、Wafer、Tape-out、Die），不需強行死譯。"],
        "glossary": """
| 專有名詞 | 翻譯規範 |
| :--- | :--- |
| Wafer / Yield | 晶圓 / 良率 |
| Packaging / Fab | 封裝 / 晶圓廠 |
| Process Node | 製程節點 |
| Etching | 蝕刻 |
"""
    },
    "medical": {
        "role": "你是一位醫療與臨床醫學文獻翻譯專家。",
        "rules": ["必須使用標準、公認的醫學術語，確保病理、藥名及臨床表現的絕對準確性。"]
    },
    "legal": {
        "role": "你是一位精通跨國商業合約與訴訟文件的法律翻譯官員。",
        "rules": ["使用極度嚴謹、精確的法律法規專業辭彙，結構必須與法條習慣完全對齊。"]
    },
    "it": {
        "role": "你是一位資深全端工程師與資訊科技（IT）技術文件作家。",
        "rules": ["請使用 IT 軟體業界與開發者常用詞彙：API=應用程式介面、Database=資料庫、Server=伺服器、Frontend=前端、Backend=後端。"]
    },
    "finance": {
        "role": "你是一位特許金融分析師（CFA）兼財經新聞主編。",
        "rules": ["請精確使用金融與證券市場術語：Equity=股權、Bond=債券、Derivative=衍生品、Hedge=避險、Dividend=股息。"]
    }
}

def generate_dynamic_prompt(domain, src_name, tgt_name):
    config = DOMAIN_CONFIG.get(domain, DOMAIN_CONFIG["general"])
    role = config.get("role", "")
    rules = config.get("rules", [])
    glossary = config.get("glossary", "")
    examples = config.get("examples", "")
    
    rule_text = ""
    for idx, rule in enumerate(rules, 1):
        rule_text += f"{idx}. {rule}\n"
        
    prompt = (
        f"【角色設定】\n{role}\n\n"
        f"【核心任務】\n"
        f"你唯一的任務是將輸入的「{src_name}」內容精準翻譯成「{tgt_name}」。\n"
        f"【嚴格遵守規則】\n"
        f"{rule_text}"
        f"*. 絕對不要輸出任何自我介紹、前言、解釋、說明或包含前後引號，你只需要直接輸出翻譯後的結果。\n"
    )
    if glossary:
        prompt += f"\n【專有名詞與專業術語對照表】\n{glossary}\n"
    if examples:
        prompt += f"\n{examples}\n"
    return prompt

# ========== 各大引擎基礎翻譯函數 ==========

def translate_google(text, src, tgt):
    try:
        src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
        tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            return "".join(part[0] for part in data[0] if part[0]) or text
    except Exception as e:
        app.logger.warning(f"Google 翻譯異常: {e}")
    return text

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

def translate_gemini(text, src, tgt, api_key, domain="general"):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    system_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000}
    }
    resp = requests.post(url, json=payload, timeout=25)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 錯誤 {resp.status_code}: {resp.text}")
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise Exception("無法解析 Gemini 單句回應")

# ================== Gemini SRT 批次脈絡翻譯 ==================
def translate_gemini_batch(subtitles, src, tgt, api_key, domain="general"):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
    if not subtitles:
        return []
    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    
    base_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)
    batch_instruction = (
        f"{base_instruction}\n"
        "【批次 JSON 翻譯規範】\n"
        "1. 使用者會提供一個 JSON 陣列，包含多個物件，每個物件有 'id' 與 'text'。\n"
        "2. 請必須保留原本的 'id'，並將 'text' 欄位內文字翻譯成目標語言。\n"
        "3. 請嚴格返回一個合法的標準 JSON 陣列，格式與輸入完全相同，如：[{\"id\": \"1\", \"text\": \"翻譯後的文字\"}]。"
    )
    
    input_data = [{"id": str(sub.get("id")), "text": sub.get("text", "")} for sub in subtitles]
    input_json_str = json.dumps(input_data, ensure_ascii=False)
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": input_json_str}]}],
        "system_instruction": {"parts": [{"text": batch_instruction}]},
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}
    }
    
    retries = 3
    for i in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 429:
                app.logger.warning("偵測到 Rate Limit (429)，等待 10 秒後重試...")
                time.sleep(10)
                continue
            if resp.status_code != 200:
                raise Exception(f"Gemini API 錯誤 {resp.status_code}: {resp.text}")
                
            data = resp.json()
            result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            if "```" in result_text:
                app.logger.info("偵測到 Gemini 回傳帶有 Markdown 包裝，啟動正則自動剝離修復...")
                result_text = re.sub(r'^```json\s*', '', result_text, flags=re.IGNORECASE)
                result_text = re.sub(r'^```\s*', '', result_text)
                result_text = re.sub(r'\s*```$', '', result_text)
            
            translated_list = json.loads(result_text)
            return translated_list
            
        except Exception as e:
            app.logger.error(f"Gemini 批次翻譯嘗試第 {i+1} 次失敗: {e}")
            if i == retries - 1:
                app.logger.error("Gemini 批次翻譯失敗，啟用安全降級...")
                raise e
            time.sleep(2)

# ================== 🌟 新增：Flask 路由設定 🌟 ==================

@app.route('/')
def index():
    """ 導向首頁，自動尋找同目錄下的 index.html """
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/translate_srt', methods=['POST'])
def translate_srt_endpoint():
    """ 接收前端上傳的字幕與翻譯設定 """
    try:
        data = request.get_json() or {}
        subtitles = data.get("subtitles", [])
        src = data.get("src", "auto")
        tgt = data.get("tgt", "zh-TW")
        domain = data.get("domain", "general")
        engine = data.get("engine", "gemini")
        
        # 優先使用前端傳過來的 Key，若無則拿後端環境變數
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY

        if not subtitles:
            return jsonify({"error": "沒有收到任何字幕資料"}), 400

        # 1. 如果選擇 Gemini，直接使用優雅的「批次脈絡翻譯」
        if engine == "gemini":
            try:
                translated_data = translate_gemini_batch(subtitles, src, tgt, gemini_key, domain)
                return jsonify({"translated": translated_data})
            except Exception as gemini_err:
                app.logger.error(f"Gemini 批次翻譯全盤失敗，啟動全安全降級機制: {gemini_err}")
                # 萬一 JSON 損壞或全盤錯誤，降級改用逐句 Google 翻譯
                engine = "google"

        # 2. 逐句翻譯模式（適用於 Google, DeepL 或從 Gemini 故障降級過來的請求）
        results = []
        for item in subtitles:
            sub_id = item.get("id")
            text = item.get("text", "").strip()
            
            if not text:
                results.append({"id": sub_id, "text": ""})
                continue
                
            translated_text = text
            if engine == "deepl":
                try:
                    translated_text = translate_deepl(text, src, tgt, deepl_key)
                except Exception:
                    translated_text = translate_google(text, src, tgt) # DeepL 爆了就找 Google 救
            elif engine == "google":
                translated_text = translate_google(text, src, tgt)
            else:
                # 萬一有未定義的 engine 類型
                translated_text = translate_google(text, src, tgt)
                
            results.append({"id": sub_id, "text": translated_text})
            
        return jsonify({"translated": results})

    except Exception as e:
        app.logger.error(f"端點發生未預期錯誤: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 本地測試用，Render 上會自動被 gunicorn 呼叫 app:app
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


