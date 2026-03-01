"""Tests for session display component formatting."""

from claude_monitor.ui.session_display import SessionDisplayComponent


def test_format_active_session_screen_shows_combined_indicator() -> None:
    """Multiple active sessions should render combined-session marker lines."""
    component = SessionDisplayComponent()
    lines = component.format_active_session_screen(
        plan="pro",
        timezone="UTC",
        tokens_used=100,
        token_limit=1000,
        usage_percentage=10.0,
        tokens_left=900,
        elapsed_session_minutes=10,
        total_session_minutes=300,
        burn_rate=5.0,
        session_cost=0.25,
        per_model_stats={},
        sent_messages=3,
        entries=[],
        predicted_end_str="12:00",
        reset_time_str="15:00",
        current_time_str="10:00",
        active_sessions_count=2,
        active_providers=["claude", "codex"],
    )

    rendered = "\n".join(lines)
    assert "Active Sessions" in rendered
    assert "2 combined" in rendered
    assert "claude, codex" in rendered
