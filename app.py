<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>智慧翻譯 | 多引擎 + 雙語SRT</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #f5f7fb; font-family: system-ui, sans-serif; padding: 0; }
        .container { max-width: 800px; margin: 0 auto; padding: 16px; }

        .accordion { background: white; border-radius: 24px; margin-bottom: 20px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
        .accordion-header { background: #fff; padding: 14px 18px; cursor: pointer; font-weight: 600; display: flex; justify-content: space-between; border-bottom: 1px solid #eef2f6; }
        .accordion-header .arrow { transition: transform 0.3s; }
        .accordion.hide .accordion-content { display: none; }
        .accordion.hide .arrow { transform: rotate(-90deg); }
        .accordion-content { padding: 16px; }

        .api-group { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
        .input-flex { flex: 2; min-width: 160px; }
        button { background: #eef2f8; border: 1px solid #dce3ec; padding: 8px 14px; border-radius: 40px; cursor: pointer; font-size: 0.8rem; }
        button.primary { background: #2c3e66; color: white; border: none; }
        button.danger { background: #fff0f0; border-color: #ffcdcd; color: #b13e3e; }
        input, select { width: 100%; padding: 10px 14px; border-radius: 30px; border: 1px solid #cfdde6; font-size: 0.9rem; }
        label { font-size: 0.7rem; font-weight: 600; color: #4b5565; margin-bottom: 4px; display: block; }

        .translate-card { background: white; border-radius: 24px; padding: 16px; margin-bottom: 16px; }
        .box-header { display: flex; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
        .lang-row { display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
        .lang-item { flex: 1; min-width: 120px; }
        textarea { width: 100%; padding: 14px; border-radius: 20px; border: 1px solid #e2e8f0; font-size: 1rem; min-height: 160px; resize: vertical; }

        .translate-bar { text-align: center; margin: 8px 0 16px; }
        .btn-translate { background: #2c3e66; color: white; padding: 12px 24px; border-radius: 60px; width: 80%; max-width: 260px; border: none; font-weight: 600; font-size: 1rem; }

        .message { background: #eef2fa; border-radius: 20px; padding: 10px 16px; margin-top: 12px; border-left: 4px solid #5f7f9e; font-size: 0.8rem; }
        .error { background: #ffefef; border-left-color: #d9534f; color: #a94442; }
        .warning { background: #fff3cd; border-left-color: #ffc107; color: #856404; }

        .srt-mode { margin: 12px 0; display: flex; gap: 24px; align-items: center; flex-wrap: wrap; font-size: 1rem; }
        .srt-mode label { display: inline-flex; align-items: center; gap: 8px; font-size: 1rem; font-weight: normal; cursor: pointer; color: #1f2937; }
        .srt-mode input[type="checkbox"] { width: 18px; height: 18px; margin: 0; cursor: pointer; }
        .srt-hint { font-size: 0.75rem; color: #e67e22; margin-left: 8px; }

        footer { text-align: center; font-size: 0.7rem; color: #6c7a8e; margin-top: 24px; }

        @media (max-width: 640px) {
            .container { padding: 12px; }
            .lang-row { flex-direction: column; gap: 8px; }
            .srt-mode { gap: 16px; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="accordion" id="keyAccordion">
        <div class="accordion-header" id="accordionToggle">
            <span>🔑 API 金鑰設定 (Gemini + DeepL 選用)</span>
            <span class="arrow">▼</span>
        </div>
        <div class="accordion-content">
            <div style="margin-bottom: 20px;">
                <label>✨ Google Gemini API Key</label>
                <div class="api-group">
                    <div class="input-flex"><input type="password" id="geminiKey" placeholder="輸入 Gemini Key"></div>
                    <button id="saveGemini" class="primary">💾 儲存</button>
                    <button id="clearGemini" class="danger">🗑️ 清除</button>
                </div>
                <div id="geminiStatus" style="font-size:0.7rem; margin-top:6px;"></div>
            </div>
            <hr>
            <div>
                <label>🌐 DeepL API Key (選填)</label>
                <div class="api-group">
                    <div class="input-flex"><input type="password" id="deeplKey" placeholder="輸入 DeepL Key"></div>
                    <button id="saveDeepl" class="primary">💾 儲存</button>
                    <button id="clearDeepl" class="danger">🗑️ 清除</button>
                </div>
                <div id="deeplStatus" style="font-size:0.7rem; margin-top:6px;"></div>
            </div>
            <div class="message">💡 策略：Gemini優先 → 英法德用Google；日韓有DeepL Key優先</div>
        </div>
    </div>

    <div class="translate-card">
        <div class="lang-row">
            <div class="lang-item">
                <label>📖 來源語言</label>
                <select id="sourceLang"></select>
            </div>
            <div class="lang-item">
                <label>🏷️ 行業領域（影響 Gemini）</label>
                <select id="domainSelect">
                    <option value="general">一般翻譯</option>
                    <option value="travel">旅遊</option>
                    <option value="baseball">棒球</option>
                    <option value="basketball">籃球</option>
                    <option value="gaming">遊戲</option>
                    <option value="news">新聞</option>
                    <option value="entertainment">娛樂</option>
                    <option value="mechanical">機械</option>
                    <option value="semiconductor">半導體</option>
                    <option value="medical">醫療</option>
                    <option value="legal">法律</option>
                    <option value="it">資訊科技</option>
                    <option value="finance">金融</option>
                </select>
            </div>
            <div class="lang-item">
                <label>🎯 目標語言</label>
                <select id="targetLang"></select>
            </div>
        </div>

        <div class="box-header">
            <div style="flex:1"></div>
            <div>
                <button id="pasteBtn">📋 貼上</button>
                <button id="fileBtn">📂 開啟檔案</button>
                <button id="clearSource" class="danger">清除</button>
                <input type="file" id="fileInput" accept=".txt,.md,.csv,.srt" style="display: none;">
            </div>
        </div>
        <textarea id="sourceText" placeholder="輸入要翻譯的文字... (若要處理SRT字幕，請勾選下方模式)"></textarea>
        <div style="font-size:0.7rem; color:#2c3e66; margin-top:6px;">💡 提示：若貼上 SRT 字幕內容（含時間軸），請務必勾選下方「SRT 字幕模式」，否則會當作普通文字翻譯。</div>
    </div>

    <div class="translate-card">
        <div class="box-header">
            <div style="flex:1"></div>
            <div><button id="copyBtn">📋 複製</button><button id="clearTarget" class="danger">清除</button></div>
        </div>
        <textarea id="targetText" placeholder="翻譯結果..." readonly></textarea>
    </div>

    <div class="srt-mode">
        <label>
            <input type="checkbox" id="srtModeCheckbox"> 🎞️ SRT 字幕模式 (保留時間軸)
        </label>
        <label>
            <input type="checkbox" id="originalFirstCheckbox" checked> 原字幕在上
        </label>
        <span class="srt-hint">⚠️ 若要產生雙語 SRT 字幕，請務必勾選「SRT 字幕模式」</span>
    </div>

    <div class="translate-bar">
        <button id="translateBtn" class="btn-translate">✨ 開始翻譯</button>
    </div>

    <div id="messageBox" class="message">✅ 就緒 | 引擎: Gemini 優先 → 英法德:Google / 日韓:DeepL(有Key) → Google降級</div>
    <footer>金鑰僅儲存於瀏覽器，並加密傳送至後端（後端不記錄）<br>SRT 翻譯結果會直接顯示在上方「翻譯結果」區，可直接複製貼上使用。</footer>
</div>

<script>
    const LANG_LIST = [
        { code: "zh-TW", name: "繁體中文" }, { code: "zh-CN", name: "簡體中文" },
        { code: "en", name: "英文" }, { code: "ja", name: "日文" },
        { code: "ko", name: "韓文" }, { code: "fr", name: "法文" }, { code: "de", name: "德文" }, { code: "es", name: "西班牙文" }
    ];
    function populateSelects() {
        const source = document.getElementById('sourceLang');
        const target = document.getElementById('targetLang');
        const opts = LANG_LIST.map(l => `<option value="${l.code}">${l.name}</option>`).join('');
        source.innerHTML = opts; target.innerHTML = opts;
        source.value = "zh-TW"; target.value = "en";
    }
    populateSelects();

    // 金鑰管理
    const geminiInput = document.getElementById('geminiKey');
    const deeplInput = document.getElementById('deeplKey');
    const geminiStatus = document.getElementById('geminiStatus');
    const deeplStatus = document.getElementById('deeplStatus');
    function loadKeys() {
        const g = localStorage.getItem('gemini_key');
        const d = localStorage.getItem('deepl_key');
        if (g) { geminiInput.value = g; geminiStatus.innerText = '✅ 已載入'; }
        if (d) { deeplInput.value = d; deeplStatus.innerText = '✅ 已載入'; }
    }
    document.getElementById('saveGemini').onclick = () => { let k = geminiInput.value.trim(); if(k){ localStorage.setItem('gemini_key', k); geminiStatus.innerText = '✅ 已儲存'; } };
    document.getElementById('clearGemini').onclick = () => { localStorage.removeItem('gemini_key'); geminiInput.value = ''; geminiStatus.innerText = '🧹 已清除'; };
    document.getElementById('saveDeepl').onclick = () => { let k = deeplInput.value.trim(); if(k){ localStorage.setItem('deepl_key', k); deeplStatus.innerText = '✅ 已儲存'; } };
    document.getElementById('clearDeepl').onclick = () => { localStorage.removeItem('deepl_key'); deeplInput.value = ''; deeplStatus.innerText = '🧹 已清除'; };
    loadKeys();

    const sourceText = document.getElementById('sourceText');
    const targetText = document.getElementById('targetText');
    const sourceLang = document.getElementById('sourceLang');
    const targetLang = document.getElementById('targetLang');
    const msgBox = document.getElementById('messageBox');
    const domainSelect = document.getElementById('domainSelect');
    function setMsg(msg, type='info') {
        msgBox.innerHTML = msg;
        msgBox.classList.remove('error', 'warning');
        if (type === 'error') msgBox.classList.add('error');
        else if (type === 'warning') msgBox.classList.add('warning');
    }

    // 輔助功能
    document.getElementById('pasteBtn').onclick = async () => { try { const t = await navigator.clipboard.readText(); sourceText.value = t; setMsg('已貼上'); } catch(e){ setMsg('無法讀取剪貼簿', 'error'); } };
    document.getElementById('clearSource').onclick = () => { sourceText.value = ''; setMsg('原文已清除'); };
    document.getElementById('copyBtn').onclick = async () => { if(targetText.value) await navigator.clipboard.writeText(targetText.value); setMsg('已複製'); };
    document.getElementById('clearTarget').onclick = () => { targetText.value = ''; setMsg('結果已清除'); };
    const fileBtn = document.getElementById('fileBtn');
    const fileInput = document.getElementById('fileInput');
    fileBtn.onclick = () => fileInput.click();
    fileInput.onchange = (event) => {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > 5 * 1024 * 1024) { setMsg('檔案過大，請選擇小於 5MB 的文字檔', 'error'); fileInput.value = ''; return; }
        const reader = new FileReader();
        reader.onload = (e) => { sourceText.value = e.target.result; setMsg(`✅ 已開啟檔案：${file.name} (${(file.size/1024).toFixed(1)} KB)`); };
        reader.onerror = () => setMsg('❌ 檔案讀取失敗', 'error');
        reader.readAsText(file, 'UTF-8');
    };
    const accordion = document.getElementById('keyAccordion');
    document.getElementById('accordionToggle').onclick = () => accordion.classList.toggle('hide');

    // SRT 模式
    const srtModeCheckbox = document.getElementById('srtModeCheckbox');
    const originalFirstCheckbox = document.getElementById('originalFirstCheckbox');
    const translateBtn = document.getElementById('translateBtn');

    translateBtn.onclick = async () => {
        const text = sourceText.value.trim();
        if (!text) { setMsg('請輸入內容', 'error'); return; }
        const tgt = targetLang.value;
        const domain = domainSelect.value;

        if (!srtModeCheckbox.checked) {
            // 普通文字翻譯
            const src = sourceLang.value;
            if (src === tgt) { targetText.value = text; setMsg('來源與目標相同'); return; }
            targetText.value = '⏳ 翻譯中...';
            setMsg('🔄 呼叫後端...');
            try {
                const gemini_key = localStorage.getItem('gemini_key') || '';
                const deepl_key = localStorage.getItem('deepl_key') || '';
                const res = await fetch('/translate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        text, 
                        source_lang: src, 
                        target_lang: tgt, 
                        gemini_key, 
                        deepl_key,
                        domain: domain
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || '失敗');
                targetText.value = data.result;
                setMsg(`✅ 完成 (引擎: ${data.engine})`);
            } catch (err) {
                targetText.value = '❌ 錯誤';
                setMsg(`錯誤: ${err.message}`, 'error');
            }
            return;
        }

        // SRT 模式
        targetText.value = '⏳ 正在解析並翻譯 SRT 字幕...';
        setMsg('🔄 SRT 字幕翻譯中，請稍候...');
        try {
            const gemini_key = localStorage.getItem('gemini_key') || '';
            const deepl_key = localStorage.getItem('deepl_key') || '';
            const srcLang = sourceLang.value;
            const response = await fetch('/translate_srt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    srt_content: text,
                    gemini_key: gemini_key,
                    deepl_key: deepl_key,
                    target_lang: tgt,
                    original_first: originalFirstCheckbox.checked,
                    source_lang: srcLang,
                    domain: domain
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            if (!data.srt_output || typeof data.srt_output !== 'string') {
                throw new Error('後端回傳資料缺少 srt_output 欄位');
            }
            targetText.value = data.srt_output;
            setMsg(`✅ SRT 翻譯完成！共 ${data.count} 條字幕。引擎: ${data.engine}。您可以直接複製上方的字幕內容。`);
        } catch (err) {
            console.error('SRT 翻譯失敗:', err);
            targetText.value = '❌ SRT 翻譯失敗';
            setMsg(`❌ SRT 翻譯失敗: ${err.message}`, 'error');
        }
    };
</script>
</body>
</html>
