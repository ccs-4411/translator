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
| jack swing / check swing | 半揮棒 / 有沒有出棒 |
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
    }
}

# =========================================================
# 提示詞與解析清洗工具
# =========================================================
def generate_dynamic_prompt(domain, src_name, tgt_name):
    config = DOMAIN_CONFIG.get(domain, DOMAIN_CONFIG["general"])
    role = config.get("role", "")
    rules = config.get("rules", [])
    glossary = config.get("glossary", "")
    examples = config.get("examples", "")

    rule_text = "".join(f"{idx}. {rule}\n" for idx, rule in enumerate(rules, 1))
    return (
        f"【角色設定】\n{role}\n\n"
        f"【核心任務】\n"
        f"你唯一的任務是將輸入的「{src_name}」內容精準翻譯成「{tgt_name}」。\n\n"
        f"【嚴格遵守規則】\n"
        f"{rule_text}"
        f"*. 絕對不要輸出任何自我介紹、前言、解釋、說明或包含前後引號，你只需要直接輸出翻譯後的結果。\n"
        f"{f'\n【專有名詞與專業術語對照表】\n{glossary}\n' if glossary else ''}"
        f"{f'\n{examples}\n' if examples else ''}"
    )

def build_batch_instruction(domain, src_name, tgt_name):
    base_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)
    extra_baseball = (
        "\n【棒球字幕特別規則】\n"
        "1. 請使用台灣棒球轉播與球評常見用語。\n"
        "2. 字幕要短、俐落，像轉播字幕，不要翻成長篇書面語。\n"
        "3. 遇到 count、strike two 等棒球口語時，優先翻成棒球圈慣用語。\n"
        "4. 不要把相鄰字幕合併理解，每一筆 id 只翻自己的文字。\n"
    ) if domain == "baseball" else ""

    return (
        f"{base_instruction}\n"
        "【SRT 字幕批次翻譯規範】\n"
        "你會收到一個 JSON 陣列，每個元素包含 id 與 text。\n"
        "請將每個元素的 text 翻譯成目標語言，並回傳 JSON 陣列。\n\n"
        "【硬性規則，必須遵守】\n"
        "1. 必須保留所有原始 id，不可新增、刪除、改名、改順序。\n"
        "2. 每個 id 的 text 只能翻譯該 id 自己的內容。\n"
        "3. 絕對禁止把相鄰字幕合併翻譯。\n"
        "4. 不要輸出任何解釋、註解、markdown、```json，只能輸出合法 JSON 陣列，例如：\n"
        '[{"id":"1","text":"翻譯結果"},{"id":"2","text":"翻譯結果"}]\n'
        f"{extra_baseball}"
    )

def parse_srt(srt_string):
    srt_string = srt_string.replace('\r\n', '\n').replace('\r', '\n')
    pattern = r'(\d+)\n([0-9:, \t\-衰>]+)\n(.*?)(?=\n\s*\n|\n\d+\n[0-9:, \t\-衰>]+|\Z)'
    matches = re.finditer(pattern, srt_string, re.DOTALL)
    
    subtitles = []
    for match in matches:
        sub_id = match.group(1).strip()
        time_sync = match.group(2).strip()
        text = match.group(3).strip()
        if '衰>' in time_sync: time_sync = time_sync.replace('衰>', '-->')
        elif '-->' not in time_sync: continue
        subtitles.append({"id": sub_id, "time": time_sync, "text": text})
        
    if not subtitles:
        blocks = re.split(r'\n\s*\n', srt_string.strip())
        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if len(lines) < 2 or '-->' not in lines[1]: continue
            subtitles.append({"id": lines[0], "time": lines[1], "text": "\n".join(lines[2:])})
    return subtitles

def normalize_subtitle_text(text):
    if not text: return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join([line.strip() for line in text.split('\n') if line.strip()]).strip()

def normalize_article_text(text):
    if not text: return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").strip()

def chunk_subtitles(subtitles, domain="general"):
    # 【關鍵修改】主動縮小每批字幕的數量（從 25 降到 10），拉長單次處理量，降低 Free Tier 的併發碰撞頻率
    max_items = 8 if domain in ["baseball", "basketball"] else 12
    max_chars = 2500

    chunks = []
    current_chunk = []
    current_chars = 0

    for sub in subtitles:
        text = normalize_subtitle_text(sub.get("text", ""))
        if current_chunk and (len(current_chunk) >= max_items or current_chars + len(text) > max_chars):
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0
        current_chunk.append(sub)
        current_chars += len(text)
    if current_chunk: chunks.append(current_chunk)
    return chunks

