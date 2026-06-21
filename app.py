import os
import re
import requests
import logging
import traceback
import time
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# =========================================================
# 基本設定與日誌
# =========================================================
base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=base_dir, static_url_path='')
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# =========================================================
# 語系對照表
# =========================================================
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

DOMAIN_CONFIG = {
    "general": {
        "role": "你是一位精準、專業的母語級翻譯專家。",
        "rules": ["保持語氣自然流暢，符合目標語言的日常表達與文本習慣。"]
    },
    "baseball": {
        "role": "你是一位專業的體育記者與棒球轉播翻譯員。你精通棒球各項戰術、紀錄與術語。",
        "rules": [
            "必須嚴格遵守下方的【術語對照表】，不得使用非棒球圈的通用詞彙。",
            "語氣要帶有體育賽事的動態感與現場感。",
            "字幕應像棒球轉播字幕，簡潔、自然、符合台灣棒球圈常見用語。"
        ],
        "glossary": """
| 英文術語 | 翻譯規範（繁體中文） |
| :--- | :--- |
| pitch | 投球 / 投出的球 |
| strike / strike two | 好球 / 兩好球 |
| ball / ball one | 壞球 / 第一球壞球 |
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
| backed him up | 逼退 / 以球路壓迫 / 把他逼到不好出棒的位置（依語境） |
| got him swinging | 讓他揮空 / 揮空三振（依語境） |
| jack swing / check swing | 半揮棒 / 有有沒有出棒 |
| punches the ticket | 三振出局 / 送回休息區（依語境） |
""",
        "examples": """
【雙語對齊範例】
- "backed him up with strike two" ➔ "用第二顆好球逼退他" / "用兩好球把它壓住"
- "put him away with the sinker" ➔ "用伸卡球解決他"
- "full payoff got him swinging" ➔ "滿球數對決讓他揮空" / "滿球數讓他揮空三振"
- "did he go?" ➔ "有出棒嗎？"
"""
    },
    "basketball": {
        "role": "你是一位專業的籃球評述員與 NBA 賽事專職翻譯。",
        "rules": [
            "嚴格遵守以下籃球核心術語：Rebound=籃板、Assist=助攻、Turnover=失誤、Fast Break=快攻、Paint=禁區、Steal=抄截、Block=阻攻、Air Ball=籃外空心。",
            "字幕應像球評口語轉播字幕，簡潔自然。"
        ]
    },
    "gaming": {
        "role": "你是一位資長的遊戲在地化（Localization）專家，精通各大 3A 遊戲、RPG 與電競術語。",
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
        "rules": [
            "請使用精確的機械專業工程術語：CNC=CNC加工、Bearing=軸承、Torque=扭矩、Tolerance=公差、Fixture=治具、Gear=齒輪、Shaft=軸。"
        ]
    },
    "semiconductor": {
        "role": "你是一位在晶圓代工大廠工作多年的資深製程工程師與半導體產業編譯。",
        "rules": [
            "保持業界高度專業口吻。",
            "當業界習慣直接使用英文簡稱或專有名詞時，請直接保留（例如 Fab、Wafer、Tape-out、Die），不需強行死譯。"
        ],
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

# =========================================================
# 提示詞與清洗工具
# =========================================================
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
        f"你唯一的任務是將輸入的「{src_name}」內容精準翻譯成「{tgt_name}」。\n\n"
        f"【嚴格遵守規則】\n"
        f"{rule_text}"
        f"*. 絕對不要輸出任何自我介紹、前言、解釋、說明或包含前後引號，你只需要直接輸出翻譯後的結果。\n"
    )

    if glossary:
        prompt += f"\n【專有名詞與專業術語對照表】\n{glossary}\n"
    if examples:
        prompt += f"\n{examples}\n"

    return prompt


def build_batch_instruction(domain, src_name, tgt_name):
    base_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)

    extra_baseball = ""
    if domain == "baseball":
        extra_baseball = (
            "\n【棒球字幕特別規則】\n"
            "1. 請使用台灣棒球轉播與球評常見用語。\n"
            "2. 字幕要短、俐落，像轉播字幕，不要翻成長篇書面語。\n"
            "3. 遇到 count、strike two、full count、payoff、got him swinging、ball one、did he go、check swing 等棒球口語時，優先翻成棒球圈慣用語。\n"
            "4. 不要把相鄰字幕合併理解後重寫成完整段落，每一筆 id 只翻自己的文字。\n"
        )

    instruction = (
        f"{base_instruction}\n"
        "【SRT 字幕批次翻譯規範】\n"
        "你會收到一個 JSON 陣列，每個元素包含 id 與 text。\n"
        "請將每個元素的 text 翻譯成目標語言，並回傳 JSON 陣列。\n\n"
        "【硬性規則，必須遵守】\n"
        "1. 必須保留所有原始 id，不可新增、刪除、改名、改順序。\n"
        "2. 每個 id 的 text 只能翻譯該 id 自己的內容，不可引用前後其他字幕的內容。\n"
        "3. 絕對禁止把相鄰字幕合併翻譯。\n"
        "4. 絕對禁止把某一筆字幕拆到前後其他 id。\n"
        "5. 即使某句本身語意不完整，也只能翻譯該句本身，不可自行與前後句合併補全。\n"
        "6. 請盡量保持字幕簡潔，不要擅自擴寫。\n"
        "7. 不要輸出任何解釋、註解、markdown、```json，只能輸出合法 JSON。\n"
        "8. 輸出格式必須是合法 JSON 陣列，例如：\n"
        '[{"id":"1","text":"翻譯結果"},{"id":"2","text":"翻譯結果"}]\n'
        f"{extra_baseball}"
    )
    return instruction


