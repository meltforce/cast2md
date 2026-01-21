"""Tests for settings configuration and source detection."""

import pytest
from cast2md.config.settings import (
    get_setting_source,
    NODE_SPECIFIC_SETTINGS,
    _DEFAULTS,
)


class TestSettingSource:
    """Tests for get_setting_source function."""

    def test_setting_from_database(self):
        """Settings with DB override should show 'database'."""
        source = get_setting_source("whisper_model", "large-v3-turbo", "large-v3-turbo")
        assert source == "database"

    def test_setting_from_env_file(self):
        """Settings from env file (differs from default, no DB) should show 'env_file'."""
        # whisper_model default is 'base'
        source = get_setting_source("whisper_model", "large-v3-turbo", None)
        assert source == "env_file"

    def test_setting_at_default(self):
        """Settings at default value should show 'default'."""
        source = get_setting_source("whisper_model", "base", None)
        assert source == "default"

    def test_stuck_threshold_from_database(self):
        """Regular settings with DB override should show 'database'."""
        source = get_setting_source("stuck_threshold_hours", 4, "4")
        assert source == "database"

    def test_stuck_threshold_from_env_file(self):
        """Regular settings from env file should show 'env_file'."""
        # stuck_threshold_hours default is 2
        source = get_setting_source("stuck_threshold_hours", 5, None)
        assert source == "env_file"

    def test_stuck_threshold_at_default(self):
        """Regular settings at default should show 'default'."""
        source = get_setting_source("stuck_threshold_hours", 2, None)
        assert source == "default"


class TestNodeSpecificSettings:
    """Tests for NODE_SPECIFIC_SETTINGS constant.

    NODE_SPECIFIC_SETTINGS is empty for the server - all settings can be
    configured via the UI. Remote nodes have their own Settings class.
    """

    def test_node_specific_settings_empty_for_server(self):
        """Server has no node-specific settings - all configurable via UI."""
        assert len(NODE_SPECIFIC_SETTINGS) == 0

    def test_whisper_settings_configurable_via_ui(self):
        """Whisper settings should be configurable via UI (not node-specific)."""
        assert "whisper_model" not in NODE_SPECIFIC_SETTINGS
        assert "whisper_device" not in NODE_SPECIFIC_SETTINGS
        assert "whisper_compute_type" not in NODE_SPECIFIC_SETTINGS
        assert "whisper_backend" not in NODE_SPECIFIC_SETTINGS


class TestDefaults:
    """Tests for _DEFAULTS dictionary."""

    def test_whisper_defaults_exist(self):
        """Whisper settings should have defaults defined."""
        assert "whisper_model" in _DEFAULTS
        assert "whisper_device" in _DEFAULTS
        assert "whisper_compute_type" in _DEFAULTS
        assert "whisper_backend" in _DEFAULTS

    def test_whisper_default_values(self):
        """Whisper defaults should match Settings class defaults."""
        assert _DEFAULTS["whisper_model"] == "base"
        assert _DEFAULTS["whisper_device"] == "auto"
        assert _DEFAULTS["whisper_compute_type"] == "int8"
        assert _DEFAULTS["whisper_backend"] == "auto"
