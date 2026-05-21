"""Regression tests for _apply_profile_override HERMES_HOME guard (issue #22502).

When HERMES_HOME is set to the hermes root (e.g. systemd hardcodes
HERMES_HOME=/root/.hermes), _apply_profile_override must still read
active_profile and update HERMES_HOME to the profile directory.

When HERMES_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, hermes_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["HERMES_HOME"] after the call,
    or None if unset.
    """
    hermes_root = tmp_path / ".hermes"
    hermes_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (hermes_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (hermes_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if hermes_home is not None:
        monkeypatch.setenv("HERMES_HOME", hermes_home)
    else:
        monkeypatch.delenv("HERMES_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["hermes", "gateway", "start"])

    from hermes_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("HERMES_HOME")


class TestApplyProfileOverrideHermesHomeGuard:
    """Regression guard for issue #22502.

    Verifies that HERMES_HOME pointing to the hermes root does NOT suppress
    the active_profile check, while HERMES_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_hermes_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """HERMES_HOME=/root/.hermes + active_profile=coder must redirect
        HERMES_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets HERMES_HOME to the hermes root
        and the user switches to a profile via `hermes profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hermes_home=str(hermes_root),
            active_profile="coder",
        )

        assert result is not None, "HERMES_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected HERMES_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected HERMES_HOME to end with 'coder', got: {result!r}"
        )

    def test_hermes_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """HERMES_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with HERMES_HOME already set to a specific profile must stay in that
        profile.
        """
        hermes_root = tmp_path / ".hermes"
        profile_dir = hermes_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (hermes_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERMES_HOME") == str(profile_dir), (
            "HERMES_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_hermes_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: HERMES_HOME unset + active_profile=coder must set
        HERMES_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hermes_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_hermes_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect HERMES_HOME."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])
        (hermes_root / "active_profile").write_text("default")

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERMES_HOME") is None


class TestApplyProfileOverrideHermeProfileEnv:
    """Tests for HERMES_PROFILE env var support (issue #29948).

    When a user starts gateways via
      HERMES_PROFILE=alice hermes gateway --replace &
    the override must resolve to the profile's own PID file, not collide
    on the default ~/.hermes/gateway.pid.
    """

    def test_hermes_profile_sets_correct_path(self, tmp_path, monkeypatch):
        """HERMES_PROFILE=bob + no HERMES_HOME → resolves to profiles/bob."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)
        profile_dir = hermes_root / "profiles" / "bob"
        profile_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_PROFILE", "bob")
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("HERMES_HOME")
        assert result is not None, "HERMES_HOME must be set from HERMES_PROFILE"
        assert "profiles" in result
        assert result.endswith("bob"), f"Expected 'bob' suffix, got: {result!r}"

    def test_profile_flag_takes_precedence_over_hermes_profile(self, tmp_path, monkeypatch):
        """--profile=-p takes precedence over HERMES_PROFILE env var."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)
        (hermes_root / "profiles" / "alice").mkdir(parents=True, exist_ok=True)
        (hermes_root / "profiles" / "bob").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_PROFILE", "alice")
        monkeypatch.setattr(sys, "argv", ["hermes", "-p", "bob"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("HERMES_HOME")
        assert result is not None
        assert result.endswith("bob"), (
            f"--profile flag should win over HERMES_PROFILE; expected 'bob', got: {result!r}"
        )

    def test_hermes_home_profile_dir_bypasses_hermes_profile(self, tmp_path, monkeypatch):
        """HERMES_HOME already pointing to profile dir → no override."""
        hermes_root = tmp_path / ".hermes"
        profile_dir = hermes_root / "profiles" / "alice"
        profile_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(profile_dir))
        monkeypatch.setenv("HERMES_PROFILE", "bob")
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERMES_HOME") == str(profile_dir), (
            "HERMES_HOME pointing to a profile dir must bypass HERMES_PROFILE"
        )

    def test_invalid_hermes_profile_falls_through_to_active_profile(self, tmp_path, monkeypatch):
        """Invalid HERMES_PROFILE name falls through to active_profile.
        
        Note: if active_profile is also set but its profile dir doesn't exist,
        the existing code calls sys.exit(1) — this test creates the coder dir
        so active_profile resolves cleanly after HERMES_PROFILE is skipped.
        """
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)
        (hermes_root / "active_profile").write_text("coder")
        (hermes_root / "profiles" / "coder").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_PROFILE", "invalid:name!")
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("HERMES_HOME")
        assert result is not None, "Should fall through to active_profile"
        assert "coder" in result, f"Fell through but wrong profile: {result!r}"

    def test_missing_hermes_profile_falls_through_to_active_profile(self, tmp_path, monkeypatch):
        """HERMES_PROFILE set but directory missing → falls through to active_profile.
        
        If active_profile is "default" (no redirect needed), HERMES_HOME stays unset.
        If active_profile names a non-existent profile dir, existing code sys.exit(1).
        This test uses active_profile=default to verify the fall-through path works.
        """
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)
        (hermes_root / "active_profile").write_text("default")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_PROFILE", "nonexistent-profile")
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        # Falls through to active_profile which is "default" → HERMES_HOME stays unset
        assert os.environ.get("HERMES_HOME") is None, (
            "Missing profile should fall through to active_profile=default → no redirect"
        )

    def test_no_env_vars_unset_default_profile(self, tmp_path, monkeypatch):
        """No HERMES_PROFILE, no HERMES_HOME, no active_profile → HERMES_HOME stays unset."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERMES_HOME") is None

    def test_hermes_profile_with_active_profile(self, tmp_path, monkeypatch):
        """HERMES_PROFILE overrides active_profile when no flag is given."""
        hermes_root = tmp_path / ".hermes"
        hermes_root.mkdir(parents=True, exist_ok=True)
        (hermes_root / "active_profile").write_text("coder")
        (hermes_root / "profiles" / "bob").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_PROFILE", "bob")
        monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "start"])

        from hermes_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("HERMES_HOME")
        assert result is not None
        assert result.endswith("bob"), (
            f"HERMES_PROFILE should override active_profile; expected 'bob', got: {result!r}"
        )