def parse_srt(srt_string):
    srt_string = srt_string.replace('\r\n', '\n').replace('\r', '\n')
    pattern = r'(\d+)\n([0-9:, \t\-衰>]+)\n(.*?)(?=\n\s*\n|\n\d+\n[0-9:, \t\-衰>]+|\Z)'
    matches = re.finditer(pattern, srt_string, re.DOTALL)
    
    subtitles = []
    for match in matches:
        sub_id = match.group(1).strip()
        time_sync = match.group(2).strip()
        text = match.group(3).strip()
        
        if '衰>' in time_sync:
            time_sync = time_sync.replace('衰>', '-->')
        elif '-->' not in time_sync:
            continue

        subtitles.append({"id": sub_id, "time": time_sync, "text": text})
        
    if not subtitles:
        blocks = re.split(r'\n\s*\n', srt_string.strip())
        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if len(lines) < 2:
                continue
            sub_id = lines[0]
            if '-->' not in lines[1]:
                continue
            time_sync = lines[1]
            text = "\n".join(lines[2:])
            subtitles.append({"id": sub_id, "time": time_sync, "text": text})
            
    return subtitles


def normalize_subtitle_text(text):
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return "\n".join(lines).strip()


def normalize_article_text(text):
    if not text:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").strip()


def chunk_subtitles(subtitles, domain="general"):
    if domain in ["baseball", "basketball"]:
        max_items = 15  # 降級每批數量，避免過度頻繁觸發 TPM 限制
        max_chars = 3500
    else:
        max_items = 25
        max_chars = 5500

    chunks = []
    current_chunk = []
    current_chars = 0

    for sub in subtitles:
        text = normalize_subtitle_text(sub.get("text", ""))
        text_len = len(text)

        if current_chunk and (
            len(current_chunk) >= max_items or
            current_chars + text_len > max_chars
        ):
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        current_chunk.append(sub)
        current_chars += text_len

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def looks_untranslated(src_text, translated_text, target_lang):
    src_text = (src_text or "").strip()
    translated_text = (translated_text or "").strip()

    if not translated_text:
        return True
    if translated_text.lower() == src_text.lower():
        return True

    if target_lang in ["zh-TW", "zh-CN"]:
        letters = sum(1 for c in translated_text if c.isascii() and c.isalpha())
        ratio = letters / max(len(translated_text), 1)
        if ratio > 0.60:
            return True

    return False