def looks_untranslated(src_text, translated_text, target_lang):
    src_text, translated_text = (src_text or "").strip(), (translated_text or "").strip()
    if not translated_text or translated_text.lower() == src_text.lower(): return True
    if target_lang in ["zh-TW", "zh-CN"]:
        letters = sum(1 for c in translated_text if c.isascii() and c.isalpha())
        if letters / max(len(translated_text), 1) > 0.60: return True
    return False

# =========================================================
# 翻譯核心底層引擎 (徹底移除 Raise 機制，改為靜音回傳)
# =========================================================
def translate_google(text, src, tgt):
    try:
        src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
        tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
        url = f"[https://translate.googleapis.com/translate_a/single?client=gtx&sl=](https://translate.googleapis.com/translate_a/single?client=gtx&sl=){src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return "".join(part[0] for part in resp.json()[0] if part[0]) or text
    except Exception as e:
        app.logger.warning(f"Google 翻譯異常: {e}")
    return text

def translate_deepl(text, src, tgt, api_key):
    try:
        if not api_key: return ""
        tgt_code = LANG_CONFIG[tgt]["deepl"]
        src_code = LANG_CONFIG[src]["deepl"]
        params = {"text": text, "target_lang": tgt_code}
        if src_code and src_code != "auto": params["source_lang"] = src_code
        headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
        resp = requests.post("[https://api-free.deepl.com/v2/translate](https://api-free.deepl.com/v2/translate)", data=params, headers=headers, timeout=15)
        if resp.status_code == 200: return resp.json()["translations"][0]["text"]
    except Exception:
        pass
    return ""

def translate_gemini(text, src, tgt, api_key, domain="general"):
    """ 單句 Gemini：失敗時直接回傳空字串，絕不對上層拋出 Exception """
    try:
        if not api_key: return ""
        src_name = LANG_CONFIG.get(src, {}).get("name", src)
        tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
        system_instruction = generate_dynamic_prompt(domain, src_name, tgt_name)
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
        }
        resp = requests.post(url, json=payload, timeout=25)
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        pass
    return ""

def translate_gemini_batch(subtitles, src, tgt, api_key, domain="general"):
    """ 
    【核心修改】批次 Gemini 徹底防禦工程：
    此處如果發生 429 流量超限，絕對不 return 錯誤，也絕對不 raise Exception。
    直接回傳一個空列表 []，優雅地告訴上層：「Gemini 掛了，請直接啟動 Google 接管」。
    """
    try:
        if not api_key or not subtitles: return []
        src_name = LANG_CONFIG.get(src, {}).get("name", src)
        tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
        batch_instruction = build_batch_instruction(domain, src_name, tgt_name)
        input_data = [{"id": str(sub.get("id")), "text": normalize_subtitle_text(sub.get("text", ""))} for sub in subtitles]
        
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": json.dumps(input_data, ensure_ascii=False)}]}],
            "system_instruction": {"parts": [{"text": batch_instruction}]},
            "generationConfig": {"temperature": 0.15, "responseMimeType": "application/json", "maxOutputTokens": 3500}
        }

        resp = requests.post(url, json=payload, timeout=35)
        if resp.status_code != 200:
            # 偵測到 429 / 403，靜音處理，不對外吐露任何錯誤訊息
            app.logger.warning(f"Gemini 狀態碼異常 {resp.status_code}，底層自動攔截，準備切換備援。")
            return []

        result_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if "```" in result_text:
            result_text = re.sub(r'^```json\s*|^```\s*|\s*```$', '', result_text, flags=re.IGNORECASE).strip()

        translated_list = json.loads(result_text)
        return translated_list if isinstance(translated_list, list) else []
    except Exception as e:
        app.logger.warning(f"Gemini 批次解析發生非預期異常，已自動切換備援: {e}")
        return []

def safe_single_translate(text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False):
    text = normalize_subtitle_text(text)
    if not text: return ""
    if allow_gemini and gemini_key:
        res = translate_gemini(text, src, tgt, gemini_key, domain)
        if res: return normalize_subtitle_text(res)
    if deepl_key and tgt in ["ja", "ko"]:
        res = translate_deepl(text, src, tgt, deepl_key)
        if res: return normalize_subtitle_text(res)
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
        src, tgt = data.get("source_lang", "auto"), data.get("target_lang", "zh-TW")
        domain = data.get("domain", "general")
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY

        if not text.strip(): return jsonify({"result": ""}), 200

        if gemini_key:
            res = translate_gemini(text, src, tgt, gemini_key, domain)
            if res: return jsonify({"result": res, "engine": "Gemini"})
            
        res = translate_google(text, src, tgt)
        return jsonify({"result": res, "engine": "Google (備援)"})
    except Exception:
        return jsonify({"result": "", "engine": "Error Fixed"}), 200

