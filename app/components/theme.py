"""
🎨 Theme Component - Shared CSS & UI utilities for all pages
"""

import streamlit as st
from pathlib import Path

# Load theme CSS
_theme_css = None


def _load_css():
    global _theme_css
    if _theme_css is None:
        css_path = Path(__file__).parent.parent / ".streamlit" / "theme.css"
        if css_path.exists():
            _theme_css = css_path.read_text(encoding="utf-8")
        else:
            _theme_css = ""
    return _theme_css


def inject_theme():
    """Inject the shared CSS theme into the current page."""
    css = _load_css()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_step_indicator(current_step: int, steps: list[str]):
    """
    Render a professional step indicator / progress stepper.
    
    Args:
        current_step: 0-indexed current step
        steps: List of step labels
    """
    html = '<div class="step-container">'
    
    for i, label in enumerate(steps):
        if i < current_step:
            circle_class = "completed"
            icon = "✓"
        elif i == current_step:
            circle_class = "active"
            icon = str(i + 1)
        else:
            circle_class = "pending"
            icon = str(i + 1)
        
        label_class = "step-label active" if i == current_step else "step-label"
        
        html += f'''
        <div class="step-item">
            <div class="step-circle {circle_class}">{icon}</div>
            <div class="{label_class}">{label}</div>
        </div>
        '''
        
        if i < len(steps) - 1:
            # Connector line
            if i < current_step:
                connector_color = "var(--success)"
            elif i == current_step:
                connector_color = "var(--primary)"
            else:
                connector_color = "var(--border)"
            html += f'<div style="flex:0.5;height:2px;background:{connector_color};margin:0 4px;margin-bottom:1.2rem;border-radius:1px;"></div>'
    
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_metric_card(label: str, value: str, delta: str = None, icon: str = ""):
    """Render a styled metric card."""
    delta_html = ""
    if delta:
        delta_color = "var(--success)" if not delta.startswith("-") else "var(--danger)"
        delta_html = f'<div style="font-size:0.8rem;color:{delta_color};margin-top:4px;">{delta}</div>'
    
    icon_html = f'<span style="margin-right:6px;">{icon}</span>' if icon else ""
    
    st.markdown(f'''
    <div style="
        background: linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        padding: 1rem 1.2rem;
        transition: all 0.2s ease;
    ">
        <div style="font-size:0.8rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.05em;font-weight:500;margin-bottom:4px;">
            {icon_html}{label}
        </div>
        <div style="font-size:1.8rem;font-weight:800;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;line-height:1.2;">
            {value}
        </div>
        {delta_html}
    </div>
    ''', unsafe_allow_html=True)


def render_nav_footer(prev_page: str = None, prev_label: str = "← Quay lại",
                      next_page: str = None, next_label: str = "Tiếp theo →"):
    """Render navigation footer with prev/next buttons."""
    st.write("")
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if prev_page:
            st.page_link(prev_page, label=prev_label, use_container_width=True)
    
    with col2:
        if next_page:
            st.page_link(next_page, label=next_label, use_container_width=True)


def render_project_status_bar(topic_id: str, has_story: bool, has_narration: bool,
                               has_audio: bool, has_images: bool, has_video: bool):
    """Render a project status bar showing progress through the pipeline."""
    steps = [
        ("📋 Topic", True),
        ("📖 Story", has_story),
        ("🎙️ Narration", has_narration),
        ("🔊 Audio", has_audio),
        ("🖼️ Ảnh", has_images),
        ("🎬 Video", has_video),
    ]
    
    html = '<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;">'
    
    for label, completed in steps:
        if completed:
            bg = "linear-gradient(135deg, var(--success), #27AE60)"
            shadow = "0 2px 8px rgba(46,204,113,0.3)"
        else:
            bg = "rgba(255,255,255,0.06)"
            shadow = "none"
        
        html += f'''
        <div style="
            background: {bg};
            color: {'white' if completed else 'var(--text-muted)'};
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            box-shadow: {shadow};
            white-space: nowrap;
        ">{'✓ ' if completed else ''}{label}</div>
        '''
        
        if label != "🎬 Video":
            html += '<div style="color:var(--text-muted);font-size:0.7rem;">→</div>'
    
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)