# =========================================================
# 翻譯核心底層引擎
# =========================================================
def translate_google(text, src, tgt):
    try:
        src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
        tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
        url = f"[https://translate.googleapis.com/translate_a/single?client=gtx&sl=](https://translate.googleapis.com/translate_a/single?client=gtx&sl=){src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            return "".join(part[0] for part in data[0] if part[0]) or text
    except Exception as e:
        app.logger.warning(f"Google 翻譯底層異常: {e}")
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
    resp = requests.post("[https://api-free.deepl.com/v2/translate](https://api-free.deepl.com/v2/translate)", data=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"]


def translate_gemini(text, src, tgt, api_key, domain="general"):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")

    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    system_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)

    url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2500}
    }

    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 錯誤 {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise Exception("無法解析 Gemini 回應內容")


def translate_gemini_batch(subtitles, src, tgt, api_key, domain="general"):
    """ Gemini 結構化 JSON 字幕批次翻譯 API（內部不再進行死纏爛打的重試，把配額限制直接拋出去觸發熔斷） """
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
    if not subtitles:
        return []

    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    batch_instruction = build_batch_instruction(domain, src_name, tgt_name)

    input_data = [{"id": str(sub.get("id")), "text": normalize_subtitle_text(sub.get("text", ""))} for sub in subtitles]
    input_json_str = json.dumps(input_data, ensure_ascii=False)
    
    url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": input_json_str}]}],
        "system_instruction": {"parts": [{"text": batch_instruction}]},
        "generationConfig": {
            "temperature": 0.15,
            "responseMimeType": "application/json",
            "maxOutputTokens": 4000
        }
    }

    # 這裡直接發送請求，如果 429 或其他錯誤直接 raise，交給上層立刻切換 Google，不再浪費時間等重試
    resp = requests.post(url, json=payload, timeout=40)
    if resp.status_code != 200:
        raise Exception(f"Gemini 服務回應錯誤碼 {resp.status_code}: {resp.text}")

    data = resp.json()
    result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

    if "```" in result_text:
        result_text = re.sub(r'^```json\s*', '', result_text, flags=re.IGNORECASE)
        result_text = re.sub(r'^```\s*', '', result_text)
        result_text = re.sub(r'\s*```$', '', result_text).strip()

    translated_list = json.loads(result_text)
    if not isinstance(translated_list, list):
        raise Exception("Gemini 回傳格式非標準 JSON 陣列")

    return translated_list


def safe_single_translate(text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False):
    text = normalize_subtitle_text(text)
    if not text:
        return ""

    if allow_gemini and gemini_key:
        try:
            return normalize_subtitle_text(translate_gemini(text, src, tgt, gemini_key, domain))
        except Exception:
            pass

    if deepl_key and tgt in ["ja", "ko"]:
        try:
            return normalize_subtitle_text(translate_deepl(text, src, tgt, deepl_key))
        except Exception:
            pass

    return normalize_subtitle_text(translate_google(text, src, tgt))


