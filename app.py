import streamlit as st
from groq import Groq
import pdfplumber
import io
import json
import re
import time

from gri_reference import ALL_STANDARDS

st.set_page_config(
    page_title="永續報告書 GRI 分析工具",
    page_icon="🌱",
    layout="wide"
)

# ── API Key Input ────────────────────────────────────────

with st.sidebar:
    st.markdown("### 🔑 Groq API Key")
    st.markdown("[免費申請 API Key →](https://console.groq.com/keys)", unsafe_allow_html=False)
    api_key = st.text_input(
        "輸入你的 Groq API Key",
        type="password",
        placeholder="gsk_...",
    )
    if api_key:
        st.success("API Key 已設定")
    else:
        st.warning("請先輸入 API Key 才能開始分析")

# ── PDF Extraction ───────────────────────────────────────

def extract_pdf_text(uploaded_file) -> tuple[str, int]:
    """
    Extract text from key pages only:
    - Reports ≤ 30 pages: all pages
    - Reports > 30 pages: first 10 pages (overview/materiality) +
                          last 12 pages (GRI index/appendices)
    These sections contain the most GRI-relevant content.
    """
    data = uploaded_file.read()
    pages_text = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total = len(pdf.pages)
        if total > 30:
            front   = list(range(min(10, total)))
            back    = list(range(max(0, total - 12), total))
            indices = sorted(set(front + back))
        else:
            indices = list(range(total))

        for i in indices:
            text = pdf.pages[i].extract_text() or ""
            if text.strip():
                pages_text.append(f"[第 {i+1} 頁]\n{text}")

    return "\n\n".join(pages_text), total


# ── Prompt ───────────────────────────────────────────────

SYSTEM_PROMPT = """你是永續報告書分析專家，精通 GRI（Global Reporting Initiative）2021 準則。

分析規則：
- 只使用真實存在的 GRI 官方代碼（GRI 2、GRI 3、GRI 201–207、GRI 301–308、GRI 401–418），不得自創代碼
- 每個 GRI 代碼須附上官方英文名稱
- 揭露品質評估：
  • 完整 = 有量化數據且邊界清楚
  • 部分 = 有提及但缺乏量化或範圍不完整
  • 不足 = 僅文字描述或完全未揭露
- 重大議題需對應 GRI 3-1、3-2、3-3"""

ANALYSIS_PROMPT = """請分析以下永續報告書，輸出結構化 JSON，不得輸出 JSON 以外的任何文字。

{truncated_notice}

JSON 結構如下：
{{
  "report_overview": {{
    "company": "公司名稱",
    "year": "報告年度",
    "framework": "主要使用框架（如 GRI Standards 2021）",
    "assurance": "第三方查證資訊或「無」",
    "reporting_boundary": "報告邊界說明"
  }},
  "material_topics": [
    {{"topic": "重大議題名稱", "gri_3_3": "對應 GRI 3-3 管理方式摘要"}}
  ],
  "chapters": [
    {{
      "chapter_name": "章節名稱",
      "pages": "頁碼範圍",
      "key_points": ["重點一", "重點二", "重點三"],
      "key_metrics": ["指標名稱: 數值 單位（年度）"],
      "gri_standards": [
        {{
          "code": "GRI 305-1",
          "official_name": "Direct (Scope 1) GHG emissions",
          "quality": "完整",
          "evidence": "揭露了具體數據（如 X tCO2e）"
        }}
      ]
    }}
  ],
  "gri_summary": {{
    "fully_disclosed": [
      {{"code": "GRI 302-1", "official_name": "Energy consumption within the organization", "note": "揭露說明"}}
    ],
    "partially_disclosed": [
      {{"code": "GRI 305-3", "official_name": "Other indirect (Scope 3) GHG emissions", "gap": "缺少項目說明"}}
    ],
    "needs_improvement": [
      {{"code": "GRI 303-3", "official_name": "Water withdrawal", "reason": "建議補強原因"}}
    ]
  }},
  "overall_score": "優",
  "overall_assessment": "2–3 句整體評估"
}}

報告書內容：
{text}"""


# ── Groq API Call ────────────────────────────────────────

