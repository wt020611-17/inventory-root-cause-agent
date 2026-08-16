"""Streamlit 页面可启动并暴露 Phase 4.2 核心控件。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _app() -> AppTest:
    app_path = Path(__file__).parents[3] / "app" / "ui" / "streamlit_app.py"
    return AppTest.from_file(str(app_path), default_timeout=20).run()


def test_streamlit_page_starts_with_synthetic_label_and_controls() -> None:
    app = _app()

    assert not app.exception
    assert app.title[0].value == "呆滞料智能体"
    assert [item.label for item in app.selectbox] == ["物料", "仓库"]
    assert app.date_input[0].label == "分析日期"
    assert {button.label for button in app.button} >= {"运行根因分析", "追溯证据路径"}
    assert app.chat_input[0].placeholder.startswith("例如：")


def test_streamlit_normal_scenario_renders_metrics() -> None:
    app = _app()
    app.selectbox[0].select("MAT-SYN-NORMAL")
    app.button[0].click().run()

    assert not app.exception
    assert "分析完成" in app.success[0].value
    assert len(app.metric) == 4


def test_streamlit_multi_cause_and_no_llm_degradation_render() -> None:
    app = _app()
    app.button[0].click().run()

    assert not app.exception
    assert "超量采购" in str(app.dataframe[0].value)
    assert "生产延期" in str(app.dataframe[0].value)
    assert any("确定性结果" in item.value for item in app.info)