# =========================================================
# Flask 路由端點
# =========================================================
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/translate', methods=['POST'])
def translate_text_endpoint():
    try:
        data = request.get_json() or {}
        text = normalize_article_text(data.get("text", ""))
        src = data.get("source_lang", "auto")
        tgt = data.get("target_lang", "zh-TW")
        domain = data.get("domain", "general")
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY

        if not text.strip():
            return jsonify({"error": "沒有輸入任何文字"}), 400

        if gemini_key:
            try:
                result = translate_gemini(text, src, tgt, gemini_key, domain)
                return jsonify({"result": result, "engine": "Gemini"})
            except Exception as e:
                app.logger.warning(f"文章翻譯：Gemini 失敗（可能額度用盡），已自動無縫切換 Google 備援。錯誤: {e}")
                result = translate_google(text, src, tgt)
                return jsonify({"result": result, "engine": "Google (Gemini自動熔斷接管)"})

        if deepl_key and tgt in ["ja", "ko"]:
            try:
                result = translate_deepl(text, src, tgt, deepl_key)
                return jsonify({"result": result, "engine": "DeepL"})
            except Exception:
                pass

        result = translate_google(text, src, tgt)
        return jsonify({"result": result, "engine": "Google"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/translate_srt', methods=['POST'])
def translate_srt_endpoint():
    """ 
    優化後的字幕端點：
    解決 Gemini 報 429 額度用盡時，中途崩潰沒有接手的情況。
    """
    try:
        data = request.get_json() or {}
        srt_content = data.get("srt_content", "").strip()
        src = data.get("source_lang", "auto")
        tgt = data.get("target_lang", "zh-TW")
        domain = data.get("domain", "general")
        layout_mode = data.get("layout_mode", "original_first")

        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY

        if not srt_content:
            return jsonify({"error": "沒有收到任何字幕資料"}), 400

        parsed_subs = parse_srt(srt_content)
        if not parsed_subs:
            return jsonify({"error": "SRT 格式解析失敗，請確認內容符合標準 SRT 規範"}), 400

        translated_map = {}
        logs = []
        fallback_used = False
        gemini_available = bool(gemini_key)

        chunks = chunk_subtitles(parsed_subs, domain=domain)
        total_chunks = len(chunks)

        app.logger.info(f"SRT 共 {len(parsed_subs)} 筆，切成 {total_chunks} 批（domain={domain}）")
        logs.append(f"SRT 共 {len(parsed_subs)} 筆，切成 {total_chunks} 批。")

        for idx, chunk in enumerate(chunks, 1):
            app.logger.info(f"正在處理第 {idx}/{total_chunks} 批，內含 {len(chunk)} 筆字幕")
            
            batch_done = False

            # 1. 如果 Gemini 目前標記為可用，嘗試發送批次
            if gemini_available:
                try:
                    batch_res = translate_gemini_batch(chunk, src, tgt, gemini_key, domain)

                    expected_ids = {str(x["id"]) for x in chunk}
                    returned_ids = set()
                    chunk_map = {}

                    for item in batch_res:
                        item_id = str(item.get("id"))
                        item_text = normalize_subtitle_text(item.get("text", ""))
                        chunk_map[item_id] = item_text
                        returned_ids.add(item_id)

                    if expected_ids != returned_ids:
                        raise Exception("回傳的字幕 ID 數量或不對稱，強制切換備援")

                    # 複查是否有漏翻
                    for sub in chunk:
                        sub_id = str(sub["id"])
                        src_text = normalize_subtitle_text(sub["text"])
                        translated_text = chunk_map.get(sub_id, "")

                        if looks_untranslated(src_text, translated_text, tgt):
                            translated_text = safe_single_translate(
                                src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False
                            )
                        translated_map[sub_id] = normalize_subtitle_text(translated_text)

                    batch_done = True
                    logs.append(f"第 {idx}/{total_chunks} 批：使用 Gemini 翻譯成功。")

                except Exception as e:
                    # 【核心修正】一旦 Gemini 拋出 429 額度耗盡，這裡立刻捕獲它！
                    app.logger.error(f"第 {idx} 批 Gemini 報錯（配額耗盡），立刻在本批次啟動 Google 翻譯接管！錯誤: {e}")
                    logs.append(f"第 {idx}/{total_chunks} 批：Gemini 額度耗盡/受限，已現場強制切換 Google 翻譯接管。")
                    
                    # 標記 Gemini 已經掛了，後續批次直接略過 Gemini 走 Google，不再等待
                    gemini_available = False
                    fallback_used = True

            # 2. 備援接管邏輯：如果 Gemini 一開始就不可用，或者剛剛在上面 try 裡面突然失敗報錯了
            if not batch_done:
                # 這裡就地（In-place）直接幫這一批 chunk 的每一句字幕，調用 Google 翻譯
                for sub in chunk:
                    sub_id = str(sub["id"])
                    src_text = normalize_subtitle_text(sub["text"])
                    
                    # 呼叫 Google/DeepL 單句翻譯，保證這一批字幕不會遺失、不會中斷
                    translated_map[sub_id] = safe_single_translate(
                        src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False
                    )

        # 3) 重新組裝標準 SRT 
        output_blocks = []
        for item in parsed_subs:
            sub_id = str(item["id"])
            time_sync = item["time"]
            orig_text = normalize_subtitle_text(item["text"])
            trans_text = normalize_subtitle_text(translated_map.get(sub_id, ""))

            block_lines = [sub_id, time_sync]
            if layout_mode == "original_first":
                block_lines.append(orig_text)
                if trans_text: block_lines.append(trans_text)
            elif layout_mode == "translated_first":
                if trans_text: block_lines.append(trans_text)
                block_lines.append(orig_text)
            elif layout_mode == "translated_only":
                block_lines.append(trans_text if trans_text else orig_text)
            else:
                block_lines.append(orig_text)
                if trans_text: block_lines.append(trans_text)

            output_blocks.append("\n".join(block_lines))

        final_srt_output = "\n\n".join(output_blocks).strip() + "\n"

        # 4) 包裝狀態回傳（確保 HTTP Status 回傳 200，絕對不丟出 500 錯誤讓前端崩潰）
        if gemini_key:
            engine_used = "Gemini 額度用完，後端已無縫自動切換 Google 翻譯全部續翻完成" if fallback_used else f"Gemini 完整批次字幕翻譯"
        else:
            engine_used = "安全備援引擎（Google）"

        return jsonify({
            "srt_output": final_srt_output,
            "count": len(parsed_subs),
            "translated_count": len(translated_map),
            "total_count": len(parsed_subs),
            "engine": engine_used,
            "fallback_used": fallback_used,
            "logs": logs
        })

    except Exception as e:
        app.logger.error(f"SRT 端點發生未預期嚴重錯誤: {traceback.format_exc()}")
        return jsonify({"error": f"系統發生嚴重錯誤: {str(e)}"}), 500


# =========================================================
# 啟動 Python 服務
# =========================================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


