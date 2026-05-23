import streamlit as st
import anthropic
import pdfplumber
import io
import json
import re

st.set_page_config(
    page_title="永續報告書 GRI 分析工具",
    page_icon="🌱",
    layout="wide"
)

# ── PDF Extraction ───────────────────────────────────────

def extract_pdf_text(uploaded_file) -> tuple[str, int]:
    """Return (full_text, page_count). Text includes page markers."""
    data = uploaded_file.read()
    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[第 {i+1} 頁]\n{text}")
    return "\n\n".join(pages), total


# ── Claude Analysis ──────────────────────────────────────

ANALYSIS_PROMPT = """你是永續報告書分析專家，精通 GRI（Global Reporting Initiative）2021 準則。

請分析以下永續報告書，針對每個主要章節產出：
1. 章節名稱與頁碼
2. 3–5 個核心重點
3. 具體 ESG 數據指標（有數字的優先列出）
4. 對應的 GRI 標準代碼（如 GRI 305-1、GRI 403-9）
5. 揭露品質：完整 / 部分 / 不足

最後整體摘要 GRI 符合度。

{truncated_notice}

請以 JSON 格式回覆，嚴格遵守以下結構（不要輸出 JSON 以外的文字）：
{{
  "report_overview": {{
    "company": "公司名稱",
    "year": "報告年度",
    "framework": "主要使用框架",
    "assurance": "第三方查證資訊或「無」"
  }},
  "material_topics": ["重大議題1", "重大議題2"],
  "chapters": [
    {{
      "chapter_name": "章節名稱",
      "pages": "頁碼範圍（如可識別，否則空字串）",
      "key_points": ["重點一", "重點二"],
      "key_metrics": ["指標: 數值 單位"],
      "gri_standards": [
        {{
          "code": "GRI 302-1",
          "topic": "組織能源消耗",
          "quality": "完整"
        }}
      ]
    }}
  ],
  "gri_summary": {{
    "fully_disclosed": ["GRI 302-1 能源消耗"],
    "partially_disclosed": ["GRI 305-3 Scope 3（分類不完整）"],
    "needs_improvement": ["GRI 303-3 水資源回收（缺乏量化數據）"]
  }},
  "overall_score": "優",
  "overall_assessment": "2–3 句整體評估文字"
}}

報告書內容：
{text}"""


def analyze_report(text: str) -> dict:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        st.error("未設定 ANTHROPIC_API_KEY，請在 Streamlit secrets 中加入此金鑰。")
        st.stop()

    MAX_CHARS = 120_000
    truncated = len(text) > MAX_CHARS
    display_text = text[:MAX_CHARS] if truncated else text
    notice = "[注意：文件過長，以下為前段截取內容]" if truncated else ""

    prompt = ANALYSIS_PROMPT.format(truncated_notice=notice, text=display_text)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text

    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {"parse_error": True, "raw_text": raw}


# ── Rendering ────────────────────────────────────────────

QUALITY_ICON = {"完整": "🟢", "部分": "🟡", "不足": "🔴"}
SCORE_ICON   = {"優": "🟢", "良": "🔵", "中": "🟡", "待改善": "🔴"}