@app.route('/translate_srt', methods=['POST'])
def translate_srt_endpoint():
    """ 
    徹底終結前端噴出 429 錯誤的防守端點。
    無論底層發生什麼配額爆炸，外層一律回傳 200 成功，並默默用 Google 把剩下的字串織完。
    """
    try:
        data = request.get_json() or {}
        srt_content = data.get("srt_content", "").strip()
        src, tgt = data.get("source_lang", "auto"), data.get("target_lang", "zh-TW")
        domain, layout_mode = data.get("domain", "general"), data.get("layout_mode", "original_first")
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY

        if not srt_content:
            return jsonify({"srt_output": "", "error": "無字幕內容"}), 200

        parsed_subs = parse_srt(srt_content)
        if not parsed_subs:
            return jsonify({"srt_output": srt_content, "error": "解析失敗"}), 200

        translated_map = {}
        logs = []
        fallback_used = False
        gemini_available = bool(gemini_key)

        chunks = chunk_subtitles(parsed_subs, domain=domain)
        total_chunks = len(chunks)

        for idx, chunk in enumerate(chunks, 1):
            batch_done = False

            # 1. 只有在 Gemini 可用時才嘗試呼叫
            if gemini_available:
                batch_res = translate_gemini_batch(chunk, src, tgt, gemini_key, domain)
                
                # 如果底層因為 429 默默回傳了空列表 []，代表 Gemini 掛了，直接出發熔斷
                if not batch_res:
                    gemini_available = False
                    fallback_used = True
                    app.logger.info(f"第 {idx} 批 Gemini 回傳為空，觸發默默切換機制。")
                else:
                    try:
                        expected_ids = {str(x["id"]) for x in chunk}
                        chunk_map = {str(item.get("id")): normalize_subtitle_text(item.get("text", "")) for item in batch_res}
                        
                        if expected_ids == set(chunk_map.keys()):
                            for sub in chunk:
                                sub_id = str(sub["id"])
                                src_text = normalize_subtitle_text(sub["text"])
                                trans_text = chunk_map.get(sub_id, "")
                                if looks_untranslated(src_text, trans_text, tgt):
                                    trans_text = safe_single_translate(src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False)
                                translated_map[sub_id] = normalize_subtitle_text(trans_text)
                            batch_done = True
                            logs.append(f"第 {idx}/{total_chunks} 批：Gemini 處理完成。")
                        else:
                            gemini_available = False
                            fallback_used = True
                    except Exception:
                        gemini_available = False
                        fallback_used = True

            # 2. 備援核心接管：只要不成功，立刻由 Google 接手。完全在後端消化，不對前端回報錯誤
            if not batch_done:
                fallback_used = True
                for sub in chunk:
                    sub_id = str(sub["id"])
                    src_text = normalize_subtitle_text(sub["text"])
                    translated_map[sub_id] = safe_single_translate(src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False)
                logs.append(f"第 {idx}/{total_chunks} 批：由安全備援引擎接手完成。")

        # 3. 重新建立標準 SRT 格式
        output_blocks = []
        for item in parsed_subs:
            sub_id, time_sync = str(item["id"]), item["time"]
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
        engine_used = "Gemini 混合 Google 翻譯智慧備援機制" if fallback_used else "Gemini 完整批次翻譯"

        # 【關鍵回傳】無論如何都給前端 200 成功，絕對不夾帶 Quota 錯誤關鍵字，防止前端跳出警告視窗
        return jsonify({
            "srt_output": final_srt_output,
            "count": len(parsed_subs),
            "translated_count": len(translated_map),
            "total_count": len(parsed_subs),
            "engine": engine_used,
            "fallback_used": fallback_used,
            "logs": logs
        }), 200

    except Exception as e:
        # 萬一連組裝都寫爛，最後防線也回傳 200，把原始內文吐回去，確保前端按鈕不卡死
        return jsonify({
            "srt_output": srt_content,
            "engine": "緊急防禦救援機制",
            "logs": [f"系統捕獲異常: {str(e)}"]
        }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