def analyze_report(text: str, api_key: str) -> dict:
    if not api_key:
        st.error("請在左側欄位輸入 Groq API Key。")
        st.stop()

    # Chinese text ≈ 2 tokens/char; keep well within Groq 20k TPM
    MAX_CHARS = 6_000
    truncated = len(text) > MAX_CHARS
    display_text = text[:MAX_CHARS] if truncated else text
    notice = "[注意：已截取關鍵頁面內容進行分析（概覽頁＋GRI索引頁）]" if truncated else ""

    prompt = ANALYSIS_PROMPT.format(truncated_notice=notice, text=display_text)

    client = Groq(api_key=api_key)

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            break
        except Exception as e:
            err = str(e)
            if ("rate_limit" in err.lower() or "429" in err) and attempt == 0:
                with st.spinner("請求頻率過高，60 秒後自動重試…"):
                    time.sleep(60)
                continue
            if "401" in err or "invalid" in err.lower():
                st.error("⚠️ API Key 無效，請確認後重新輸入。")
            elif "rate_limit" in err.lower() or "429" in err:
                st.error("⚠️ 額度仍然不足，請稍後再試或換一把 API Key。")
            else:
                st.error(f"API 呼叫失敗：{err}")
            st.stop()

    raw = response.choices[0].message.content

    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    match = re.search(r"\{[\s\S]*\}", raw)
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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("公司",    overview.get("company", "—"))
    c2.metric("年度",    overview.get("year", "—"))
    c3.metric("框架",    (overview.get("framework") or "—")[:22])
    c4.metric("整體評分", f"{icon} {score}")

    if assurance := overview.get("assurance"):
        st.caption(f"第三方查證：{assurance}")
    if boundary := overview.get("reporting_boundary"):
        st.caption(f"報告邊界：{boundary}")

    topics = result.get("material_topics") or []
    if topics:
        st.markdown("**重大議題（GRI 3）**")
        for t in topics:
            with st.expander(f"📌 {t.get('topic', '')}"):
                st.markdown(f"**GRI 3-3 管理方式：** {t.get('gri_3_3', '—')}")

    st.divider()

    st.subheader("章節分析")
    for ch in result.get("chapters") or []:
        pages  = f"（第 {ch['pages']} 頁）" if ch.get("pages") else ""
        header = f"**{ch.get('chapter_name', '未知章節')}** {pages}"
        with st.expander(header):
            left, right = st.columns(2)

            with left:
                if pts := ch.get("key_points"):
                    st.markdown("**核心重點**")
                    for p in pts:
                        st.markdown(f"- {p}")
                if metrics := ch.get("key_metrics"):
                    st.markdown("**關鍵數據**")
                    for m in metrics:
                        st.markdown(f"📊 `{m}`")

            with right:
                if gris := ch.get("gri_standards"):
                    st.markdown("**對應 GRI 標準（官方）**")
                    for g in gris:
                        q    = g.get("quality", "")
                        ico  = QUALITY_ICON.get(q, "⚪")
                        code = g.get("code", "")
                        name = g.get("official_name", "")
                        ev   = g.get("evidence", "")
                        st.markdown(f"{ico} **{code}** *{name}*")
                        if ev:
                            st.caption(f"　　↳ {ev}")

    st.divider()

    st.subheader("GRI 符合度摘要")
    s = result.get("gri_summary") or {}
    gc1, gc2, gc3 = st.columns(3)

    with gc1:
        st.markdown("**🟢 完整揭露**")
        for item in s.get("fully_disclosed") or []:
            st.markdown(f"- **{item.get('code','')}** {item.get('official_name','')}")
            if note := item.get("note"):
                st.caption(f"  {note}")

    with gc2:
        st.markdown("**🟡 部分揭露**")
        for item in s.get("partially_disclosed") or []:
            st.markdown(f"- **{item.get('code','')}** {item.get('official_name','')}")
            if gap := item.get("gap"):
                st.caption(f"  缺少：{gap}")

    with gc3:
        st.markdown("**🔴 建議補強**")
        for item in s.get("needs_improvement") or []:
            st.markdown(f"- **{item.get('code','')}** {item.get('official_name','')}")
            if reason := item.get("reason"):
                st.caption(f"  原因：{reason}")

    if note := result.get("overall_assessment"):
        st.info(f"💡 {note}")

    with st.expander("📖 GRI 2021 官方標準代碼對照表"):
        for standard_name, disclosures in ALL_STANDARDS.items():
            st.markdown(f"**{standard_name}**")
            rows = [f"`{k}` {v}" for k, v in disclosures.items()]
            st.markdown("　　".join(rows))
            st.markdown("")


# ── Main UI ──────────────────────────────────────────────

st.title("🌱 永續報告書 GRI 分析工具")
st.markdown(
    "上傳一份或多份永續報告書 PDF，依據 **GRI Standards 2021 官方標準**自動分析章節重點與 GRI 符合度。"
)

uploaded_files = st.file_uploader(
    "上傳永續報告書 PDF（可多選）",
    type="pdf",
    accept_multiple_files=True,
)

if uploaded_files:
    names = [f.name for f in uploaded_files]
    st.markdown(f"已選擇 **{len(names)}** 份：{', '.join(names)}")

    if st.button("🔍 開始分析", type="primary", use_container_width=True, disabled=not api_key):
        if "results" not in st.session_state:
            st.session_state.results = {}

        bar = st.progress(0, text="準備中…")

        for i, f in enumerate(uploaded_files):
            if f.name not in st.session_state.results:
                bar.progress(i / len(uploaded_files), text=f"分析中：{f.name}")
                f.seek(0)
                text, page_count = extract_pdf_text(f)
                result = analyze_report(text, api_key)
                result["_page_count"] = page_count
                st.session_state.results[f.name] = result

        bar.progress(1.0, text="✅ 分析完成！")

    if st.session_state.get("results"):
        ready = [f.name for f in uploaded_files if f.name in st.session_state.results]

        if len(ready) == 1:
            st.divider()
            pc = st.session_state.results[ready[0]].get("_page_count", "?")
            st.caption(f"共 {pc} 頁")
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

    with st.expander("📚 支援的 GRI 標準範圍（GRI 2021 官方）"):
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            "**GRI 200 經濟**\n"
            "- GRI 201 Economic Performance\n"
            "- GRI 204 Procurement Practices\n"
            "- GRI 205 Anti-corruption\n"
            "- GRI 207 Tax"
        )
        c2.markdown(
            "**GRI 300 環境**\n"
            "- GRI 302 Energy\n"
            "- GRI 303 Water and Effluents\n"
            "- GRI 305 Emissions\n"
            "- GRI 306 Waste\n"
            "- GRI 308 Supplier Environmental Assessment"
        )
        c3.markdown(
            "**GRI 400 社會**\n"
            "- GRI 401 Employment\n"
            "- GRI 403 Occupational Health and Safety\n"
            "- GRI 404 Training and Education\n"
            "- GRI 408 Child Labor\n"
            "- GRI 414 Supplier Social Assessment"
        )