def render_result(result: dict):
    if result.get("parse_error"):
        st.warning("無法解析結構化結果，顯示原始輸出：")
        st.markdown(result.get("raw_text", "（無內容）"))
        return

    overview = result.get("report_overview", {})
    score    = result.get("overall_score", "—")
    icon     = SCORE_ICON.get(score, "⚪")

    # Header metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("公司",   overview.get("company", "—"))
    c2.metric("年度",   overview.get("year", "—"))
    c3.metric("框架",   (overview.get("framework") or "—")[:22])
    c4.metric("整體評分", f"{icon} {score}")

    if assurance := overview.get("assurance"):
        st.caption(f"第三方查證：{assurance}")

    # Material topics
    topics = result.get("material_topics") or []
    if topics:
        st.markdown("**重大議題：** " + "　".join(f"`{t}`" for t in topics))

    st.divider()

    # Chapter analysis
    st.subheader("章節分析")
    for ch in result.get("chapters") or []:
        pages   = f"（第 {ch['pages']} 頁）" if ch.get("pages") else ""
        header  = f"**{ch.get('chapter_name', '未知章節')}** {pages}"
        with st.expander(header):
            left, right = st.columns(2)

            with left:
                pts = ch.get("key_points") or []
                if pts:
                    st.markdown("**核心重點**")
                    for p in pts:
                        st.markdown(f"- {p}")

                metrics = ch.get("key_metrics") or []
                if metrics:
                    st.markdown("**關鍵數據**")
                    for m in metrics:
                        st.markdown(f"📊 `{m}`")

            with right:
                gris = ch.get("gri_standards") or []
                if gris:
                    st.markdown("**對應 GRI 標準**")
                    for g in gris:
                        q    = g.get("quality", "")
                        ico  = QUALITY_ICON.get(q, "⚪")
                        st.markdown(f"{ico} **{g.get('code','')}** {g.get('topic','')}")

    st.divider()

    # GRI summary
    st.subheader("GRI 符合度摘要")
    s = result.get("gri_summary") or {}
    gc1, gc2, gc3 = st.columns(3)

    with gc1:
        st.markdown("**🟢 完整揭露**")
        for item in s.get("fully_disclosed") or []:
            st.markdown(f"- {item}")

    with gc2:
        st.markdown("**🟡 部分揭露**")
        for item in s.get("partially_disclosed") or []:
            st.markdown(f"- {item}")

    with gc3:
        st.markdown("**🔴 建議補強**")
        for item in s.get("needs_improvement") or []:
            st.markdown(f"- {item}")

    if note := result.get("overall_assessment"):
        st.info(f"💡 {note}")


# ── Main UI ──────────────────────────────────────────────

st.title("🌱 永續報告書 GRI 分析工具")
st.markdown("上傳一份或多份永續報告書 PDF，自動分析各章節重點並對應 GRI 標準。")

uploaded_files = st.file_uploader(
    "上傳永續報告書 PDF（可多選）",
    type="pdf",
    accept_multiple_files=True,
)

if uploaded_files:
    names = [f.name for f in uploaded_files]
    st.markdown(f"已選擇 **{len(names)}** 份：{', '.join(names)}")

    if st.button("🔍 開始分析", type="primary", use_container_width=True):
        if "results" not in st.session_state:
            st.session_state.results = {}

        bar = st.progress(0, text="準備中…")

        for i, f in enumerate(uploaded_files):
            if f.name not in st.session_state.results:
                bar.progress(i / len(uploaded_files), text=f"分析中：{f.name}")
                f.seek(0)
                text, pages = extract_pdf_text(f)
                result = analyze_report(text)
                result["_page_count"] = pages
                st.session_state.results[f.name] = result

        bar.progress(1.0, text="✅ 分析完成！")

    # Render results
    if st.session_state.get("results"):
        ready = [f.name for f in uploaded_files if f.name in st.session_state.results]

        if len(ready) == 1:
            st.divider()
            render_result(st.session_state.results[ready[0]])
        elif len(ready) > 1:
            tabs = st.tabs([f"📄 {n}" for n in ready])
            for tab, name in zip(tabs, ready):
                with tab:
                    pc = st.session_state.results[name].get("_page_count", "?")
                    st.caption(f"共 {pc} 頁")
                    render_result(st.session_state.results[name])

else:
    st.info("👆 請上傳 PDF 檔案開始分析")

    with st.expander("📚 支援的 GRI 標準範圍"):
        c1, c2, c3 = st.columns(3)
        c1.markdown("**GRI 200 經濟**\n- GRI 201 經濟績效\n- GRI 204 採購實務\n- GRI 205 反腐敗")
        c2.markdown("**GRI 300 環境**\n- GRI 302 能源\n- GRI 303 水資源\n- GRI 305 排放\n- GRI 306 廢棄物")
        c3.markdown("**GRI 400 社會**\n- GRI 401 雇用\n- GRI 403 職安\n- GRI 404 訓練\n- GRI 408/409 人權")
